"""Keep an installer-visible marker alive until every device process exits."""
import ctypes
import os

RUNNING_MUTEX = "Local\\OceanLab.Win7.Running"
_handle = None


def hold_running_marker():
    global _handle
    if os.name != "nt" or _handle is not None:
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    _handle = kernel.CreateMutexW(None, False, RUNNING_MUTEX)
    if not _handle:
        raise ctypes.WinError(ctypes.get_last_error())
    # Intentionally held for process lifetime; Windows closes it at exit.
