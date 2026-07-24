from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

from .adapters.oceandirect import OceanDirectSpectrometer, is_oceandirect_home, normalize_sdk_home
from .adapters.filesystem import DelimitedSpectrumExporter, JsonPreferencesRepository
from .adapters.spectrum_files import DelimitedSpectrumImporter
from .adapters.simulation import SimulatedSpectrometer
from .application.acquisition import AcquisitionService, CoalescingEventQueue
from .config import PREFERENCES_FILE
from .ui.app import OceanLabApp


def configure_log_file() -> Path:
    candidates = [
        Path(tempfile.gettempdir()) / "OceanLab",
        Path.home() / "AppData" / "Local" / "OceanLab",
    ]
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "OceanLab.log"
            file_handler = logging.FileHandler(path, encoding="utf-8")
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
                handlers=[file_handler],
            )
            return path
        except OSError:
            continue
    logging.basicConfig(level=logging.INFO)
    return Path(tempfile.gettempdir()) / "OceanLab.log"


def enable_windows_per_monitor_dpi() -> str:
    if sys.platform != "win32":
        return "not-windows"
    try:
        import ctypes

        user32 = ctypes.windll.user32
        set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None:
            set_context.argtypes = (ctypes.c_void_p,)
            set_context.restype = ctypes.c_bool
            if set_context(ctypes.c_void_p(-4)):
                return "per-monitor-v2"
        shcore = getattr(ctypes.windll, "shcore", None)
        set_awareness = getattr(shcore, "SetProcessDpiAwareness", None) if shcore is not None else None
        if set_awareness is not None and set_awareness(2) == 0:
            return "per-monitor"
        if user32.SetProcessDPIAware():
            return "system-aware"
    except Exception:
        logging.getLogger("OceanLab").exception("Could not configure Windows DPI awareness")
    return "unchanged"


def service_factory(sdk_home: Path, events: CoalescingEventQueue) -> AcquisitionService:
    return AcquisitionService(lambda: OceanDirectSpectrometer(sdk_home), events)


def run_gui(sdk_home: Path) -> int:
    import tkinter as tk

    enable_windows_per_monitor_dpi()
    root = tk.Tk()
    OceanLabApp(
        root,
        sdk_home,
        service_factory=service_factory,
        sdk_validator=is_oceandirect_home,
        sdk_normalizer=normalize_sdk_home,
        preferences_repository=JsonPreferencesRepository(PREFERENCES_FILE),
        spectrum_exporter=DelimitedSpectrumExporter(),
        spectrum_importer=DelimitedSpectrumImporter(),
        simulation_factory=SimulatedSpectrometer,
    )
    root.mainloop()
    return 0
