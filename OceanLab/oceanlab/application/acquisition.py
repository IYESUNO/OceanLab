from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Any, Callable

from ..domain.models import AcquisitionRequest, AcquisitionState, ConnectedDevice, SpectrumContext
from ..ports.spectrometer import SpectrometerPort
from .messages import AcquisitionCommand, ApplicationEvent


class CoalescingEventQueue:
    def __init__(self) -> None:
        self._items: deque[ApplicationEvent] = deque()
        self._lock = threading.Lock()

    def put(self, item: ApplicationEvent | tuple[str, dict[str, Any]]) -> None:
        event = item if isinstance(item, ApplicationEvent) else ApplicationEvent(item[0], item[1])
        with self._lock:
            if event.kind == "spectrum" and self._items and self._items[-1].kind == "spectrum":
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

    def send(self, command: str, **payload: Any) -> None:
        self.commands.put(AcquisitionCommand(command, payload))

    def emit(self, kind: str, **payload: Any) -> None:
        self.events.put(ApplicationEvent(kind, payload))

    def transition(self, state: AcquisitionState) -> None:
        self.state = state
        self.emit("service_state", state=state)

    def run(self) -> None:
        try:
            self.spectrometer = self._factory()
            initializer = getattr(self.spectrometer, "initialize", None)
            version = tuple(initializer()) if callable(initializer) else (0, 0, 0)
            self.emit("sdk_ready", version=version)
        except Exception as exc:
            self.transition(AcquisitionState.ERROR)
            self.emit("fatal", message=f"SDK 加载失败：{self.describe_error(exc)}")
            return

        next_acquisition = 0.0
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
                self.emit("error", message=self.describe_error(exc))
                if self.state is AcquisitionState.CONFIGURING:
                    self.transition(AcquisitionState.IDLE)

            if self.running and time.monotonic() >= next_acquisition:
                started = time.monotonic()
                self._acquire_frame()
                next_acquisition = time.monotonic() + max(0.001, self.interval_s - (time.monotonic() - started))
        self._cleanup()

    @staticmethod
    def describe_error(exc: Exception) -> str:
        if hasattr(exc, "get_error_details"):
            code, message = exc.get_error_details()
            return f"OceanDirect 错误 {code}：{message}"
        return str(exc)

    def _handle(self, command: AcquisitionCommand) -> None:
        handlers = {
            "connect": self._connect,
            "disconnect": self._disconnect,
            "configure_start": lambda: self._configure("start", command.payload),
            "configure_single": lambda: self._configure("single", command.payload),
            "start": self._start,
            "stop": self._stop,
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
        if self.spectrometer is not None:
            self.spectrometer.disconnect()
        self.connected = None
        self.context = None
        self.transition(AcquisitionState.DISCONNECTED)
        self.emit("disconnected")

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
        self.running = False
        self.transition(AcquisitionState.CONFIGURING)
        context = self.spectrometer.apply_configuration(request)
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
        if action == "start":
            self._start()
        else:
            self.transition(AcquisitionState.IDLE)
            self._single()

    def _apply_partial(self, payload: dict[str, Any]) -> None:
        request = self._request_from_payload(payload)
        if self.spectrometer is None or self.connected is None:
            raise RuntimeError("请先连接仪器。")
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

    def _start(self) -> None:
        if self.context is None:
            raise RuntimeError("采集配置尚未由仪器确认，拒绝启动采集。")
        self.running = True
        self.transition(AcquisitionState.ACQUIRING)
        self.emit("acquisition_state", running=True)

    def _stop(self) -> None:
        self.running = False
        self.transition(AcquisitionState.IDLE if self.connected else AcquisitionState.DISCONNECTED)
        self.emit("acquisition_state", running=False)

    def _single(self) -> None:
        if self.context is None:
            raise RuntimeError("采集配置尚未由仪器确认。")
        self._acquire_frame()

    def _acquire_frame(self) -> None:
        if self.spectrometer is None or self.context is None:
            return
        try:
            frame = self.spectrometer.acquire(self.context)
            self.emit("spectrum", spectrum=list(frame.values), timestamp=frame.timestamp, context=frame.context)
        except Exception as exc:
            self.running = False
            self.emit("acquisition_state", running=False)
            self._disconnect()
            self.emit("error", message=f"采集失败，仪器连接已释放：{self.describe_error(exc)}")

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
