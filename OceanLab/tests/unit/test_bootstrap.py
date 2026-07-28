from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oceanlab import cli
from oceanlab.bootstrap import configure_log_file
from oceanlab.config import APP_ICON_PATH


class BootstrapCompatibilityTest(unittest.TestCase):
    def test_application_icon_asset_exists(self) -> None:
        self.assertTrue(APP_ICON_PATH.is_file(), APP_ICON_PATH)

    def test_windowed_version_check_does_not_require_stdout(self) -> None:
        with mock.patch.object(cli.sys, "stdout", None):
            self.assertEqual(cli.main(["--version"]), 0)

    def test_log_file_handler_writes_utf8_without_basic_config_encoding(self) -> None:
        root_logger = logging.getLogger()
        previous_handlers = list(root_logger.handlers)
        previous_level = root_logger.level
        for handler in previous_handlers:
            root_logger.removeHandler(handler)

        try:
            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
                with mock.patch("oceanlab.bootstrap.tempfile.gettempdir", return_value=temp_dir):
                    path = configure_log_file()
                logging.getLogger("OceanLab.compatibility").info("光谱日志")
                for handler in root_logger.handlers:
                    handler.flush()
                self.assertEqual(path, Path(temp_dir) / "OceanLab" / "OceanLab.log")
                self.assertIn("光谱日志", path.read_text(encoding="utf-8"))
                for handler in list(root_logger.handlers):
                    root_logger.removeHandler(handler)
                    handler.close()
        finally:
            for handler in list(root_logger.handlers):
                root_logger.removeHandler(handler)
                handler.close()
            for handler in previous_handlers:
                root_logger.addHandler(handler)
            root_logger.setLevel(previous_level)


if __name__ == "__main__":
    unittest.main()
