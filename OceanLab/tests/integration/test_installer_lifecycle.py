import ctypes
import os
import subprocess
import sys
import unittest
from pathlib import Path

from oceanlab.adapters.windows_lifecycle import RUNNING_MUTEX


@unittest.skipUnless(os.name == "nt", "Windows installer process marker")
class InstallerLifecycleTest(unittest.TestCase):
    def test_marker_outlives_main_thread_and_disappears_at_process_exit(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenMutexW.argtypes = (ctypes.c_uint, ctypes.c_bool, ctypes.c_wchar_p)
        kernel.OpenMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        # Isolate the marker from any actual OceanLab processes on this machine.
        marker = RUNNING_MUTEX + ".Test." + str(os.getpid())
        code = (
            "import sys; from oceanlab.adapters import windows_lifecycle as w; "
            "w.RUNNING_MUTEX = " + repr(marker) + "; "
            "w.hold_running_marker(); w.hold_running_marker(); "
            "print('ready', flush=True); sys.stdin.readline()"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[2]), universal_newlines=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            handle = kernel.OpenMutexW(0x00100000, False, marker)
            self.assertTrue(handle)
            if handle:
                kernel.CloseHandle(handle)
            _, error = process.communicate("exit\n", timeout=10)
            self.assertEqual(process.returncode, 0, error)
            handle = kernel.OpenMutexW(0x00100000, False, marker)
            if handle:
                kernel.CloseHandle(handle)
            self.assertFalse(handle)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
