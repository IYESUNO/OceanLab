from __future__ import annotations

import unittest
from pathlib import Path

from oceanlab import APP_VERSION as PACKAGE_VERSION
from oceanlab.config import APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class VersionConsistencyTest(unittest.TestCase):
    def test_release_version_is_synchronized(self) -> None:
        self.assertEqual(PACKAGE_VERSION, APP_VERSION)
        for relative_path in (
            "build_win7_legacy.ps1",
            "packaging/version_info.txt",
            "packaging/PORTABLE_README_WIN7.txt",
            "README.md",
        ):
            content = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn(APP_VERSION, content, relative_path)


if __name__ == "__main__":
    unittest.main()
