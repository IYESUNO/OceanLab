from __future__ import annotations

import queue
import logging
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Any, Callable

from ..domain.models import AcquisitionRequest, AcquisitionState, ConnectedDevice, SpectrumContext
from ..ports.spectrometer import SpectrometerInterrupted, SpectrometerPort
from .messages import AcquisitionCommand, ApplicationEvent


logger = logging.getLogger("OceanLab")


class CoalescingEventQueue:
    def __init__(self) -> None:
        self._items: deque[ApplicationEvent] = deque()
        self._lock = threading.Lock()

    def put(self, item: ApplicationEvent | tuple[str, dict[str, Any]]) -> None:
        event = item if isinstance(item, ApplicationEvent) else ApplicationEvent(item[0], item[1])
        with self._lock:
            if (event.kind == "spectrum" and self._items and self._items[-1].kind == "spectrum"
                    and event.payload.get("continuous", True) and self._items[-1].payload.get("continuous", True)
                    and event.payload.get("request_id") == self._items[-1].payload.get("request_id")):
                self._items[-1] = event
            else:
                self._items.append(event)

    def get_nowait(self) -> ApplicationEvent:
        with self._lock:
            if not self._items:
                raise queue.Empty
            return self._items.popleft()

    def empty(self) -> bool:
        with self._lock:
            return not self._items

    def qsize(self) -> int:
        with self._lock:
            return len(self._items)


class AcquisitionService(threading.Thread):
    """Serializes every hardware call and exposes state transitions as events."""

    def __init__(
        self,
        spectrometer_factory: Callable[[], SpectrometerPort],
        event_queue: CoalescingEventQueue,
    ) -> None:
        super().__init__(name="SpectrometerAcquisition", daemon=True)
        self._factory = spectrometer_factory
        self.events = event_queue
        self.commands: queue.Queue[AcquisitionCommand] = queue.Queue()
        self.stop_event = threading.Event()
        self.spectrometer: SpectrometerPort | None = None
        self.connected: ConnectedDevice | None = None
        self.context: SpectrumContext | None = None
        self.request: AcquisitionRequest | None = None
        self.state = AcquisitionState.DISCONNECTED
        self.running = False
        self.interval_s = 0.08
        self.connection_check_interval_s = 2.0
        self.active_request_id: str | None = None
        self._interrupted = threading.Event()
        self._pause_requested = threading.Event()

    def send(self, command: str, **payload: Any) -> None:
        if command == "stop":
            self._pause_requested.set()
            pause = getattr(self.spectrometer, "pause_pending", None)
            if callable(pause):
                pause()
        if command in ("disconnect", "shutdown"):
            self._interrupted.set()
            cancel = getattr(self.spectrometer, "cancel_pending", None)
            if callable(cancel):
                cancel()
        logger.info("Acquisition command queued: %s request=%s", command, payload.get("request_id"))
        self.commands.put(AcquisitionCommand(command, payload))

    def emit(self, kind: str, **payload: Any) -> None:
        if kind in ("spectrum", "error", "configuration_applied", "settings_applied", "acquisition_state"):
            payload.setdefault("request_id", self.active_request_id)
        self.events.put(ApplicationEvent(kind, payload))

    def transition(self, state: AcquisitionState) -> None:
        self.state = state
        logger.info("Acquisition state: %s request=%s", state.name, self.active_request_id)
        self.emit("service_state", state=state)

    def run(self) -> None:
        try:
            self.spectrometer = self._factory()
            bind_interrupt = getattr(self.spectrometer, "bind_interrupt_event", None)
            if callable(bind_interrupt):
                bind_interrupt(self._interrupted)
            initializer = getattr(self.spectrometer, "initialize", None)
            version = tuple(initializer()) if callable(initializer) else (0, 0, 0)
            self.emit("sdk_ready", version=version)
        except Exception as exc:
            self.transition(AcquisitionState.ERROR)
            self.emit("fatal", message=f"SDK 加载失败：{self.describe_error(exc)}")
            return

        next_acquisition = 0.0
        next_connection_check = time.monotonic() + self.connection_check_interval_s
        while not self.stop_event.is_set():
            timeout = 0.05
            if self.running:
                timeout = max(0.0, min(0.05, next_acquisition - time.monotonic()))
            try:
                command = self.commands.get(timeout=timeout)
                self._handle(command)
            except queue.Empty:
                pass
            except Exception as exc:
                cancelled = isinstance(exc, SpectrometerInterrupted)
                logger.exception("Acquisition command failed: request=%s", self.active_request_id)
                if self.connected is not None or self.state in (AcquisitionState.CONNECTING, AcquisitionState.CONFIGURING):
                    try:
                        self._disconnect()
                    except Exception:
                        logger.exception("Device release after command failure failed")
                self.emit("error", message=self.describe_error(exc), cancelled=cancelled)

            if self.running and time.monotonic() >= next_acquisition:
                started = time.monotonic()
                self._acquire_frame()
                next_acquisition = time.monotonic() + max(0.001, self.interval_s - (time.monotonic() - started))
            elif not self.running and self.connected is not None and time.monotonic() >= next_connection_check:
                self._check_connection()
                next_connection_check = time.monotonic() + self.connection_check_interval_s
        self._cleanup()

    @staticmethod
    def describe_error(exc: Exception) -> str:
        if hasattr(exc, "get_error_details"):
            code, message = exc.get_error_details()
            return f"OceanDirect 错误 {code}：{message}"
        return str(exc)

    def _handle(self, command: AcquisitionCommand) -> None:
        if command.kind in ("connect", "disconnect"):
            self._interrupted.clear()
        handlers = {
            "connect": self._connect,
            "disconnect": self._disconnect,
            "configure_start": lambda: self._configure("start", command.payload),
            "configure_single": lambda: self._configure("single", command.payload),
            "configure": lambda: self._configure("configure", command.payload),
            "start": self._start,
            "stop": self._stop_acquisition,
            "single": self._single,
            "settings": lambda: self._apply_partial(command.payload),
            "corrections": lambda: self._apply_partial(command.payload),
            "device_options": lambda: self._apply_partial(command.payload),
            "shutdown": self._shutdown,
        }
        handler = handlers.get(command.kind)
        if handler is None:
            raise RuntimeError(f"未知命令：{command.kind}")
        handler()

    def _connect(self) -> None:
        if self.spectrometer is None or self.connected is not None:
            return
        self.transition(AcquisitionState.CONNECTING)
        self.emit("status", message="正在搜索 USB 光谱仪…")
        self.connected = self.spectrometer.connect()
        self.request = self.connected.settings
        info = self.connected.info
        settings = asdict(self.request)
        settings.update({
            "electric_dark_supported": info.capabilities.electric_dark,
            "nonlinearity_supported": info.capabilities.nonlinearity,
            "trigger_supported": info.capabilities.trigger,
            "light_source_supported": info.capabilities.light_source,
        })
        self.transition(AcquisitionState.IDLE)
        self.emit("connected", info=info, settings=settings, wavelengths=list(self.connected.wavelengths))

    def _disconnect(self) -> None:
        self.running = False
        disconnect_error: Exception | None = None
        try:
            if self.spectrometer is not None:
                self.spectrometer.disconnect()
        except Exception as exc:
            disconnect_error = exc
        finally:
            self.connected = None
            self.context = None
            self.transition(AcquisitionState.DISCONNECTED)
            self.emit("disconnected")
        if disconnect_error is not None:
            raise disconnect_error

    def _request_from_payload(self, payload: dict[str, Any]) -> AcquisitionRequest:
        base = asdict(self.request) if self.request is not None else asdict(AcquisitionRequest(10_000, 1, 0, 80))
        for name in base:
            if name in payload:
                base[name] = payload[name]
        return AcquisitionRequest(**base)

    def _configure(self, action: str, payload: dict[str, Any]) -> None:
        if self.spectrometer is None or self.connected is None:
            raise RuntimeError("请先连接仪器。")
        request = self._request_from_payload(payload)
        self.active_request_id = payload.get("request_id")
        if self._interrupted.is_set():
            raise SpectrometerInterrupted("采集请求已取消。")
        if self._pause_requested.is_set():
            return
        self.running = False
        self.transition(AcquisitionState.CONFIGURING)
        context = self.spectrometer.apply_configuration(request)
        if self._interrupted.is_set():
            raise SpectrometerInterrupted("配置请求已取消，仪器连接将释放。")
        self.request = request
        self.context = context
        self.interval_s = max(0.001, request.interval_ms / 1000.0)
        settings = asdict(request)
        settings.update({
            "integration_us": context.integration_us,
            "averages": context.averages,
            "boxcar": context.boxcar,
            "trigger_mode": context.trigger_mode,
            "electric_dark": context.electric_dark,
            "nonlinearity": context.nonlinearity,
            "light_source": context.light_source,
        })
        self.emit("configuration_applied", settings=settings, context=context, action=action)
        if self._pause_requested.is_set():
            # Keep the successfully verified configuration. The queued stop
            # acknowledges readiness only after this hardware operation ends.
            self.transition(AcquisitionState.IDLE)
            return
        if action == "start":
            self._start()
        elif action == "single":
            self._single()
        else:
            self.transition(AcquisitionState.IDLE)

    def _apply_partial(self, payload: dict[str, Any]) -> None:
        self.active_request_id = payload.get("request_id")
        request = self._request_from_payload(payload)
        if self.spectrometer is None or self.connected is None:
            raise RuntimeError("请先连接仪器。")
        self.transition(AcquisitionState.CONFIGURING)
        context = self.spectrometer.apply_configuration(request)
        self.request = request
        self.context = context
        self.interval_s = max(0.001, request.interval_ms / 1000.0)
        self.emit("settings_applied", settings={
            "integration_us": context.integration_us,
            "averages": context.averages,
            "boxcar": context.boxcar,
            "interval_ms": request.interval_ms,
        })
        self.transition(AcquisitionState.IDLE)

    def _start(self) -> None:
        if self._pause_requested.is_set():
            return
        if self.context is None:
            raise RuntimeError("采集配置尚未由仪器确认，拒绝启动采集。")
        self.running = True
        self.transition(AcquisitionState.ACQUIRING)
        self.emit("acquisition_state", running=True)

    def _stop_acquisition(self) -> None:
        cancelled = self._pause_requested.is_set()
        clear_pause = getattr(self.spectrometer, "clear_pause", None)
        if callable(clear_pause):
            clear_pause()
        self._pause_requested.clear()
        self.running = False
        self.transition(AcquisitionState.IDLE if self.connected else AcquisitionState.DISCONNECTED)
        if self.connected:
            self.emit("acquisition_state", running=False, cancelled=cancelled)

    def _single(self) -> None:
        if self._pause_requested.is_set():
            return
        if self.context is None:
            raise RuntimeError("采集配置尚未由仪器确认。")
        self.transition(AcquisitionState.ACQUIRING)
        self._acquire_frame()
        if self.connected is not None:
            self.transition(AcquisitionState.IDLE)

    def _acquire_frame(self) -> None:
        if self.spectrometer is None or self.context is None:
            return
        try:
            if self._interrupted.is_set():
                raise SpectrometerInterrupted("采集请求已取消。")
            if self._pause_requested.is_set():
                return
            frame = self.spectrometer.acquire(self.context)
            if self._interrupted.is_set():
                raise SpectrometerInterrupted("采集请求已取消，已丢弃本帧数据。")
            if self._pause_requested.is_set():
                return
            if frame is None:
                raise RuntimeError("设备未返回光谱，且没有暂停请求。")
            self.emit("spectrum", spectrum=list(frame.values), timestamp=frame.timestamp, context=frame.context,
                      continuous=self.running)
        except Exception as exc:
            logger.exception("Spectrum acquisition failed: request=%s", self.active_request_id)
            self.running = False
            try:
                self._disconnect()
            except Exception:
                pass
            self.emit("acquisition_state", running=False)
            cancelled = isinstance(exc, SpectrometerInterrupted)
            prefix = "采集已取消，仪器连接已释放" if cancelled else "采集失败，仪器连接已释放"
            self.emit("error", message=f"{prefix}：{self.describe_error(exc)}", cancelled=cancelled)

    def _check_connection(self) -> None:
        if self.spectrometer is None or self.connected is None:
            return
        checker = getattr(self.spectrometer, "check_connection", None)
        if not callable(checker):
            return
        try:
            if checker():
                return
            raise RuntimeError("设备未响应连接检查。")
        except Exception as exc:
            try:
                self._disconnect()
            except Exception:
                pass
            self.emit("error", message=f"USB 连接已断开：{self.describe_error(exc)}")

    def _shutdown(self) -> None:
        self.running = False
        self.transition(AcquisitionState.SHUTTING_DOWN)
        self.stop_event.set()

    def _cleanup(self) -> None:
        try:
            if self.spectrometer is not None:
                self.spectrometer.shutdown()
        finally:
            self.connected = None
            self.context = None
            self.emit("shutdown_complete")
