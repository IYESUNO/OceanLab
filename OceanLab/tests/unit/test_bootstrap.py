from __future__ import annotations

import unittest

from oceanlab.config import APP_ICON_PATH


class BootstrapCompatibilityTest(unittest.TestCase):
    def test_application_icon_asset_exists(self) -> None:
        self.assertTrue(APP_ICON_PATH.is_file(), APP_ICON_PATH)


if __name__ == "__main__":
    unittest.main()
