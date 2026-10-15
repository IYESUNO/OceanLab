from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from typing import Any, Callable

from ..ports.spectrometer import SpectrometerInterrupted, SpectrometerPort
from .windows_lifecycle import hold_running_marker


logger = logging.getLogger("OceanLab")


def _serve_spectrometer(connection, factory: Callable[[], SpectrometerPort], pause_requested) -> None:
    """Own the SDK exclusively in a disposable process, including its DLLs."""
    spectrometer = None
    try:
        hold_running_marker()
        spectrometer = factory()
        bind_pause = getattr(spectrometer, "bind_pause_event", None)
        if callable(bind_pause):
            bind_pause(pause_requested)
        while True:
            method, args = connection.recv()
            try:
                result = getattr(spectrometer, method)(*args)
                connection.send((True, result))
            except Exception as exc:
                details = getattr(exc, "get_error_details", None)
                message = str(exc)
                if callable(details):
                    code, description = details()
                    message = "OceanDirect 错误 {}：{}".format(code, description)
                connection.send((False, message))
            if method == "shutdown":
                break
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class IsolatedSpectrometer:
    """Bound SDK waits without concurrent access to the same USB device."""

    def __init__(self, factory: Callable[[], SpectrometerPort], operation_timeout_s: float = 15.0):
        self._factory = factory
        self.operation_timeout_s = operation_timeout_s
        self._process = None
        self._connection = None
        self._release_failed = False
        self._cancelled = threading.Event()
        self._bound_interrupt = False
        self._pause_requested = multiprocessing.get_context("spawn").Event()

    def pause_pending(self) -> None:
        # Shared flag only: the device process checks it between scans.
        self._pause_requested.set()

    def clear_pause(self) -> None:
        self._pause_requested.clear()

    def bind_interrupt_event(self, interruption: threading.Event) -> None:
        self._cancelled = interruption
        self._bound_interrupt = True

    def cancel_pending(self) -> None:
        # Called by the UI thread. Only signal; all process cleanup stays on
        # the serialized acquisition thread. Never call the SDK here.
        self._cancelled.set()

    def _dispose(self) -> None:
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if process is not None:
            try:
                if process.pid is not None and process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=1.0)
                    if process.is_alive():
                        raise RuntimeError("设备进程未能退出。")
                process.close()
            except Exception as exc:
                # Retain ownership if Windows cannot reap the process. Never
                # create another SDK process while this one might own USB.
                self._process, self._connection = process, connection
                self._release_failed = True
                raise RuntimeError("设备进程未能退出，请关闭程序后重新连接仪器。") from exc
        if connection is not None:
            connection.close()
        self._release_failed = False

    def _call(self, method: str, *args: Any, **options: Any):
        if self._release_failed:
            raise RuntimeError("设备进程尚未释放，请关闭程序后重新连接仪器。")
        started = time.monotonic()
        try:
            interruptible = method not in ("disconnect", "shutdown")
            if interruptible and self._cancelled.is_set():
                raise SpectrometerInterrupted("设备操作已取消。")
            if self._process is None:
                context = multiprocessing.get_context("spawn")
                parent, child = context.Pipe()
                self._connection = parent
                self._process = context.Process(
                    target=_serve_spectrometer, args=(child, self._factory, self._pause_requested),
                    name="OceanLabDevice", daemon=True,
                )
                try:
                    self._process.start()
                finally:
                    child.close()
            timeout = options.get("timeout_s", self.operation_timeout_s)
            self._connection.send((method, args))
            while True:
                if interruptible and self._cancelled.is_set():
                    raise SpectrometerInterrupted("已取消设备操作，连接已释放；请重新连接仪器。")
                if time.monotonic() - started > timeout:
                    label = {"initialize": "加载 SDK", "connect": "连接仪器", "apply_configuration": "同步参数",
                             "acquire": "读取光谱", "check_connection": "检查连接", "disconnect": "断开仪器",
                             "shutdown": "关闭仪器"}.get(method, "设备操作")
                    raise TimeoutError("{}超时（{:.1f} 秒），连接已释放；请重新连接仪器。".format(label, timeout))
                if self._connection.poll(0.05):
                    try:
                        success, result = self._connection.recv()
                    except EOFError as exc:
                        raise RuntimeError("设备进程通信中断，请重新连接仪器。") from exc
                    if not success:
                        raise RuntimeError(result)
                    return result
                if not self._process.is_alive():
                    raise RuntimeError("设备进程意外退出，连接已释放；请重新连接仪器。")
        except Exception:
            logger.exception("Device call failed: method=%s elapsed=%.3fs", method, time.monotonic() - started)
            self._dispose()
            raise
        finally:
            log = logger.debug if method in ("acquire", "check_connection") else logger.info
            log("Device call finished: method=%s elapsed=%.3fs", method, time.monotonic() - started)

    def initialize(self):
        return self._call("initialize")

    def connect(self):
        if not self._bound_interrupt:
            self._cancelled.clear()
        if self._process is None:
            self.initialize()
        return self._call("connect")

    def apply_configuration(self, request):
        return self._call("apply_configuration", request)

    def acquire(self, context):
        exposure = context.integration_us / 1_000_000.0 * context.averages
        timeout = max(self.operation_timeout_s, exposure * 2.0 + 5.0)
        return self._call("acquire", context, timeout_s=timeout)

    def check_connection(self):
        return self._call("check_connection") if self._process is not None else False

    def disconnect(self):
        if self._process is not None:
            try:
                self._call("disconnect")
            finally:
                self._dispose()

    def shutdown(self):
        if self._process is not None:
            try:
                self._call("shutdown")
            finally:
                self._dispose()
