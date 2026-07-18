from __future__ import annotations

import importlib
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..domain.models import (
    AcquisitionRequest,
    ConnectedDevice,
    DeviceCapabilities,
    DeviceInfo,
    SpectrumContext,
    SpectrumFrame,
)


DEFAULT_SDK_HOME = Path(r"C:\OceanDirect")


def normalize_sdk_home(value: str | os.PathLike[str]) -> Path:
    text = os.path.expandvars(os.fspath(value)).strip().strip('"')
    return Path(text).expanduser()


def is_oceandirect_home(path: Path) -> bool:
    try:
        return (
            (path / "Python" / "oceandirect" / "OceanDirectAPI.py").is_file()
            and (path / "Python" / "oceandirect" / "lib" / "OceanDirect.dll").is_file()
        )
    except OSError:
        return False


def oceandirect_candidates() -> list[Path]:
    candidates: list[Path] = []
    for value in (
        os.environ.get("OCEANDIRECT_HOME"),
        os.environ.get("OCEANDIRECT_SDK_HOME"),
        DEFAULT_SDK_HOME,
    ):
        if value:
            path = normalize_sdk_home(value)
            if path not in candidates:
                candidates.append(path)
    return candidates


def resolve_oceandirect_home(explicit_home: str | None = None) -> Path:
    if explicit_home:
        return normalize_sdk_home(explicit_home)
    for candidate in oceandirect_candidates():
        if is_oceandirect_home(candidate):
            return candidate
    return DEFAULT_SDK_HOME


def load_oceandirect(sdk_home: Path):
    python_dir = sdk_home / "Python"
    if not python_dir.exists():
        raise FileNotFoundError(f"找不到 OceanDirect Python SDK：{python_dir}")
    python_text = str(python_dir)
    if python_text not in sys.path:
        sys.path.insert(0, python_text)
    module = importlib.import_module("oceandirect.OceanDirectAPI")
    return module.OceanDirectAPI, module.OceanDirectError


class OceanDirectSpectrometer:
    """The only runtime component allowed to call the OceanDirect SDK."""

    def __init__(self, sdk_home: Path):
        self.sdk_home = sdk_home
        self.api: Any = None
        self.device: Any = None
        self.device_id: int | None = None
        self.connected: ConnectedDevice | None = None
        self.error_type: type[Exception] = Exception

    def initialize(self) -> tuple[int, ...]:
        if self.api is None:
            api_type, self.error_type = load_oceandirect(self.sdk_home)
            self.api = api_type()
        return tuple(self.api.get_api_version_numbers())

    @staticmethod
    def describe_error(exc: Exception) -> str:
        if hasattr(exc, "get_error_details"):
            code, message = exc.get_error_details()
            return f"OceanDirect 错误 {code}：{message}"
        return str(exc)

    def connect(self) -> ConnectedDevice:
        self.initialize()
        if self.connected is not None:
            return self.connected
        count = self.api.find_usb_devices()
        device_ids = self.api.get_device_ids()
        if count < 1 or not device_ids:
            raise RuntimeError("未发现 Ocean Insight USB 光谱仪。请检查电源、USB 线和 WinUSB 驱动。")

        self.device_id = int(device_ids[0])
        cached = getattr(self.api, "open_devices", None)
        cached_device = cached.get(self.device_id) if isinstance(cached, dict) else None
        if cached_device is not None and getattr(cached_device, "status", None) != "open":
            cached.pop(self.device_id, None)
        try:
            self.device = self.api.open_device(self.device_id)
            wavelengths = tuple(float(value) for value in self.device.get_wavelengths())
            capabilities, optional = self._read_optional_settings()
            info = DeviceInfo(
                device_id=self.device_id,
                serial=str(self.device.get_serial_number()),
                model=str(self.device.get_device_type()),
                pixels=int(self.device.get_formatted_spectrum_length()),
                wavelength_min=wavelengths[0],
                wavelength_max=wavelengths[-1],
                integration_min_us=int(self.device.get_minimum_integration_time()),
                integration_max_us=int(self.device.get_maximum_integration_time()),
                integration_increment_us=int(self.device.get_integration_time_increment()),
                max_intensity=float(self.device.get_max_intensity()),
                capabilities=capabilities,
            )
            settings = AcquisitionRequest(
                integration_us=int(self.device.get_integration_time()),
                averages=max(1, int(self.device.get_scans_to_average())),
                boxcar=max(0, int(self.device.get_boxcar_width())),
                interval_ms=80,
                trigger_mode=int(optional["trigger_mode"]),
                electric_dark=bool(optional["electric_dark"]),
                nonlinearity=bool(optional["nonlinearity"]),
                light_source=bool(optional["light_source"]),
            )
            self.connected = ConnectedDevice(info=info, wavelengths=wavelengths, settings=settings)
            return self.connected
        except Exception:
            self._close_current_device()
            raise

    def _read_optional_settings(self) -> tuple[DeviceCapabilities, dict[str, Any]]:
        values: dict[str, Any] = {
            "electric_dark": False,
            "nonlinearity": False,
            "trigger_mode": 0,
            "light_source": False,
        }
        supported: dict[str, bool] = {}
        readers = {
            "electric_dark": self.device.get_electric_dark_correction_usage,
            "nonlinearity": self.device.get_nonlinearity_correction_usage,
            "trigger_mode": self.device.get_trigger_mode,
            "light_source": self.device.Advanced.get_enable_lamp,
        }
        for name, reader in readers.items():
            try:
                values[name] = reader()
                supported["trigger" if name == "trigger_mode" else name] = True
            except Exception:
                supported["trigger" if name == "trigger_mode" else name] = False
        return DeviceCapabilities(**supported), values

    def _core_settings(self, interval_ms: int) -> dict[str, int]:
        return {
            "integration_us": int(self.device.get_integration_time()),
            "averages": max(1, int(self.device.get_scans_to_average())),
            "boxcar": max(0, int(self.device.get_boxcar_width())),
            "interval_ms": max(1, int(interval_ms)),
        }

    def apply_configuration(self, request: AcquisitionRequest) -> SpectrumContext:
        if self.device is None or self.connected is None:
            raise RuntimeError("请先连接仪器。")
        capabilities = self.connected.info.capabilities
        previous = self._snapshot_configuration(request.interval_ms, capabilities)
        try:
            self.device.set_integration_time(int(request.integration_us))
            self.device.set_scans_to_average(max(1, int(request.averages)))
            self.device.set_boxcar_width(max(0, int(request.boxcar)))
            actual = self._core_settings(request.interval_ms)
            requested_core = {
                "integration_us": int(request.integration_us),
                "averages": max(1, int(request.averages)),
                "boxcar": max(0, int(request.boxcar)),
                "interval_ms": max(1, int(request.interval_ms)),
            }
            for name, value in requested_core.items():
                if actual[name] != value:
                    raise RuntimeError(f"{name} 读回值 {actual[name]} 与请求值 {value} 不一致。")

            options = self._apply_optional_configuration(request, capabilities)
        except Exception:
            self._restore_configuration(previous)
            raise

        return SpectrumContext(
            source="instrument",
            serial=self.connected.info.serial,
            integration_us=actual["integration_us"],
            averages=actual["averages"],
            boxcar=actual["boxcar"],
            trigger_mode=int(options["trigger_mode"]),
            electric_dark=bool(options["electric_dark"]),
            nonlinearity=bool(options["nonlinearity"]),
            light_source=bool(options["light_source"]),
            pixel_count=len(self.connected.wavelengths),
            wavelength_min=self.connected.wavelengths[0],
            wavelength_max=self.connected.wavelengths[-1],
        )

    def _snapshot_configuration(self, interval_ms: int, capabilities: DeviceCapabilities) -> dict[str, Any]:
        previous: dict[str, Any] = {"core": self._core_settings(interval_ms)}
        if capabilities.electric_dark:
            previous["electric_dark"] = bool(self.device.get_electric_dark_correction_usage())
        if capabilities.nonlinearity:
            previous["nonlinearity"] = bool(self.device.get_nonlinearity_correction_usage())
        if capabilities.trigger:
            previous["trigger_mode"] = int(self.device.get_trigger_mode())
        if capabilities.light_source:
            previous["light_source"] = bool(self.device.Advanced.get_enable_lamp())
        return previous

    def _apply_optional_configuration(
        self,
        request: AcquisitionRequest,
        capabilities: DeviceCapabilities,
    ) -> dict[str, Any]:
        actual: dict[str, Any] = {
            "electric_dark": False,
            "nonlinearity": False,
            "trigger_mode": 0,
            "light_source": False,
        }
        operations = (
            ("electric_dark", request.apply_electric_dark and capabilities.electric_dark,
             lambda: self.device.set_electric_dark_correction_usage(bool(request.electric_dark)),
             lambda: bool(self.device.get_electric_dark_correction_usage()), bool(request.electric_dark)),
            ("nonlinearity", request.apply_nonlinearity and capabilities.nonlinearity,
             lambda: self.device.set_nonlinearity_correction_usage(bool(request.nonlinearity)),
             lambda: bool(self.device.get_nonlinearity_correction_usage()), bool(request.nonlinearity)),
            ("trigger_mode", request.apply_trigger and capabilities.trigger,
             lambda: self.device.set_trigger_mode(int(request.trigger_mode)),
             lambda: int(self.device.get_trigger_mode()), int(request.trigger_mode)),
            ("light_source", request.apply_light_source and capabilities.light_source,
             lambda: self.device.Advanced.set_enable_lamp(bool(request.light_source)),
             lambda: bool(self.device.Advanced.get_enable_lamp()), bool(request.light_source)),
        )
        for name, enabled, setter, getter, requested in operations:
            if not enabled:
                continue
            setter()
            actual[name] = getter()
            if actual[name] != requested:
                raise RuntimeError(f"{name} 读回值与请求不一致。")
        return actual

    def _restore_configuration(self, previous: dict[str, Any]) -> None:
        try:
            core = previous["core"]
            self.device.set_integration_time(core["integration_us"])
            self.device.set_scans_to_average(core["averages"])
            self.device.set_boxcar_width(core["boxcar"])
            if "electric_dark" in previous:
                self.device.set_electric_dark_correction_usage(previous["electric_dark"])
            if "nonlinearity" in previous:
                self.device.set_nonlinearity_correction_usage(previous["nonlinearity"])
            if "trigger_mode" in previous:
                self.device.set_trigger_mode(previous["trigger_mode"])
            if "light_source" in previous:
                self.device.Advanced.set_enable_lamp(previous["light_source"])
        except Exception:
            pass

    def acquire(self, context: SpectrumContext) -> SpectrumFrame:
        if self.device is None or self.connected is None:
            raise RuntimeError("请先连接仪器。")
        values = tuple(float(value) for value in self.device.get_formatted_spectrum())
        if len(values) != len(self.connected.wavelengths):
            raise RuntimeError(f"光谱长度异常：期望 {len(self.connected.wavelengths)} 点，实际 {len(values)} 点。")
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError("光谱包含非有限数值，已拒绝本帧数据。")
        return SpectrumFrame(values=values, timestamp=time.time(), context=context)

    def _close_current_device(self) -> None:
        closing_id = self.device_id
        if self.api is not None and closing_id is not None:
            try:
                self.api.close_device(closing_id)
            finally:
                cached = getattr(self.api, "open_devices", None)
                if isinstance(cached, dict):
                    cached.pop(closing_id, None)
        self.device = None
        self.device_id = None
        self.connected = None

    def disconnect(self) -> None:
        self._close_current_device()

    def shutdown(self) -> None:
        try:
            self.disconnect()
        finally:
            if self.api is not None:
                self.api.shutdown()
                self.api = None
