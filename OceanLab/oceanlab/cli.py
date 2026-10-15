from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
from dataclasses import replace
from functools import partial

from .adapters.oceandirect import OceanDirectSpectrometer, is_oceandirect_home, resolve_oceandirect_home
from .adapters.isolated_spectrometer import IsolatedSpectrometer
from .bootstrap import configure_log_file, run_gui
from .config import APP_VERSION, SIMULATION_SETTINGS


LOG_FILE = configure_log_file()
logger = logging.getLogger("OceanLab")


def diagnostic_output(*values) -> None:
    if sys.stdout is not None:
        print(*values)
    else:
        logger.info(" ".join(map(str, values)))


def probe_device(sdk_home, acquire_once: bool = False) -> int:
    adapter = IsolatedSpectrometer(partial(OceanDirectSpectrometer, sdk_home))
    try:
        version = adapter.initialize()
        diagnostic_output("OceanDirect API:", ".".join(map(str, version)))
        connected = adapter.connect()
        info = connected.info
        diagnostic_output("USB devices: 1")
        diagnostic_output(
            f"- ID={info.device_id}, model={info.model}, serial={info.serial}, "
            f"pixels={info.pixels}, range={info.wavelength_min:.2f}-{info.wavelength_max:.2f} nm"
        )
        if acquire_once:
            capabilities = info.capabilities
            request = replace(
                connected.settings, **SIMULATION_SETTINGS,
                trigger_mode=0, electric_dark=capabilities.electric_dark,
                nonlinearity=False, light_source=False,
                apply_electric_dark=capabilities.electric_dark,
                apply_nonlinearity=capabilities.nonlinearity,
                apply_trigger=capabilities.trigger, apply_light_source=capabilities.light_source,
            )
            context = adapter.apply_configuration(request)
            frame = adapter.acquire(context)
            peak_index = max(range(len(frame.values)), key=frame.values.__getitem__)
            diagnostic_output(
                f"  integration={context.integration_us} us, "
                f"peak={frame.values[peak_index]:.6g} @ {connected.wavelengths[peak_index]:.3f} nm"
            )
        return 0
    except RuntimeError as exc:
        if "未发现" in str(exc):
            diagnostic_output("USB devices: 0")
            return 2
        raise
    finally:
        adapter.shutdown()


def main(argv: list[str] | None = None) -> int:
    logger.info("OceanLab process started, Python=%s", sys.version.replace("\n", " "))
    argument_list = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argument_list and sys.stdout is None:
        # PyInstaller's windowed executable intentionally has no console
        # stream.  Logging the version still lets this diagnostic exit
        # successfully instead of opening the GUI error dialog.
        logger.info("OceanLab %s", APP_VERSION)
        return 0
    parser = argparse.ArgumentParser(description="OceanLab OceanDirect spectroscopy acquisition software")
    parser.add_argument("--version", action="version", version=f"OceanLab {APP_VERSION}")
    parser.add_argument("--sdk-home", default=None, help="显式指定 OceanDirect SDK 根目录；省略时自动检测")
    parser.add_argument("--probe", action="store_true", help="仅检测设备，不启动界面")
    parser.add_argument("--once", action="store_true", help="检测设备并采集一帧")
    args = parser.parse_args(argument_list)
    sdk_home = resolve_oceandirect_home(args.sdk_home)
    logger.info("OceanDirect SDK path resolved to %s (valid=%s)", sdk_home, is_oceandirect_home(sdk_home))
    if args.probe or args.once:
        return probe_device(sdk_home, acquire_once=args.once)
    return run_gui(sdk_home)


def entrypoint() -> None:
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        logger.exception("Unhandled application exception")
        try:
            import tkinter.messagebox

            tkinter.messagebox.showerror("OceanLab 启动失败", f"程序启动失败，请查看日志：\n{LOG_FILE}")
        except Exception:
            pass
        raise
