from __future__ import annotations

import os
import tempfile
from pathlib import Path


APP_VERSION = "2.0.3"
PROJECT_DIR = Path(__file__).resolve().parents[1]
TOOLBAR_ICON_DIR = PROJECT_DIR / "assets" / "oceanview_toolbar"
APP_ICON_PATH = PROJECT_DIR / "assets" / "app_icons" / "oceanlab-icon-spectrum.ico"
APP_USER_MODEL_ID = "OceanLab.Spectroscopy"
PREFERENCES_FILE = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "OceanLab" / "settings.json"

SIMULATION_SETTINGS = {
    "integration_us": 10_000,
    "averages": 1,
    "boxcar": 0,
    "interval_ms": 80,
}
TRIGGER_MODE_OPTIONS = ("按需 / 正常", "软件触发")
TRIGGER_MODE_TO_VALUE = {"按需 / 正常": 0, "软件触发": 1}
TRIGGER_VALUE_TO_LABEL = {value: label for label, value in TRIGGER_MODE_TO_VALUE.items()}
SAVE_FORMAT_CSV = "CSV（UTF-8，含表头）"
SAVE_FORMAT_TXT = "TXT（制表符，含表头）"
SAVE_FORMAT_OPTIONS = (SAVE_FORMAT_CSV, SAVE_FORMAT_TXT)
SAVE_SUFFIX_TIME = "按时间"
SAVE_SUFFIX_SEQUENCE = "按序号"
SAVE_SUFFIX_NONE = "无后缀"
INTEGRATION_PRESET_NAMES = ("预设1", "预设2", "预设3")
