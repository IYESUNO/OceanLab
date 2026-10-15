from __future__ import annotations

import __future__
import importlib
import importlib.util
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
_WINDOWS_DLL_HANDLES: list[Any] = []


def _model_key(model: str) -> str:
    return "".join(character for character in str(model).upper() if character.isalnum())


def default_saturation_intensity(model: str, max_intensity: float) -> float:
    """OceanView uses the detector maximum, not a corrected-peak estimate."""
    return float(max_intensity)


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
    program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
    installed_locations = (
        Path(program_files) / "Ocean Optics" / "OceanDirect",
        Path(program_files) / "Ocean Insight" / "OceanDirect",
    ) if program_files else ()
    for value in (
        os.environ.get("OCEANDIRECT_HOME"),
        os.environ.get("OCEANDIRECT_SDK_HOME"),
        DEFAULT_SDK_HOME,
        *installed_locations,
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
    _prepare_windows_dll_search(sdk_home)
    python_text = str(python_dir)
    if python_text not in sys.path:
        sys.path.insert(0, python_text)

    # Current OceanDirect releases use annotations such as ``list[int]`` and
    # ``tuple[int, str]``. They are valid on Python 3.9+, but legacy Python
    # versions evaluate them while importing and raise "'type' object is not
    # subscriptable". Compile this one external SDK module with postponed
    # annotation evaluation. The SDK source on disk remains untouched.
    importlib.import_module("oceandirect")
    module_name = "oceandirect.OceanDirectAPI"
    module_path = python_dir / "oceandirect" / "OceanDirectAPI.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None:
        raise ImportError(f"无法创建 OceanDirect SDK 模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        source = module_path.read_bytes()
        code = compile(
            source,
            str(module_path),
            "exec",
            flags=__future__.annotations.compiler_flag,
            dont_inherit=True,
        )
        exec(code, module.__dict__)
    except Exception:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
        raise
    return module.OceanDirectAPI, module.OceanDirectError


def _prepare_windows_dll_search(sdk_home: Path) -> None:
    if os.name != "nt":
        return

    sdk_lib = sdk_home / "Python" / "oceandirect" / "lib"
    if not sdk_lib.is_dir():
        return

    # Python 3.7 uses the classic Windows DLL search behavior. Add the official
    # SDK directory explicitly and preload the application-local VC142 runtime
    # so very old offline instrument computers do not need a separate VC++
    # Redistributable installation.
    app_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    search_dirs = (app_root, sdk_lib)
    current_path = os.environ.get("PATH", "")
    existing_parts = [part for part in current_path.split(os.pathsep) if part]
    prefixes = [str(path) for path in search_dirs if str(path) not in existing_parts]
    if prefixes:
        os.environ["PATH"] = os.pathsep.join(prefixes + existing_parts)

    import ctypes

    for filename in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        runtime_path = app_root / filename
        if runtime_path.is_file():
            _WINDOWS_DLL_HANDLES.append(ctypes.WinDLL(str(runtime_path)))

    set_dll_directory = getattr(ctypes.windll.kernel32, "SetDllDirectoryW", None)
    if set_dll_directory is not None:
        set_dll_directory.argtypes = (ctypes.c_wchar_p,)
        set_dll_directory.restype = ctypes.c_bool
        set_dll_directory(str(sdk_lib))


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
            model = str(self.device.get_device_type())
            max_intensity = float(self.device.get_max_intensity())
            saturation_intensity = default_saturation_intensity(model, max_intensity)
            advanced = getattr(self.device, "Advanced", None)
            saturation_reader = getattr(advanced, "get_autonull_saturation_level", None)
            if callable(saturation_reader) and "QE65000" not in _model_key(model):
                try:
                    reported_saturation = float(saturation_reader())
                except Exception:
                    reported_saturation = 0.0
                # QE65000 uses its ADC maximum, matching OceanView. Other
                # devices can report an explicit usable SDK saturation value.
                if (
                    reported_saturation > 0
                    and math.isfinite(reported_saturation)
                ):
                    saturation_intensity = min(max_intensity, reported_saturation)
            info = DeviceInfo(
                device_id=self.device_id,
                serial=str(self.device.get_serial_number()),
                model=model,
                pixels=int(self.device.get_formatted_spectrum_length()),
                wavelength_min=wavelengths[0],
                wavelength_max=wavelengths[-1],
                integration_min_us=int(self.device.get_minimum_integration_time()),
                integration_max_us=int(self.device.get_maximum_integration_time()),
                integration_increment_us=int(self.device.get_integration_time_increment()),
                max_intensity=max_intensity,
                capabilities=capabilities,
                saturation_intensity=saturation_intensity,
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
            if previous["core"]["integration_us"] != int(request.integration_us):
                self.device.set_integration_time(int(request.integration_us))
            if previous["core"]["averages"] != max(1, int(request.averages)):
                self.device.set_scans_to_average(max(1, int(request.averages)))
            if previous["core"]["boxcar"] != max(0, int(request.boxcar)):
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

            options = self._apply_optional_configuration(request, capabilities, previous)
            # Optional SDK setters can affect other parameters. Verify the
            # final state before declaring this configuration confirmed.
            actual = self._core_settings(request.interval_ms)
            for name, value in requested_core.items():
                if actual[name] != value:
                    raise RuntimeError(f"{name} 最终读回值 {actual[name]} 与请求值 {value} 不一致。")
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
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        actual: dict[str, Any] = {
            "electric_dark": bool(previous.get("electric_dark", False)),
            "nonlinearity": bool(previous.get("nonlinearity", False)),
            "trigger_mode": int(previous.get("trigger_mode", 0)),
            "light_source": bool(previous.get("light_source", False)),
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
            if actual[name] != requested:
                setter()
            actual[name] = getter()
            if actual[name] != requested:
                raise RuntimeError(f"{name} 读回值与请求不一致。")
        # SDKs may couple NLC and electric-dark switches. Check the complete
        # final state after every setter has run, not just intermediate states.
        supported = {
            "electric_dark": capabilities.electric_dark,
            "nonlinearity": capabilities.nonlinearity,
            "trigger_mode": capabilities.trigger,
            "light_source": capabilities.light_source,
        }
        for name, enabled, setter, getter, requested in operations:
            if supported[name]:
                actual[name] = getter()
                if enabled and actual[name] != requested:
                    raise RuntimeError(f"{name} 最终读回值与请求不一致。")
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

    def bind_pause_event(self, pause_requested) -> None:
        self._pause_requested = pause_requested

    def _paused(self) -> bool:
        flag = getattr(self, "_pause_requested", None)
        return flag is not None and flag.is_set()

    def _read_values(self) -> tuple[float, ...]:
        values = tuple(float(value) for value in self.device.get_formatted_spectrum())
        return self._validate_values(values)

    def _validate_values(self, values) -> tuple[float, ...]:
        values = tuple(float(value) for value in values)
        if len(values) != len(self.connected.wavelengths):
            raise RuntimeError(f"光谱长度异常：期望 {len(self.connected.wavelengths)} 点，实际 {len(values)} 点。")
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError("光谱包含非有限数值，已拒绝本帧数据。")
        return values

    def acquire(self, context: SpectrumContext) -> SpectrumFrame | None:
        if self.device is None or self.connected is None:
            raise RuntimeError("请先连接仪器。")
        if self._paused():
            return None
        if context.averages <= 1:
            values = self._read_values()
        else:
            values = self._acquire_average(context)
        if self._paused():
            return None
        return SpectrumFrame(values=values, timestamp=time.time(), context=context)

    def _acquire_average(self, context: SpectrumContext) -> tuple[float, ...] | None:
        # OceanView checks for stop between scans, then processes the average.
        # SDK electric-dark subtraction is linear; averaging its results is
        # equivalent to subtracting the averaged dark level (apart from roundoff).
        # NLC is nonlinear, so it MUST run once after the average, before boxcar.
        try:
            self.device.set_scans_to_average(1)
            if context.boxcar:
                self.device.set_boxcar_width(0)
            if context.nonlinearity:
                self.device.set_nonlinearity_correction_usage(False)
            self._verify_scan_configuration(context, 1, 0, False)
            total = [0.0] * context.pixel_count
            for _ in range(context.averages):
                if self._paused():
                    return None
                scan = self._read_values()
                if self._paused():
                    return None
                for index, value in enumerate(scan):
                    total[index] += value
            values = [value / context.averages for value in total]
            if context.nonlinearity:
                # No second dark subtraction: electric-dark is already applied.
                # This processes supplied data; it does not acquire another scan
                # or mutate the SDK's stored dark spectrum.
                if context.electric_dark:
                    self.device.set_electric_dark_correction_usage(False)
                    if self.device.get_electric_dark_correction_usage():
                        raise RuntimeError("处理平均谱前 electric_dark 关闭读回失败。")
                values = self.device.nonlinearity_correct_spectrum2([0.0] * len(values), values)
            if context.boxcar:
                values = self.device.boxcar_correct_spectrum(values, context.boxcar)
            return self._validate_values(values)
        finally:
            # Restoration failure is a real device error, never a successful
            # pause. The service releases that connection through its error path.
            self.device.set_scans_to_average(context.averages)
            if context.boxcar:
                self.device.set_boxcar_width(context.boxcar)
            if context.nonlinearity:
                self.device.set_nonlinearity_correction_usage(True)
                if self.connected.info.capabilities.electric_dark:
                    self.device.set_electric_dark_correction_usage(context.electric_dark)
            self._verify_scan_configuration(context, context.averages, context.boxcar, context.nonlinearity)

    def _verify_scan_configuration(self, context, averages, boxcar, nonlinearity) -> None:
        actual = self._core_settings(1)
        expected = {"integration_us": context.integration_us, "averages": averages, "boxcar": boxcar}
        if any(actual[name] != value for name, value in expected.items()):
            raise RuntimeError("逐次扫描参数读回不一致。")
        capabilities = self.connected.info.capabilities
        if capabilities.electric_dark and bool(self.device.get_electric_dark_correction_usage()) != context.electric_dark:
            raise RuntimeError("逐次扫描 electric_dark 读回不一致。")
        if capabilities.nonlinearity and bool(self.device.get_nonlinearity_correction_usage()) != nonlinearity:
            raise RuntimeError("逐次扫描 nonlinearity 读回不一致。")

    def check_connection(self) -> bool:
        if self.device is None or self.connected is None:
            return False
        serial = str(self.device.get_serial_number())
        return serial == self.connected.info.serial

    def _close_current_device(self) -> None:
        closing_id = self.device_id
        close_error: Exception | None = None
        try:
            if self.api is not None and closing_id is not None:
                self.api.close_device(closing_id)
        except Exception as exc:
            close_error = exc
        finally:
            if self.api is not None and closing_id is not None:
                cached = getattr(self.api, "open_devices", None)
                if isinstance(cached, dict):
                    cached.pop(closing_id, None)
            self.device = None
            self.device_id = None
            self.connected = None
        if close_error is not None:
            raise close_error

    def disconnect(self) -> None:
        self._close_current_device()

    def shutdown(self) -> None:
        try:
            self.disconnect()
        finally:
            if self.api is not None:
                try:
                    self.api.shutdown()
                finally:
                    self.api = None
