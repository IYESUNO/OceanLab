from __future__ import annotations

import logging
import sys
import tempfile
from functools import partial
from pathlib import Path

from .adapters.oceandirect import OceanDirectSpectrometer, is_oceandirect_home, normalize_sdk_home
from .adapters.filesystem import DelimitedSpectrumExporter, JsonPreferencesRepository
from .adapters.spectrum_files import DelimitedSpectrumImporter
from .adapters.simulation import SimulatedSpectrometer
from .adapters.isolated_spectrometer import IsolatedSpectrometer
from .adapters.windows_lifecycle import hold_running_marker
from .application.acquisition import AcquisitionService, CoalescingEventQueue
from .config import APP_ICON_PATH, APP_USER_MODEL_ID, PREFERENCES_FILE
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


def configure_windows_app_identity() -> str:
    """Set the Windows 7+ taskbar identity before creating any UI window."""
    if sys.platform != "win32":
        return "not-windows"
    try:
        import ctypes

        setter = getattr(ctypes.windll.shell32, "SetCurrentProcessExplicitAppUserModelID", None)
        if setter is None:
            return "unsupported"
        setter.argtypes = (ctypes.c_wchar_p,)
        setter.restype = ctypes.c_long
        result = int(setter(APP_USER_MODEL_ID))
        if result != 0:
            logging.getLogger("OceanLab").warning(
                "Could not set Windows AppUserModelID %s: HRESULT 0x%08x",
                APP_USER_MODEL_ID,
                result & 0xFFFFFFFF,
            )
            return f"failed-0x{result & 0xFFFFFFFF:08x}"
        return APP_USER_MODEL_ID
    except Exception:
        logging.getLogger("OceanLab").exception("Could not set Windows AppUserModelID")
        return "failed"


def _apply_native_windows_icons(root, icon_path: Path) -> bool:
    """Bind correctly sized ICO images to the actual Win32 top-level window."""
    import ctypes

    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.LoadImageW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    )
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_void_p,
    )
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.DestroyIcon.argtypes = (ctypes.c_void_p,)
    user32.DestroyIcon.restype = ctypes.c_bool

    root.update_idletasks()
    child_handle = ctypes.c_void_p(int(root.winfo_id()))
    window_handle = user32.GetAncestor(child_handle, 2) or child_handle

    big_width = max(32, int(user32.GetSystemMetrics(11)))
    big_height = max(32, int(user32.GetSystemMetrics(12)))
    small_width = max(16, int(user32.GetSystemMetrics(49)))
    small_height = max(16, int(user32.GetSystemMetrics(50)))
    load_from_file = 0x0010
    image_icon = 1
    big_icon = user32.LoadImageW(
        None,
        str(icon_path),
        image_icon,
        big_width,
        big_height,
        load_from_file,
    )
    small_icon = user32.LoadImageW(
        None,
        str(icon_path),
        image_icon,
        small_width,
        small_height,
        load_from_file,
    )
    if not big_icon or not small_icon:
        if big_icon:
            user32.DestroyIcon(big_icon)
        if small_icon:
            user32.DestroyIcon(small_icon)
        return False

    wm_seticon = 0x0080
    user32.SendMessageW(window_handle, wm_seticon, 1, big_icon)
    user32.SendMessageW(window_handle, wm_seticon, 0, small_icon)
    # The window owns these icon handles until process shutdown. Keeping the
    # references on the root prevents accidental replacement or early cleanup.
    root._oceanlab_native_icon_handles = (big_icon, small_icon)
    root._oceanlab_native_icon_sizes = (
        (big_width, big_height),
        (small_width, small_height),
    )
    return True


def apply_windows_app_icon(root) -> str:
    """Apply multi-size ICO identities to Tk and its Win32 top-level window."""
    if sys.platform != "win32":
        return "not-windows"
    icon_path = APP_ICON_PATH
    if not icon_path.is_file():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            icon_path = Path(meipass) / "assets" / "app_icons" / icon_path.name
    if not icon_path.is_file():
        logging.getLogger("OceanLab").warning("Application icon not found: %s", icon_path)
        return "missing"
    try:
        root.iconbitmap(default=str(icon_path))
        if not _apply_native_windows_icons(root, icon_path):
            logging.getLogger("OceanLab").warning(
                "Could not apply size-specific Win32 application icons: %s",
                icon_path,
            )
        return str(icon_path)
    except Exception:
        logging.getLogger("OceanLab").exception("Could not apply application icon: %s", icon_path)
        return "failed"


def service_factory(sdk_home: Path, events: CoalescingEventQueue) -> AcquisitionService:
    hardware_factory = partial(OceanDirectSpectrometer, sdk_home)
    factory = partial(IsolatedSpectrometer, hardware_factory) if is_oceandirect_home(sdk_home) else hardware_factory
    return AcquisitionService(factory, events)


def run_gui(sdk_home: Path) -> int:
    import tkinter as tk

    hold_running_marker()
    configure_windows_app_identity()
    enable_windows_per_monitor_dpi()
    root = tk.Tk()
    apply_windows_app_icon(root)
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
