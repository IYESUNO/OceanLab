from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable


from ..config import (
    APP_VERSION,
    INTEGRATION_PRESET_NAMES,
    SAVE_FORMAT_CSV,
    SAVE_FORMAT_OPTIONS,
    SAVE_FORMAT_TXT,
    SAVE_SUFFIX_NONE,
    SAVE_SUFFIX_SEQUENCE,
    SAVE_SUFFIX_TIME,
    SIMULATION_SETTINGS,
    TOOLBAR_ICON_DIR,
    TRIGGER_MODE_OPTIONS,
    TRIGGER_MODE_TO_VALUE,
    TRIGGER_VALUE_TO_LABEL,
)

LOG_FILE = Path(tempfile.gettempdir()) / "OceanLab" / "OceanLab.log"
DEFAULT_NAME_SUFFIX = "{integration}-{averages}"
logger = logging.getLogger("OceanLab")

from ..application.acquisition import AcquisitionService, CoalescingEventQueue
from ..application.absorbance import HistoricalAbsorbanceService
from ..application.pl_results import PlResultService, turbo_color
from ..application.spectrum_service import SpectrumService
from ..domain.models import AcquisitionRequest, BaselineSet, DeviceInfo, SpectrumContext, SpectrumFrame
from ..domain.processing import ProcessingMode
from ..domain.result_processing import PHOTON_ENERGY_EV_NM, calculate_tauc_values, normalize_peak, photon_energy_ev
from ..domain.results import PlotSeries
from ..ports.exporter import CurveExportRequest, ExportRequest, SpectrumExporter
from ..ports.preferences import PreferencesRepository
from ..ports.spectrometer import SpectrometerPort
from ..ports.spectrum_importer import SpectrumImporter

from .plot import SpectrumPlot
from .panels import (
    build_acquisition_workspace,
    build_absorbance_workspace,
    build_footer,
    build_header,
    build_pl_workspace,
    build_processing_workspace,
)
from .dialogs import center_dialog, close_modal
from .widgets import LargeCheckbutton, LargeRadiobutton
from .signal_offset_dialog import SignalOffsetDialog
from .tauc_modes import TAUC_DIRECT, TAUC_INDIRECT, TAUC_OPTIONS, normalize_tauc_mode, tauc_exponent, tauc_label
from .history_widgets import EllipsisLabel
from .history_icons import HistoryIconButton
from ..domain.signal_offset import assess_signal


@dataclass(frozen=True)
class SingleCaptureSnapshot:
    capture_id: int
    frame: SpectrumFrame
    wavelengths: tuple[float, ...]
    display_name: str


class OceanLabApp:
    def __init__(
        self,
        root,
        sdk_home: Path,
        service_factory: Callable[[Path, CoalescingEventQueue], AcquisitionService],
        sdk_validator: Callable[[Path], bool],
        sdk_normalizer: Callable[[str | os.PathLike[str]], Path],
        preferences_repository: PreferencesRepository,
        spectrum_exporter: SpectrumExporter,
        spectrum_importer: SpectrumImporter,
        simulation_factory: Callable[[], SpectrometerPort],
    ):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.sdk_home = sdk_home
        self.service_factory = service_factory
        self.sdk_validator = sdk_validator
        self.sdk_normalizer = sdk_normalizer
        self.preferences_repository = preferences_repository
        self.spectrum_exporter = spectrum_exporter
        self.spectrum_importer = spectrum_importer
        self.pl_result_service = PlResultService(spectrum_importer)
        self.historical_absorbance_service = HistoricalAbsorbanceService(spectrum_importer)
        self.simulation_spectrometer = simulation_factory()
        self.simulation_active_context: SpectrumContext | None = None
        self.events = CoalescingEventQueue()
        self.worker = self.service_factory(sdk_home, self.events)
        self.worker.start()

        self.connected = False
        self.simulation_mode = False
        self.sdk_available = False
        self.connection_pending = False
        self.auto_connect_attempted = False
        self.acquiring = False
        self.device_info: DeviceInfo | None = None
        self.wavelengths: list[float] = []
        self.raw_spectrum: list[float] = []
        self.reference_spectrum: list[float] | None = None
        self.frame_context: SpectrumContext | None = None
        self.reference_context: SpectrumContext | None = None
        self.reference_timestamp: float | None = None
        self.spectrum_service = SpectrumService()
        self.last_timestamp: float | None = None
        self.frame_times: list[float] = []
        self.closing = False
        self.simulation_after_id: str | None = None
        self.simulation_single_after_id: str | None = None
        self.simulation_frame_index = 0
        self.simulation_settings = SIMULATION_SETTINGS.copy()
        self.applied_settings = SIMULATION_SETTINGS.copy()
        self.single_in_progress = False
        self.acquisition_pending = False
        self.cancel_requested = False
        self._next_acquisition_request_id = 1
        self.active_acquisition_request_id: str | None = None
        self.single_started_at: float | None = None
        self.single_expected_s = 0.0
        self.single_progress_after_id: str | None = None
        self.advanced_expanded = False
        self.device_capabilities = {
            "electric_dark": False,
            "nonlinearity": False,
            "trigger": False,
            "light_source": False,
        }
        self.save_dialog = None
        self.preset_save_verified = {
            index: False for index in range(len(INTEGRATION_PRESET_NAMES))
        }
        self.save_verified = False
        self.pl_row_variables: dict[str, dict[str, Any]] = {}
        self.ab_row_variables: dict[str, dict[str, Any]] = {}
        self.ab_group_header_labels: dict[str, Any] = {}
        self.ab_group_widgets: dict[str, dict[str, Any]] = {}
        self.workspace_view_bounds: dict[str, tuple[float | None, float | None, float | None, float | None]] = {}
        self._active_workspace: str | None = None
        self.single_capture_candidates: dict[int, SingleCaptureSnapshot] = {}
        self.single_capture_history: dict[int, list[SingleCaptureSnapshot]] = {
            index: [] for index in range(len(INTEGRATION_PRESET_NAMES))
        }
        self._next_single_capture_id = 1
        self._displayed_single_capture_id: int | None = None
        self._show_single_capture_history = False

        preferences = self.load_preferences()
        self.curve_export_directory = str(preferences.get("curve_export_directory", "") or "")
        self.curve_export_verified = {}
        # TXT matches the OceanView-compatible format expected by the Win7
        # deployment.  Keep an explicitly saved legacy CSV preference intact.
        save_format = preferences.get("save_format", SAVE_FORMAT_TXT)
        if save_format not in SAVE_FORMAT_OPTIONS:
            save_format = SAVE_FORMAT_TXT
        suffix_mode = preferences.get("suffix_mode", SAVE_SUFFIX_TIME)
        if suffix_mode not in (SAVE_SUFFIX_TIME, SAVE_SUFFIX_SEQUENCE, SAVE_SUFFIX_NONE):
            suffix_mode = SAVE_SUFFIX_TIME
        try:
            self.save_sequence = max(1, int(preferences.get("sequence", 1)))
        except (TypeError, ValueError):
            self.save_sequence = 1
        try:
            sequence_digits = min(9, max(1, int(preferences.get("sequence_digits", 5))))
        except (TypeError, ValueError):
            sequence_digits = 5
        integration_unit = str(preferences.get("integration_unit", "ms"))
        if integration_unit not in ("ms", "s"):
            integration_unit = "ms"
        try:
            integration_value = Decimal(str(preferences.get("integration_value", "100")))
            if integration_value <= 0:
                raise ValueError
            integration_text = self.decimal_text(integration_value)
        except (InvalidOperation, TypeError, ValueError):
            integration_text = "100"
            integration_unit = "ms"
        try:
            averages_value = min(1000, max(1, int(preferences.get("averages", 1))))
        except (TypeError, ValueError):
            averages_value = 1

        has_existing_name_settings = "base_name" in preferences
        base_name_text = str(preferences.get("base_name", "spectrum"))
        name_prefix_text = str(preferences.get("name_prefix", ""))
        name_suffix_text = str(
            preferences.get(
                "name_suffix",
                "" if has_existing_name_settings else DEFAULT_NAME_SUFFIX,
            )
        )
        position_text = str(preferences.get("position", "")).strip()
        if position_text and not position_text.isdigit():
            position_text = "01"
        fallback_preset = {
            "integration_value": integration_text,
            "integration_unit": integration_unit,
            "averages": str(averages_value),
            "base_name": base_name_text,
            "name_prefix": name_prefix_text,
            "name_suffix": name_suffix_text,
            "position": position_text,
            "save_format": save_format,
            "save_directory": str(preferences.get("save_directory", "")),
            "suffix_mode": suffix_mode,
            "sequence_digits": str(sequence_digits),
        }
        acquisition_presets: list[dict[str, str]] = []
        saved_presets = preferences.get("acquisition_presets", preferences.get("integration_presets", []))
        if isinstance(saved_presets, list):
            for item in saved_presets[: len(INTEGRATION_PRESET_NAMES)]:
                if not isinstance(item, dict):
                    continue
                # Versions through 0.2.1 stored only {value, unit}.  Fill the
                # newly bound acquisition/save fields from the global values.
                unit = str(item.get("integration_unit", item.get("unit", "ms")))
                try:
                    value = Decimal(str(item.get("integration_value", item.get("value", ""))))
                    if value <= 0 or unit not in ("ms", "s"):
                        raise ValueError
                except (InvalidOperation, TypeError, ValueError):
                    continue
                try:
                    preset_averages = min(1000, max(1, int(item.get("averages", averages_value))))
                    preset_digits = min(9, max(1, int(item.get("sequence_digits", sequence_digits))))
                except (TypeError, ValueError):
                    continue
                preset_format = str(item.get("save_format", save_format))
                if preset_format not in SAVE_FORMAT_OPTIONS:
                    preset_format = save_format
                preset_suffix = str(item.get("suffix_mode", suffix_mode))
                if preset_suffix not in (SAVE_SUFFIX_TIME, SAVE_SUFFIX_SEQUENCE, SAVE_SUFFIX_NONE):
                    preset_suffix = suffix_mode
                acquisition_presets.append(
                    {
                        "integration_value": self.decimal_text(value),
                        "integration_unit": unit,
                        "averages": str(preset_averages),
                        "base_name": str(item.get("base_name", base_name_text)),
                        "name_prefix": str(item.get("name_prefix", name_prefix_text)),
                        "name_suffix": str(item.get("name_suffix", name_suffix_text)),
                        "position": str(item.get("position", position_text)),
                        "save_format": preset_format,
                        "save_directory": str(item.get("save_directory", preferences.get("save_directory", ""))),
                        "suffix_mode": preset_suffix,
                        "sequence_digits": str(preset_digits),
                    }
                )
        while len(acquisition_presets) < len(INTEGRATION_PRESET_NAMES):
            acquisition_presets.append(fallback_preset.copy())
        try:
            selected_preset = int(preferences.get("selected_integration_preset", 0))
        except (TypeError, ValueError):
            selected_preset = 0
        selected_preset = min(len(INTEGRATION_PRESET_NAMES) - 1, max(0, selected_preset))
        active_preset = acquisition_presets[selected_preset]
        integration_text = active_preset["integration_value"]
        integration_unit = active_preset["integration_unit"]
        averages_value = int(active_preset["averages"])
        base_name_text = active_preset["base_name"]
        name_prefix_text = active_preset["name_prefix"]
        name_suffix_text = active_preset["name_suffix"]
        position_text = active_preset["position"] if active_preset["position"].isdigit() else ""
        share_variable_name = bool(preferences.get("share_variable_name", False))
        shared_base_name_text = str(preferences.get("shared_base_name", base_name_text))
        if share_variable_name:
            base_name_text = shared_base_name_text
        save_format = active_preset["save_format"]
        suffix_mode = active_preset["suffix_mode"]
        sequence_digits = int(active_preset["sequence_digits"])

        self.status_var = tk.StringVar(value="正在加载 OceanDirect SDK…也可先使用模拟模式")
        self.device_var = tk.StringVar(value="")
        self.run_state_var = tk.StringVar(value="采集未运行")
        self.single_progress_var = tk.DoubleVar(value=0.0)
        self.integration_var = tk.StringVar(value=integration_text)
        self.integration_unit_var = tk.StringVar(value=integration_unit)
        self._integration_unit_last = integration_unit
        self.acquisition_presets = acquisition_presets
        self._active_integration_preset = selected_preset
        self.integration_preset_var = tk.StringVar(value=INTEGRATION_PRESET_NAMES[selected_preset])
        self.averages_var = tk.StringVar(value=str(averages_value))
        self.interval_var = tk.StringVar(value="80")
        self.boxcar_var = tk.StringVar(value="0")
        self.trigger_mode_var = tk.StringVar(value=TRIGGER_MODE_OPTIONS[0])
        self.x_axis_var = tk.StringVar(value="波长 (nm)")
        workspace = str(preferences.get("workspace", "采集"))
        if workspace == "PL":
            workspace = "PL荧光"
        self.initial_workspace = workspace if workspace in ("采集", "处理", "PL荧光", "AB吸收") else "采集"
        processing_mode = normalize_tauc_mode(str(preferences.get("processing_mode", "原始强度")))
        if processing_mode not in (
            "原始强度",
            "峰值归一化",
            "透过率 (%)",
            "吸光度 (A)",
            *TAUC_OPTIONS,
        ):
            processing_mode = "原始强度"
        ab_mode = normalize_tauc_mode(str(preferences.get("ab_mode", "吸光度 (A)")))
        if ab_mode not in ("透过率 (%)", "吸光度 (A)", *TAUC_OPTIONS):
            ab_mode = "吸光度 (A)"
        ab_x_axis = str(preferences.get("ab_x_axis", "波长 (nm)"))
        if ab_x_axis not in ("波长 (nm)", "光子能量 (eV)"):
            ab_x_axis = "波长 (nm)"
        self.mode_var = tk.StringVar(value=processing_mode)
        self.pl_normalize_var = tk.BooleanVar(value=bool(preferences.get("pl_normalize", False)))
        self.pl_turbo_var = tk.BooleanVar(value=bool(preferences.get("pl_turbo", False)))
        self.pl_active_var = tk.StringVar(value="")
        self.ab_active_var = tk.StringVar(value="")
        self.ab_group_var = tk.StringVar(value=self.historical_absorbance_service.current_group.name)
        self.ab_action_status_var = tk.StringVar(value="")
        self.ab_mode_var = tk.StringVar(value=ab_mode)
        self.ab_x_axis_var = tk.StringVar(value=ab_x_axis)
        self.xmin_var = tk.StringVar(value="")
        self.xmax_var = tk.StringVar(value="")
        self.range_name_var = tk.StringVar(value="波段")
        self.range_unit_var = tk.StringVar(value="nm")
        self.electric_dark_var = tk.BooleanVar(value=True)
        self.nonlinearity_var = tk.BooleanVar(value=False)
        self.decouple_var = tk.BooleanVar(value=False)
        self.light_source_var = tk.BooleanVar(value=False)
        self.advanced_capability_var = tk.StringVar(value="连接设备后检测触发、校正和光源能力。")
        self.metrics_var = tk.StringVar(value="峰值: —    峰位: —    帧率: —")
        self.peak_metric_var = tk.StringVar(value="峰值: —")
        self.position_metric_var = tk.StringVar(value="峰位: —")
        self.frame_metric_var = tk.StringVar(value="帧率: —")
        self.peak_value_var = tk.StringVar(value="—")
        self.position_value_var = tk.StringVar(value="—")
        self.frame_value_var = tk.StringVar(value="—")
        self.single_history_warning_var = tk.StringVar(value="")
        self.cursor_var = tk.StringVar(value="光标: —")
        self.cursor_position_var = tk.StringVar(value="")
        self.cursor_tool_name_var = tk.StringVar(value="数据游标")
        self.cursor_axis_name_var = tk.StringVar(value="波长")
        self.cursor_unit_var = tk.StringVar(value="nm")
        self.sdk_footer_var = tk.StringVar(
            value=f"SDK: {self.sdk_home}" if self.sdk_validator(self.sdk_home) else "SDK: 未检测到 OceanDirect"
        )
        self.saturation_var = tk.StringVar(value="饱和度: —")
        self.settings_state_var = tk.StringVar(value="连接仪器或进入模拟模式后可应用参数")
        self.baseline_state_var = tk.StringVar(value="参考谱：未记录")
        self.pl_status_var = tk.StringVar(value="尚未选择 PL 文件")
        self.pl_background_status_var = tk.StringVar(value="未选择（显示原始 PL）")
        self.ab_reference_status_var = tk.StringVar(value="未选择")
        self.ab_result_status_var = tk.StringVar(value="请选择样品信号和 BG 参考")
        self.ab_offset_status_var = tk.StringVar(value="未应用信号偏移")
        self.ab_offset_dialog = None
        self.base_name_var = tk.StringVar(value=base_name_text)
        self.share_variable_name_var = tk.BooleanVar(value=share_variable_name)
        self.shared_base_name = shared_base_name_text
        self.name_prefix_var = tk.StringVar(value=name_prefix_text)
        self.name_suffix_var = tk.StringVar(value=name_suffix_text)
        self.position_var = tk.StringVar(value=position_text)
        self.save_format_var = tk.StringVar(value=save_format)
        self.save_directory_var = tk.StringVar(value=active_preset["save_directory"])
        self.save_suffix_var = tk.StringVar(value=suffix_mode)
        self.sequence_digits_var = tk.StringVar(value=str(sequence_digits))
        self.save_button_text_var = tk.StringVar(value="保存 CSV" if save_format == SAVE_FORMAT_CSV else "保存 TXT")
        self.save_context_var = tk.StringVar(value="")
        self._syncing_settings = False
        self.controls_enabled = False

        self.configure_style()
        self.build_ui()
        for variable in (self.integration_var, self.averages_var, self.interval_var, self.boxcar_var, self.trigger_mode_var):
            variable.trace_add("write", self.on_setting_changed)
        for variable in (
            self.base_name_var,
            self.name_prefix_var,
            self.name_suffix_var,
            self.position_var,
            self.save_format_var,
            self.save_directory_var,
            self.save_suffix_var,
            self.sequence_digits_var,
        ):
            variable.trace_add("write", self.mark_save_pending)
        self.set_controls(False)
        self.update_connection_buttons()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.report_callback_exception = self.report_callback_exception
        self.root.after(120, self.show_window)
        self.root.after(50, self.poll_events)

    def load_preferences(self) -> dict[str, Any]:
        return self.preferences_repository.load()

    def save_preferences(self) -> None:
        self.commit_current_acquisition_preset()
        data = {
            "base_name": self.base_name_var.get().strip(),
            "share_variable_name": bool(self.share_variable_name_var.get()),
            "shared_base_name": self.shared_base_name,
            "name_prefix": self.name_prefix_var.get().strip(),
            "name_suffix": self.name_suffix_var.get().strip(),
            "position": self.position_var.get().strip(),
            "save_format": self.save_format_var.get(),
            "save_directory": self.save_directory_var.get().strip(),
            "curve_export_directory": self.curve_export_directory,
            "suffix_mode": self.save_suffix_var.get(),
            "sequence_digits": self.sequence_digits_var.get(),
            "sequence": self.save_sequence,
            "integration_value": self.integration_var.get().strip(),
            "integration_unit": self.integration_unit_var.get(),
            "acquisition_presets": self.acquisition_presets,
            "selected_integration_preset": self._active_integration_preset,
            "averages": self.averages_var.get().strip(),
            "workspace": self.current_workspace() if hasattr(self, "sidebar_notebook") else self.initial_workspace,
            "processing_mode": self.mode_var.get(),
            "pl_normalize": bool(self.pl_normalize_var.get()),
            "pl_turbo": bool(self.pl_turbo_var.get()),
            "ab_mode": self.ab_mode_var.get(),
            "ab_x_axis": self.ab_x_axis_var.get(),
            "sdk_home": str(self.sdk_home) if self.sdk_validator(self.sdk_home) else "",
        }
        try:
            self.preferences_repository.save(data)
        except OSError:
            logger.exception("Could not save OceanLab preferences")

    def show_window(self) -> None:
        """Bring the main window to the foreground after Windows finishes creating it."""
        try:
            self.root.deiconify()
            self.root.state("zoomed" if self.compact_layout else "normal")
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(500, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
            logger.info("Main window shown, state=%s", self.root.state())
        except Exception:
            logger.exception("Could not bring main window to foreground")

    def report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        from tkinter import messagebox

        logger.exception("Tk callback failed", exc_info=(exc_type, exc_value, exc_traceback))
        messagebox.showerror("OceanLab 程序错误", f"{exc_type.__name__}: {exc_value}\n\n日志：{LOG_FILE}")

    def configure_style(self) -> None:
        self.root.title(f"OceanLab {APP_VERSION} 光谱采集")
        dpi_scale = max(1.0, min(3.0, float(self.root.winfo_fpixels("1i")) / 96.0))
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.compact_layout = screen_width <= 1440 or screen_height <= 800
        if self.compact_layout:
            # Windows 7 lab machines commonly run at 1366x768.  Use almost all of
            # the work area and let show_window maximize around the taskbar.
            initial_width = max(1024, screen_width - round(24 * dpi_scale))
            initial_height = max(620, screen_height - round(72 * dpi_scale))
            minimum_width = min(1120, initial_width)
            minimum_height = min(620, initial_height)
        else:
            initial_width = min(round(1630 * dpi_scale), max(1080, screen_width - round(64 * dpi_scale)))
            initial_height = min(round(820 * dpi_scale), max(680, screen_height - round(96 * dpi_scale)))
            minimum_width = min(round(1350 * dpi_scale), max(900, screen_width - round(32 * dpi_scale)))
            minimum_height = min(round(680 * dpi_scale), max(600, screen_height - round(64 * dpi_scale)))
        self.root.geometry(f"{initial_width}x{initial_height}")
        self.root.minsize(minimum_width, minimum_height)
        self.root.configure(bg="#f3f6f9")
        style = self.ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f6f9")
        style.configure("Header.TFrame", background="#ffffff")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f3f6f9", foreground="#172033", font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#172033", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background="#ffffff", foreground="#0f172a", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("PresetHint.TLabel", background="#ffffff", foreground="#16a34a", font=("Microsoft YaHei UI", 14))
        style.configure("Sub.TLabel", background="#ffffff", foreground="#526174", font=("Microsoft YaHei UI", 9))
        style.configure("Footer.TLabel", background="#f3f6f9", foreground="#526174", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#ffffff", foreground="#0f172a", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#526174", font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(12, 8), foreground="#ffffff", background="#0f6cbd")
        style.map("Primary.TButton", background=[("active", "#115ea3"), ("disabled", "#a8b5c4")])
        style.configure(
            "Saved.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(12, 8),
            foreground="#ffffff",
            background="#198754",
            bordercolor="#146c43",
            lightcolor="#146c43",
            darkcolor="#146c43",
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "Saved.TButton",
            background=[("active", "#146c43"), ("disabled", "#77b99a")],
            foreground=[("disabled", "#ffffff")],
        )
        style.configure(
            "Selected.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(10, 7),
            foreground="#ffffff",
            background="#198754",
            bordercolor="#146c43",
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "Selected.TButton",
            background=[("active", "#146c43"), ("disabled", "#77b99a")],
            foreground=[("disabled", "#ffffff")],
        )
        style.configure(
            "BaselineEmpty.TLabel",
            background="#f6f8fb",
            foreground="#526174",
            bordercolor="#cbd5e1",
            borderwidth=1,
            relief="solid",
            padding=(8, 6),
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "BaselinePartial.TLabel",
            background="#fff4ce",
            foreground="#7a3e00",
            bordercolor="#d97706",
            borderwidth=1,
            relief="solid",
            padding=(8, 6),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "BaselineReady.TLabel",
            background="#e7f6ec",
            foreground="#176b3a",
            bordercolor="#198754",
            borderwidth=1,
            relief="solid",
            padding=(8, 6),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "ConnectIdle.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(12, 8),
            foreground="#172033",
            background="#ffffff",
            bordercolor="#26364a",
            lightcolor="#26364a",
            darkcolor="#26364a",
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "ConnectIdle.TButton",
            background=[("active", "#eef2f6"), ("disabled", "#eef2f6")],
            foreground=[("disabled", "#8b98a7")],
            bordercolor=[("disabled", "#a8b5c4")],
        )
        style.configure(
            "Connecting.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(12, 8),
            foreground="#7a3e00",
            background="#fde7a7",
            bordercolor="#d97706",
            lightcolor="#d97706",
            darkcolor="#d97706",
            borderwidth=1,
            relief="solid",
        )
        style.map("Connecting.TButton", background=[("disabled", "#fde7a7")], foreground=[("disabled", "#7a3e00")])
        style.configure(
            "Connected.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(12, 8),
            foreground="#ffffff",
            background="#198754",
            bordercolor="#146c43",
            lightcolor="#146c43",
            darkcolor="#146c43",
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "Connected.TButton",
            background=[("active", "#146c43"), ("disabled", "#77b99a")],
            foreground=[("disabled", "#ffffff")],
        )
        style.configure(
            "Simulation.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(12, 8),
            foreground="#7a3e00",
            background="#fff4ce",
        )
        style.map(
            "Simulation.TButton",
            background=[("active", "#fde7a7"), ("disabled", "#eef2f6")],
            foreground=[("disabled", "#8b98a7")],
        )
        style.configure("Stop.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(10, 8), foreground="#b42318", background="#fff1f0")
        style.map("Stop.TButton", background=[("active", "#fee4e2"), ("disabled", "#eef2f6")], foreground=[("disabled", "#8b98a7")])
        style.configure("Tool.TButton", font=("Microsoft YaHei UI", 9), padding=(5, 5), foreground="#26364a", background="#eef2f6")
        style.map("Tool.TButton", background=[("active", "#dde7f0")])
        style.configure(
            "PlotTool.TButton",
            font=("Segoe MDL2 Assets", 14 if self.compact_layout else 22),
            padding=(3, 2) if self.compact_layout else (10, 7),
            foreground="#26364a",
            background="#f8fafc",
            bordercolor="#cbd5e1",
            borderwidth=1,
        )
        style.map("PlotTool.TButton", background=[("active", "#e5edf5")], bordercolor=[("active", "#94a3b8")])
        style.configure("Saved.PlotTool.TButton", background="#198754", bordercolor="#146c43")
        style.map("Saved.PlotTool.TButton", background=[("active", "#146c43")], bordercolor=[("active", "#146c43")])
        style.configure(
            "PlotToolActive.TButton",
            font=("Segoe MDL2 Assets", 14 if self.compact_layout else 22),
            padding=(3, 2) if self.compact_layout else (10, 7),
            foreground="#ffffff",
            background="#0f6cbd",
            bordercolor="#0f6cbd",
            borderwidth=1,
        )
        style.map("PlotToolActive.TButton", background=[("active", "#115ea3")])
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 7))
        style.configure("History.TButton", font=("Microsoft YaHei UI", 9), padding=(6, 3))
        style.configure("History.TMenubutton", font=("Microsoft YaHei UI", 9), padding=(6, 3))
        for kind, color in (("Primary", "#0f6cbd"), ("Selected", "#198754")):
            name = kind + ".History.TButton"
            style.configure(name, font=("Microsoft YaHei UI", 9), padding=(6, 3),
                            background=color, foreground="#ffffff", borderwidth=1)
            style.map(name, background=[("active", "#115ea3" if kind == "Primary" else "#146c43"),
                                        ("disabled", "#a8b5c4")], foreground=[("disabled", "#ffffff")])
        style.configure("History.TCombobox", padding=2, font=("Microsoft YaHei UI", 9))
        style.configure("History.TCheckbutton", padding=(0, 2), font=("Microsoft YaHei UI", 9))
        style.configure("HistoryName.TLabel", background="#ffffff", foreground="#172033",
                        font=("Microsoft YaHei UI", 9), padding=(0, 3))
        style.configure("ABGroupName.TLabel", background="#f4f7fb", foreground="#172033",
                        font=("Microsoft YaHei UI", 9), padding=(0, 3))
        for name, color in (("ABGroupHeader.TLabel", "#e8eef5"), ("Selected.ABGroupHeader.TLabel", "#dceafb")):
            style.configure(name, background=color, foreground="#243b55", font=("Microsoft YaHei UI", 9, "bold"), padding=0)
        input_padding = 2 if self.compact_layout else 5
        input_font = ("Microsoft YaHei UI", 9 if self.compact_layout else 10)
        style.configure("TEntry", padding=input_padding, font=input_font)
        style.configure("TSpinbox", padding=input_padding, font=input_font)
        style.configure("TCombobox", padding=input_padding, font=input_font)
        style.configure(
            "FooterTool.TButton",
            font=("Microsoft YaHei UI", 8),
            padding=(4, 1),
            foreground="#26364a",
            background="#eef2f6",
        )
        style.map("FooterTool.TButton", background=[("active", "#dde7f0")])
        style.configure("TCheckbutton", background="#ffffff", font=("Microsoft YaHei UI", 9))
        style.configure("TRadiobutton", background="#ffffff", foreground="#172033", font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#cbd5e1", borderwidth=1, relief="solid")
        style.configure(
            "Card.TLabelframe.Label",
            background="#ffffff",
            foreground="#0f172a",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure(
            "Preview.TLabel",
            background="#f6f8fb",
            foreground="#334155",
            bordercolor="#cbd5e1",
            borderwidth=1,
            relief="solid",
            padding=(9, 7),
            font=("Segoe UI", 9),
        )
        style.configure("Sidebar.TNotebook", background="#ffffff", borderwidth=0)
        style.configure(
            "Sidebar.TNotebook.Tab",
            background="#e8edf3",
            foreground="#415166",
            font=("Microsoft YaHei UI", 10),
            padding=(12, 6) if self.compact_layout else (18, 8),
        )
        style.map(
            "Sidebar.TNotebook.Tab",
            background=[("selected", "#ffffff"), ("active", "#f4f7fa")],
            foreground=[("selected", "#0f172a")],
        )
        style.configure("Metric.TLabel", background="#f6f8fb", foreground="#334155", font=("Microsoft YaHei UI", 9), padding=(8, 4))
        style.configure("MetricCompact.TFrame", background="#f6f8fb")
        style.configure(
            "MetricCaption.TLabel",
            background="#f6f8fb",
            foreground="#334155",
            font=("Microsoft YaHei UI", 9),
            padding=(4, 4, 1, 4),
        )
        style.configure(
            "MetricValue.TLabel",
            background="#f6f8fb",
            foreground="#172033",
            font=("Microsoft YaHei UI", 9),
            padding=(1, 4, 3, 4),
        )
        style.configure("Cursor.TLabel", background="#ffffff", foreground="#c21875", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("MetricSafe.TLabel", background="#e7f6ec", foreground="#176b3a", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 4))
        style.configure("MetricWarn.TLabel", background="#fff4ce", foreground="#8a4b08", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 4))
        style.configure("MetricDanger.TLabel", background="#fee4e2", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 4))
        style.configure(
            "DeviceOff.TLabel",
            background="#ffffff",
            foreground="#172033",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 4),
            bordercolor="#26364a",
            lightcolor="#26364a",
            darkcolor="#26364a",
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "DevicePending.TLabel",
            background="#fde7a7",
            foreground="#7a3e00",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 4),
            bordercolor="#d97706",
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "DeviceOn.TLabel",
            background="#198754",
            foreground="#ffffff",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 4),
            bordercolor="#146c43",
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "DeviceSim.TLabel",
            background="#fff4ce",
            foreground="#7a3e00",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 4),
            bordercolor="#d97706",
            borderwidth=1,
            relief="solid",
        )
        style.configure("RunIdle.TLabel", background="#ffffff", foreground="#526174", font=("Microsoft YaHei UI", 9))
        style.configure("RunActive.TLabel", background="#ffffff", foreground="#176b3a", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("RunPending.TLabel", background="#ffffff", foreground="#0f6cbd", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("RunError.TLabel", background="#ffffff", foreground="#b42318", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure(
            "Acquisition.Horizontal.TProgressbar",
            troughcolor="#e5eaf0",
            background="#0f6cbd",
            bordercolor="#cbd5e1",
            lightcolor="#0f6cbd",
            darkcolor="#0f6cbd",
            thickness=16,
        )
        style.configure(
            "AcquisitionComplete.Horizontal.TProgressbar",
            troughcolor="#e5eaf0",
            background="#198754",
            bordercolor="#cbd5e1",
            lightcolor="#198754",
            darkcolor="#198754",
            thickness=16,
        )

    def build_ui(self) -> None:
        root = self.root
        build_header(self, root)

        body_padding = (8, 6, 8, 4) if self.compact_layout else (14, 12, 14, 10)
        panel_padding = 7 if self.compact_layout else 12
        # Keep the same visual proportion as the desktop layout: the sidebar
        # occupies roughly 30% at 1366 px instead of crowding the plot.
        sidebar_width = 400 if self.compact_layout else 570
        sidebar_gap = 7 if self.compact_layout else 12
        page_padding = 8 if self.compact_layout else 14
        body = self.ttk.Frame(root, padding=body_padding)
        body.pack(fill="both", expand=True)
        sidebar = self.ttk.Frame(body, style="Card.TFrame", width=sidebar_width)
        sidebar.pack(side="left", fill="y", padx=(0, sidebar_gap))
        sidebar.pack_propagate(False)
        main = self.ttk.Frame(body, style="Card.TFrame", padding=panel_padding)
        main.pack(side="left", fill="both", expand=True)

        save_padding = (8, 4, 8, 7) if self.compact_layout else (14, 8, 14, 12)
        sidebar_save_panel = self.ttk.Frame(sidebar, style="Card.TFrame", padding=save_padding)
        sidebar_save_panel.pack(side="bottom", fill="x", padx=1, pady=(0, 1))
        self.sidebar_save_panel = sidebar_save_panel

        notebook = self.ttk.Notebook(sidebar, style="Sidebar.TNotebook")
        notebook.pack(fill="both", expand=True, padx=1, pady=1)
        acquisition_page = self.ttk.Frame(notebook, style="Card.TFrame")
        acquisition_canvas = self.tk.Canvas(
            acquisition_page,
            background="#ffffff",
            highlightthickness=0,
            borderwidth=0,
        )
        acquisition_scrollbar = self.ttk.Scrollbar(
            acquisition_page,
            orient="vertical",
            command=acquisition_canvas.yview,
        )

        def update_acquisition_scrollbar(first: str, last: str) -> None:
            acquisition_scrollbar.set(first, last)
            needs_scrollbar = float(first) > 0.0 or float(last) < 0.999
            is_managed = bool(acquisition_scrollbar.winfo_manager())
            if needs_scrollbar and not is_managed:
                acquisition_scrollbar.pack(side="right", fill="y", before=acquisition_canvas)
            elif not needs_scrollbar and is_managed:
                acquisition_scrollbar.pack_forget()

        acquisition_canvas.configure(yscrollcommand=update_acquisition_scrollbar)
        acquisition_scrollbar.pack(side="right", fill="y")
        acquisition_canvas.pack(side="left", fill="both", expand=True)
        acquisition_tab = self.ttk.Frame(acquisition_canvas, style="Card.TFrame", padding=page_padding)
        acquisition_window = acquisition_canvas.create_window((0, 0), window=acquisition_tab, anchor="nw")
        acquisition_tab.bind(
            "<Configure>",
            lambda _event: acquisition_canvas.configure(scrollregion=acquisition_canvas.bbox("all")),
        )
        acquisition_canvas.bind(
            "<Configure>",
            lambda event: acquisition_canvas.itemconfigure(acquisition_window, width=event.width),
        )

        def scroll_acquisition(event) -> None:
            if not self.advanced_expanded:
                return "break"
            acquisition_canvas.yview_scroll(int(-event.delta / 120), "units")
            return "break"

        self.scroll_acquisition = scroll_acquisition

        acquisition_page.bind(
            "<Enter>",
            lambda _event: self.root.bind_all("<MouseWheel>", scroll_acquisition),
        )
        acquisition_page.bind(
            "<Leave>",
            lambda _event: self.root.unbind_all("<MouseWheel>"),
        )
        processing_tab = self.ttk.Frame(notebook, style="Card.TFrame", padding=page_padding)
        pl_tab = self.ttk.Frame(notebook, style="Card.TFrame", padding=page_padding)
        absorbance_tab = self.ttk.Frame(notebook, style="Card.TFrame", padding=page_padding)
        self.sidebar_notebook = notebook
        self.acquisition_tab = acquisition_page
        self.acquisition_canvas = acquisition_canvas
        self.processing_tab = processing_tab
        self.pl_tab = pl_tab
        self.absorbance_tab = absorbance_tab
        notebook.add(acquisition_page, text="采集")
        notebook.add(processing_tab, text="处理")
        notebook.add(pl_tab, text="PL荧光")
        notebook.add(absorbance_tab, text="AB吸收")

        build_acquisition_workspace(self, acquisition_tab)
        build_processing_workspace(self, processing_tab)
        build_pl_workspace(self, pl_tab)
        build_absorbance_workspace(self, absorbance_tab)

        save_controls = sidebar_save_panel
        self.save_controls = save_controls
        self.ttk.Separator(save_controls).pack(fill="x", pady=(0, 9))
        name_labels = self.ttk.Frame(save_controls, style="Card.TFrame")
        name_labels.pack(fill="x", pady=(0, 5))
        self.ttk.Label(name_labels, text="当前变量", style="Card.TLabel").pack(side="left")
        shared_variable_check = LargeCheckbutton(
            name_labels,
            text="共用",
            variable=self.share_variable_name_var,
            command=self.on_share_variable_name_changed,
        )
        if self.compact_layout:
            shared_variable_check.configure(padx=0, pady=0)
            shared_variable_check.box.configure(width=22, height=22)
            shared_variable_check.box.coords(shared_variable_check.box_outline, 3, 3, 19, 19)
            shared_variable_check.box.coords(shared_variable_check.box_check, 6, 11, 10, 15, 17, 7)
            shared_variable_check.text_label.configure(font=("Microsoft YaHei UI", 9), padx=1)
        self.shared_variable_check = shared_variable_check
        shared_variable_check.pack(side="left", padx=(8, 0))
        self.shared_variable_box = shared_variable_check.box
        self.shared_variable_box_outline = shared_variable_check.box_outline
        self.shared_variable_box_check = shared_variable_check.box_check
        self.shared_variable_text = shared_variable_check.text_label
        self.ttk.Label(name_labels, text="末尾编号", style="Card.TLabel").pack(side="right", padx=(0, 7))
        base_name_shell = self.tk.Frame(
            save_controls,
            width=1,
            height=40 if self.compact_layout else 54,
            background="#ffffff",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
            highlightcolor="#0f6cbd",
        )
        self.base_name_shell = base_name_shell
        base_name_shell.pack(fill="x")
        base_name_shell.grid_propagate(False)
        base_name_shell.columnconfigure(0, weight=1)
        base_name_shell.columnconfigure(2, weight=0, minsize=82)
        base_name_shell.columnconfigure(4, weight=0, minsize=38)
        base_name_shell.rowconfigure((0, 2), weight=1, uniform="base_name_stepper")
        self.base_name_entry = self.tk.Entry(
            base_name_shell,
            textvariable=self.base_name_var,
            width=36,
            font=("Microsoft YaHei UI", 9 if self.compact_layout else 11),
            background="#ffffff",
            foreground="#172033",
            insertbackground="#172033",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.base_name_entry.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(7, 6), pady=1)
        self.tk.Frame(base_name_shell, width=1, background="#94a3b8").grid(
            row=0, column=1, rowspan=3, sticky="ns"
        )
        self.position_entry = self.tk.Entry(
            base_name_shell,
            textvariable=self.position_var,
            width=6,
            justify="center",
            font=("Microsoft YaHei UI", 9 if self.compact_layout else 11),
            background="#ffffff",
            foreground="#172033",
            insertbackground="#172033",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.position_entry.grid(row=0, column=2, rowspan=3, sticky="nsew", padx=5, pady=1)
        self.tk.Frame(base_name_shell, width=1, background="#94a3b8").grid(
            row=0, column=3, rowspan=3, sticky="ns"
        )
        base_name_arrow_options = {
            "master": base_name_shell,
            "width": 4,
            "font": ("Segoe UI Symbol", 8, "bold"),
            "foreground": "#183b66",
            "background": "#eef2f6",
            "activeforeground": "#0f4c81",
            "activebackground": "#dbe7f3",
            "relief": "flat",
            "borderwidth": 0,
            "highlightthickness": 0,
            "takefocus": True,
            "repeatdelay": 400,
            "repeatinterval": 100,
        }
        self.position_up_button = self.tk.Button(
            text="▲", command=lambda: self.adjust_position_number(1), **base_name_arrow_options
        )
        self.position_up_button.grid(row=0, column=4, sticky="nsew")
        self.tk.Frame(base_name_shell, height=1, background="#94a3b8").grid(row=1, column=4, sticky="ew")
        self.position_down_button = self.tk.Button(
            text="▼", command=lambda: self.adjust_position_number(-1), **base_name_arrow_options
        )
        self.position_down_button.grid(row=2, column=4, sticky="nsew")
        self.position_entry.bind("<Up>", lambda _event: (self.adjust_position_number(1), "break")[1])
        self.position_entry.bind("<Down>", lambda _event: (self.adjust_position_number(-1), "break")[1])

        save_button_row = self.ttk.Frame(save_controls, style="Card.TFrame")
        save_button_row.pack(fill="x", pady=(7, 0))
        save_button_row.columnconfigure(0, weight=0)
        save_button_row.columnconfigure(1, weight=1)
        self.save_settings_button = self.ttk.Button(
            save_button_row,
            text="保存设置",
            style="Tool.TButton",
            command=self.open_save_settings,
        )
        self.save_settings_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.save_button = self.ttk.Button(
            save_button_row,
            textvariable=self.save_button_text_var,
            style="Primary.TButton",
            command=self.save_csv,
        )
        self.save_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.ttk.Label(
            save_controls,
            textvariable=self.save_context_var,
            style="Hint.TLabel",
            wraplength=500,
        ).pack(anchor="w", fill="x", pady=(5, 0))

        toolbar = self.ttk.Frame(main, style="Card.TFrame")
        self.plot_toolbar = toolbar
        toolbar.pack(fill="x", padx=2, pady=(0, 4 if self.compact_layout else 8))
        self.plot_tool_buttons = {}
        self.plot_toolbar_images = []
        plot_tools = (
            ("缩到最小", "zoom_out_to_max_128px.png", "\ue9a6", self.reset_plot_view, None),
            ("合适的大小", "fit_to_window_128px.png", "\ue759", self.fit_plot_both, None),
            ("合适的高度", "scale_height_128px.png", "\ue746", self.fit_plot_height, None),
            ("手动设置数值范围", "numeric_range_128px.png", "\ue8ef", self.open_plot_range_dialog, None),
            ("放大", "zoom_in_128px.png", "\ue8a3", lambda: self.toggle_plot_mode("zoom_in"), "zoom_in"),
            ("缩小", "zoom_out_128px.png", "\ue71f", lambda: self.toggle_plot_mode("zoom_out"), "zoom_out"),
            ("手动选择缩放范围", "manual_zoom128px.png", "\ue71e", lambda: self.toggle_plot_mode("select"), "select"),
            ("打开/关闭拖拽模式", "drag_128px.png", "\ue815", lambda: self.toggle_plot_mode("drag"), "drag"),
            ("添加并拖动十字架", "crosshair_128px.png", "⌖", lambda: self.toggle_plot_mode("crosshair"), "crosshair"),
        )
        for tooltip, icon_file, fallback_icon, command, mode in plot_tools:
            icon = self.load_oceanview_toolbar_icon(icon_file)
            if icon is not None:
                self.plot_toolbar_images.append(icon)
                button = self.ttk.Button(toolbar, image=icon, style="PlotTool.TButton", command=command)
            else:
                button = self.ttk.Button(toolbar, text=fallback_icon, width=4, style="PlotTool.TButton", command=command)
            button.pack(side="left", padx=(0, 3 if self.compact_layout else 5))
            self.attach_tooltip(button, tooltip)
            if mode:
                self.plot_tool_buttons[mode] = button

        self.curve_save_icon = self.create_save_toolbar_icon("#111111")
        self.curve_save_verified_icon = self.create_save_toolbar_icon("#ffffff")
        self.plot_toolbar_images.append(self.curve_save_icon)
        self.curve_save_button = self.ttk.Button(
            toolbar, image=self.curve_save_icon, command=self.save_plot_curves, style="PlotTool.TButton",
        )
        self.attach_tooltip(self.curve_save_button, "保存当前显示曲线为两列 TXT（完整数据，保留当前横轴和处理结果）")

        # Keep the legend in the spare toolbar area.  It should never cover
        # spectral data or consume any of the plot's x/y drawing rectangle.
        self.plot_legend_canvas = self.tk.Canvas(
            toolbar,
            width=1,
            height=48 if self.compact_layout else 58,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            borderwidth=0,
        )
        self.plot_legend_canvas.pack(side="left", fill="both", expand=True, padx=(6, 0))

        metrics = self.ttk.Frame(main, style="Card.TFrame")
        self.metrics_bar = metrics
        metrics.pack(fill="x", padx=2, pady=(0, 7))
        def compact_metric(caption: str, variable, value_width: int, background: str):
            if self.compact_layout:
                value_width = max(6, value_width - 1)
            group = self.tk.Frame(
                metrics,
                background=background,
                highlightthickness=1,
                highlightbackground="#cbd5e1",
                borderwidth=0,
                padx=3 if self.compact_layout else 5,
                pady=3,
            )
            group.pack(side="left", padx=(0, 3 if self.compact_layout else 5))
            caption_label = self.tk.Label(
                group,
                text=caption,
                background=background,
                foreground="#334155",
                font=("Microsoft YaHei UI", 9),
                padx=0,
                pady=0,
            )
            caption_label.pack(side="left")
            value_label = self.tk.Label(
                group,
                textvariable=variable,
                background=background,
                foreground="#172033",
                font=("Microsoft YaHei UI", 9, "bold"),
                width=value_width,
                anchor="w",
                padx=2,
                pady=0,
            )
            value_label.pack(side="left")
            return group, value_label

        self.peak_metric_group, self.peak_metric_label = compact_metric(
            "峰值:", self.peak_value_var, 8, "#eef6ff"
        )
        self.position_metric_group, self.position_metric_label = compact_metric(
            "峰位:", self.position_value_var, 10, "#f5f3ff"
        )
        self.frame_metric_group, self.frame_metric_label = compact_metric(
            "帧率:", self.frame_value_var, 9, "#f0fdf4"
        )
        self.saturation_label = self.ttk.Label(
            metrics,
            textvariable=self.saturation_var,
            style="Metric.TLabel",
            anchor="w",
        )
        self.saturation_label.pack(side="left", padx=(2 if self.compact_layout else 3, 0))
        self.single_history_warning_label = self.ttk.Label(
            main,
            textvariable=self.single_history_warning_var,
            style="MetricWarn.TLabel",
            anchor="w",
        )

        canvas = self.tk.Canvas(main, highlightthickness=0, background=SpectrumPlot.BG)
        canvas.pack(fill="both", expand=True)
        self.plot = SpectrumPlot(canvas, legend_canvas=self.plot_legend_canvas)
        if self.compact_layout:
            self.plot.PLOT_LEFT = 82
            self.plot.Y_TITLE_X = 18
        self.plot.crosshair_drag_preview_enabled = self.current_workspace() != "采集"
        self.plot.cursor_callback = self.update_cursor_readout
        self.plot.crosshair_callback = self.update_crosshair_readout
        self.plot.limit_callback = lambda: self.status_var.set("已达到最大显示范围")
        self.plot.mode_callback = self.update_plot_mode_buttons
        self.plot.set_interaction_mode("normal")

        cursor_bar = self.ttk.Frame(metrics, style="Card.TFrame")
        self.cursor_bar = cursor_bar
        if self.compact_layout:
            cursor_bar.pack(side="right", padx=(8, 0))
        else:
            cursor_bar.pack(side="right", padx=(12, 20))
        self.ttk.Label(cursor_bar, textvariable=self.cursor_tool_name_var, style="Card.TLabel").pack(side="left")
        self.ttk.Label(cursor_bar, textvariable=self.cursor_axis_name_var, style="Card.TLabel").pack(
            side="left",
            padx=(8, 4) if self.compact_layout else (14, 5),
        )
        cursor_shell = self.tk.Frame(
            cursor_bar,
            width=145 if self.compact_layout else 190,
            height=27 if self.compact_layout else 48,
            background="#ffffff",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
            highlightcolor="#0f6cbd",
        )
        self.cursor_shell = cursor_shell
        cursor_shell.pack(side="left")
        cursor_shell.grid_propagate(False)
        cursor_shell.columnconfigure(0, weight=1)
        cursor_shell.rowconfigure((0, 2), weight=1, uniform="cursor_stepper")
        self.cursor_position_entry = self.tk.Entry(
            cursor_shell,
            textvariable=self.cursor_position_var,
            justify="right",
            font=("Microsoft YaHei UI", 9 if self.compact_layout else 11),
            background="#ffffff",
            foreground="#172033",
            insertbackground="#172033",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.cursor_position_entry.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(7, 6), pady=1)
        self.tk.Frame(cursor_shell, width=1, background="#94a3b8").grid(row=0, column=1, rowspan=3, sticky="ns")
        cursor_arrow_options = {
            "master": cursor_shell,
            "width": 4,
            "font": ("Segoe UI Symbol", 8, "bold"),
            "foreground": "#183b66",
            "background": "#eef2f6",
            "activeforeground": "#0f4c81",
            "activebackground": "#dbe7f3",
            "relief": "flat",
            "borderwidth": 0,
            "highlightthickness": 0,
            "takefocus": True,
            "repeatdelay": 400,
            "repeatinterval": 80,
        }
        self.cursor_up_button = self.tk.Button(text="▲", command=lambda: self.step_data_cursor(1), **cursor_arrow_options)
        self.cursor_up_button.grid(row=0, column=2, sticky="nsew")
        self.tk.Frame(cursor_shell, height=1, background="#94a3b8").grid(row=1, column=2, sticky="ew")
        self.cursor_down_button = self.tk.Button(text="▼", command=lambda: self.step_data_cursor(-1), **cursor_arrow_options)
        self.cursor_down_button.grid(row=2, column=2, sticky="nsew")
        self.ttk.Label(cursor_bar, textvariable=self.cursor_unit_var, style="Card.TLabel").pack(
            side="left",
            padx=(4, 8) if self.compact_layout else (6, 16),
        )
        self.cursor_value_label = self.ttk.Label(
            cursor_bar,
            textvariable=self.cursor_var,
            style="Cursor.TLabel",
            width=15 if not self.compact_layout else 14,
            anchor="e",
        )
        self.cursor_value_label.pack(side="left")
        self.cursor_position_entry.bind("<Return>", self.apply_cursor_position)
        self.cursor_position_entry.bind("<FocusOut>", self.apply_cursor_position)
        self.cursor_position_entry.bind("<Up>", lambda _event: (self.step_data_cursor(1), "break")[1])
        self.cursor_position_entry.bind("<Down>", lambda _event: (self.step_data_cursor(-1), "break")[1])
        self.update_cursor_control_state()

        build_footer(self, root)
        self.workspace_tabs = {
            "采集": acquisition_page,
            "处理": processing_tab,
            "PL荧光": pl_tab,
            "AB吸收": absorbance_tab,
        }
        notebook.bind("<<NotebookTabChanged>>", self.on_workspace_changed)
        notebook.select(self.workspace_tabs[self.initial_workspace])
        self.rebuild_pl_file_list()
        self.rebuild_ab_file_list()
        self.update_baseline_state()
        self.on_workspace_changed()

    def current_workspace(self) -> str:
        if not hasattr(self, "sidebar_notebook"):
            return self.initial_workspace
        selected = self.sidebar_notebook.select()
        return str(self.sidebar_notebook.tab(selected, "text")) if selected else self.initial_workspace

    def current_plot_uses_pixels(self) -> bool:
        return self.current_workspace() in ("采集", "处理") and self.x_axis_var.get() == "像素索引"

    def current_plot_uses_energy(self) -> bool:
        return self.current_workspace() == "AB吸收" and self.ab_x_axis_var.get() == "光子能量 (eV)"

    def on_workspace_changed(self, _event=None) -> None:
        if not hasattr(self, "plot"):
            return
        if self._active_workspace:
            self.workspace_view_bounds[self._active_workspace] = (
                self.plot.x_min,
                self.plot.x_max,
                self.plot.y_min,
                self.plot.y_max,
            )
        workspace = self.current_workspace()
        self._active_workspace = workspace
        self.plot.crosshair_drag_preview_enabled = workspace != "采集"
        bounds = self.workspace_view_bounds.get(workspace, (None, None, None, None))
        self.plot.x_min, self.plot.x_max, self.plot.y_min, self.plot.y_max = bounds
        self.update_cursor_axis_labels()
        self.refresh_plot()
        self.update_save_availability()

    def update_save_availability(self) -> None:
        if not hasattr(self, "save_button"):
            return
        workspace = self.current_workspace()
        if workspace == "采集":
            self.curve_save_button.pack_forget()
        elif not self.curve_save_button.winfo_manager():
            self.curve_save_button.pack(
                side="left", before=self.plot_legend_canvas,
                padx=(0, 3 if self.compact_layout else 5),
            )
        if workspace == "采集":
            if not self.sidebar_save_panel.winfo_manager():
                # The notebook is expandable.  Reinsert the save panel before
                # it so Tk's pack order remains stable after a tab switch.
                self.sidebar_save_panel.pack(
                    side="bottom",
                    fill="x",
                    before=self.sidebar_notebook,
                    padx=1,
                    pady=(0, 1),
                )
            self.save_button.configure(
                state="normal" if self.controls_enabled else "disabled",
                style="Saved.TButton" if self.save_verified else "Primary.TButton",
            )
        else:
            self.sidebar_save_panel.pack_forget()
        self.save_context_var.set("")

    def update_import_selection_styles(self) -> None:
        if hasattr(self, "pl_open_button"):
            self.pl_open_button.configure(
                style="Selected.History.TButton" if self.pl_result_service.entries else "Primary.History.TButton"
            )
            self.pl_background_button.configure(
                style="Selected.History.TButton" if self.pl_result_service.background is not None else "History.TButton"
            )
        if hasattr(self, "ab_samples_button"):
            self.ab_samples_button.configure(
                style="Selected.History.TButton" if self.historical_absorbance_service.current_group.entries else "Primary.History.TButton"
            )
            self.ab_reference_file_button.configure(
                style="Selected.History.TButton" if self.historical_absorbance_service.reference is not None else "History.TButton"
            )
            self.ab_reference_file_button.configure(
                text="更换 BG…" if self.historical_absorbance_service.reference is not None else "选择 BG…"
            )
            self.ab_reload_button.configure(
                state="normal" if self.historical_absorbance_service.current_group.entries else "disabled"
            )

    def select_pl_files(self) -> None:
        from tkinter import filedialog, messagebox

        selected = filedialog.askopenfilenames(
            title="选择历史 PL 文件",
            filetypes=(("光谱文件", "*.csv *.txt"), ("CSV 文件", "*.csv"), ("TXT 文件", "*.txt")),
            parent=self.root,
        )
        if not selected:
            return
        result = self.pl_result_service.load_many(Path(path) for path in selected)
        self.pl_active_var.set(self.pl_result_service.active_id or "")
        self.rebuild_pl_file_list()
        parts = [f"已加入 {result.added} 个文件"]
        if result.duplicates:
            parts.append(f"跳过 {result.duplicates} 个重复文件")
        if result.errors:
            parts.append(f"{len(result.errors)} 个文件失败")
            messagebox.showwarning(
                "部分 PL 文件无法读取",
                "\n".join(f"{path.name}：{message}" for path, message in result.errors),
                parent=self.root,
            )
        self.pl_status_var.set("；".join(parts))
        if result.added:
            self.plot.x_min = self.plot.x_max = self.plot.y_min = self.plot.y_max = None
        self.refresh_plot()

    def select_pl_background(self) -> None:
        from tkinter import filedialog, messagebox

        selected = filedialog.askopenfilename(
            title="选择 Si 基底 BG 文件",
            filetypes=(("光谱文件", "*.csv *.txt"), ("CSV 文件", "*.csv"), ("TXT 文件", "*.txt")),
            parent=self.root,
        )
        if not selected:
            return
        try:
            background = self.pl_result_service.load_background(Path(selected))
        except (OSError, ValueError) as exc:
            messagebox.showerror("BG 文件无法读取", str(exc), parent=self.root)
            return
        self.pl_background_status_var.set(background.source_path.name)
        self.plot.y_min = self.plot.y_max = None
        self.rebuild_pl_file_list()
        self.refresh_plot()

    def clear_pl_background(self) -> None:
        self.pl_result_service.clear_background()
        self.pl_background_status_var.set("未选择（显示原始 PL）")
        self.plot.y_min = self.plot.y_max = None
        self.rebuild_pl_file_list()
        self.refresh_plot()

    def clear_pl_files(self) -> None:
        self.pl_result_service.clear()
        self.pl_active_var.set("")
        self.pl_status_var.set("尚未选择 PL 文件")
        self.pl_background_status_var.set("未选择（显示原始 PL）")
        self.rebuild_pl_file_list()
        self.plot.x_min = self.plot.x_max = self.plot.y_min = self.plot.y_max = None
        self.refresh_plot()

    def rebuild_pl_file_list(self) -> None:
        if not hasattr(self, "pl_list_frame"):
            return
        for child in self.pl_list_frame.winfo_children():
            child.destroy()
        self.pl_row_variables.clear()
        self.update_import_selection_styles()
        if not self.pl_result_service.entries:
            self.ttk.Label(
                self.pl_list_frame,
                text="可一次选择多个 OceanLab 或通用两列文件。",
                style="Hint.TLabel",
            ).pack(anchor="w", padx=4, pady=6)
            return
        turbo_enabled = bool(self.pl_turbo_var.get())
        turbo_denominator = max(1, len(self.pl_result_service.entries) - 1)
        for index, entry in enumerate(self.pl_result_service.entries):
            display_color = turbo_color(1.0 - index / turbo_denominator) if turbo_enabled else entry.color
            self.build_history_file_row("pl", entry, display_color, turbo_enabled)

    def build_history_file_row(self, kind, entry, color, color_disabled=False, parent=None, background="#ffffff"):
        frame = parent if parent is not None else getattr(self, kind + "_list_frame")
        canvas = getattr(self, kind + "_list_canvas")
        variables = getattr(self, kind + "_row_variables")
        active_variable = getattr(self, kind + "_active_var")
        set_visible = getattr(self, "set_" + kind + "_visible")
        set_active = getattr(self, "set_" + kind + "_active")
        choose_color = getattr(self, "choose_" + kind + "_color")
        remove = getattr(self, "remove_" + kind + "_entry")
        row = self.tk.Frame(frame, background=background) if parent is not None else self.ttk.Frame(frame, style="Card.TFrame")
        row.pack(fill="x", pady=1)
        row.columnconfigure(3, weight=1)
        visible = self.tk.BooleanVar(value=entry.visible)
        name = self.tk.StringVar(value=entry.display_name)
        visible_button = HistoryIconButton(row, icon="eye", variable=visible, toggle=True,
                                           background=background,
                                           command=lambda: set_visible(entry.entry_id, visible))
        visible_button.grid(row=0, column=0)
        self.attach_tooltip(visible_button, lambda: "隐藏此曲线" if visible.get() else "显示此曲线")
        active_button = HistoryIconButton(row, icon="crosshair", variable=active_variable, value=entry.entry_id,
                                          background=background,
                                          command=lambda: set_active(entry.entry_id))
        active_button.grid(row=0, column=1, padx=1)
        self.attach_tooltip(active_button, lambda: "当前活动曲线（双倍线宽、峰值及游标）"
                            if active_variable.get() == entry.entry_id else "设为活动曲线（双倍线宽、峰值及游标）")
        color_button = HistoryIconButton(row, icon="palette", foreground=color,
                                         background=background,
                                         command=lambda: choose_color(entry.entry_id, entry.color),
                                         state="disabled" if color_disabled else "normal")
        color_button.grid(row=0, column=2, padx=(0, 3))
        self.attach_tooltip(color_button, lambda: "Turbo 渐变色已开启，关闭后可选择颜色"
                            if color_button.disabled else "选择曲线颜色")
        name_label = EllipsisLabel(row, textvariable=name, style="ABGroupName.TLabel" if parent is not None else "HistoryName.TLabel", takefocus=True, cursor="hand2")
        name_label.grid(row=0, column=3, sticky="ew")
        self.attach_tooltip(name_label, lambda: entry.spectrum.source_path.name, delay_ms=600, max_width=None)
        for event in ("<Button-1>", "<Return>", "<space>"):
            name_label.bind(event, lambda _event: set_active(entry.entry_id))
        remove_button = HistoryIconButton(row, icon="trash", background=background, command=lambda: remove(entry.entry_id))
        remove_button.grid(row=0, column=4, padx=(4, 0))
        self.attach_tooltip(remove_button, "移除此曲线")
        variables[entry.entry_id] = {"visible": visible, "name": name, "name_label": name_label,
                                    "color_button": color_button, "remove_button": remove_button,
                                    "active_button": active_button, "visible_button": visible_button}
        if parent is None:
            self.bind_list_mousewheel(row, canvas)

    def bind_list_mousewheel(self, widget, canvas) -> None:
        def scroll(event):
            delta = int(getattr(event, "delta", 0))
            if delta:
                units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
            else:
                units = -1 if int(getattr(event, "num", 0)) == 4 else 1
            canvas.yview_scroll(units, "units")
            return "break"

        widget.bind("<MouseWheel>", scroll, add="+")
        widget.bind("<Button-4>", scroll, add="+")
        widget.bind("<Button-5>", scroll, add="+")
        for child in widget.winfo_children():
            self.bind_list_mousewheel(child, canvas)

    def update_pl_row_colors(self) -> None:
        turbo_enabled = bool(self.pl_turbo_var.get())
        denominator = max(1, len(self.pl_result_service.entries) - 1)
        for index, entry in enumerate(self.pl_result_service.entries):
            row = self.pl_row_variables.get(entry.entry_id)
            if row is None or "color_button" not in row:
                continue
            color = turbo_color(1.0 - index / denominator) if turbo_enabled else entry.color
            row["color_button"].configure(
                foreground=color,
                disabledforeground=color,
                state="disabled" if turbo_enabled else "normal",
            )

    def set_pl_visible(self, entry_id: str, variable) -> None:
        self.pl_result_service.set_visible(entry_id, variable.get())
        self.pl_active_var.set(self.pl_result_service.active_id or "")
        self.refresh_plot()

    def set_pl_active(self, entry_id: str) -> None:
        self.pl_result_service.set_active(entry_id)
        self.pl_active_var.set(self.pl_result_service.active_id or "")
        row = self.pl_row_variables.get(entry_id)
        if row is not None:
            row["visible"].set(True)
        self.refresh_plot()

    def rename_pl_entry(self, entry_id: str, variable):
        from tkinter import messagebox

        try:
            self.pl_result_service.rename(entry_id, variable.get())
        except ValueError as exc:
            messagebox.showerror("曲线名称无效", str(exc), parent=self.root)
            self.rebuild_pl_file_list()
        self.refresh_plot()
        return "break"

    def choose_pl_color(self, entry_id: str, current: str) -> None:
        from tkinter import colorchooser

        _rgb, color = colorchooser.askcolor(color=current, title="选择 PL 曲线颜色", parent=self.root)
        if color:
            self.pl_result_service.set_color(entry_id, color)
            self.update_pl_row_colors()
            row = self.pl_row_variables.get(entry_id)
            if row is not None and "color_button" in row:
                row["color_button"].configure(
                    command=lambda entry_id=entry_id, current=color: self.choose_pl_color(entry_id, current)
                )
            self.refresh_plot()

    def remove_pl_entry(self, entry_id: str) -> None:
        self.pl_result_service.remove(entry_id)
        self.pl_active_var.set(self.pl_result_service.active_id or "")
        self.rebuild_pl_file_list()
        self.pl_status_var.set(f"已载入 {len(self.pl_result_service.entries)} 个 PL 文件")
        self.refresh_plot()

    def on_pl_display_changed(self, _event=None) -> None:
        self.plot.y_min = self.plot.y_max = None
        self.update_pl_row_colors()
        self.refresh_plot()

    def on_ab_display_changed(self, _event=None) -> None:
        self.plot.y_min = self.plot.y_max = None
        self.refresh_plot()

    def open_ab_offset_dialog(self) -> None:
        if self.ab_offset_dialog is not None and self.ab_offset_dialog.window.winfo_exists():
            self.ab_offset_dialog.window.lift()
            return
        if self.historical_absorbance_service.group_signals():
            self.ab_offset_dialog = SignalOffsetDialog(self)

    def update_ab_offset_status(self) -> None:
        service = self.historical_absorbance_service
        descriptions = []
        suspected = []
        platforms = []
        for key, name, spectrum in service.group_signals():
            info = assess_signal(spectrum.raw)
            if info.plateau is not None:
                platforms.append(f'{name} 原平台 {info.plateau:.4f} counts（{info.plateau_points} 点）')
            correction = service.current_group.offsets.get(key)
            if correction:
                descriptions.append(f'{name} +{correction.delta:.1f}' +
                                    ('（自动估算）' if correction.automatic else '（估算）' if correction.estimated else ''))
            elif info.suspected_offset:
                suspected.append(name)
        text = '已应用：' + '；'.join(descriptions) if descriptions else '未应用信号偏移'
        if suspected:
            text += '\n疑似整体偏移：' + '、'.join(suspected) + '；请预览检查'
        if platforms:
            text += '\n' + '；'.join(platforms)
        self.ab_offset_status_var.set(text)
        if hasattr(self, 'ab_offset_summary_var'):
            summary = (f'偏移：已应用 {len(descriptions)} 项' +
                       ('（自动估算）' if any(c.automatic for c in service.current_group.offsets.values()) else '')
                       if descriptions else '偏移：未应用')
            if suspected:
                summary += f'\n疑似整体偏移 {len(suspected)} 项，请检查'
            self.ab_offset_summary_var.set(summary)
        if hasattr(self, 'ab_offset_button'):
            self.ab_offset_button.configure(state='normal' if service.group_signals() else 'disabled',
                                            style='Primary.History.TButton' if suspected else 'Selected.History.TButton' if descriptions else 'History.TButton')

    def set_ab_x_axis(self, value: str) -> None:
        if value not in ("波长 (nm)", "光子能量 (eV)"):
            return
        self.ab_x_axis_var.set(value)
        self.plot.x_min = self.plot.x_max = None
        self.update_ab_axis_button_styles()
        self.update_cursor_axis_labels()
        self.refresh_plot()

    def update_ab_axis_button_styles(self) -> None:
        if not hasattr(self, "ab_wavelength_axis_button"):
            return
        energy = self.ab_x_axis_var.get() == "光子能量 (eV)"
        self.ab_wavelength_axis_button.configure(style="History.TButton" if energy else "Selected.History.TButton")
        self.ab_energy_axis_button.configure(style="Selected.History.TButton" if energy else "History.TButton")

    def sync_ab_group_controls(self) -> None:
        service = self.historical_absorbance_service
        self.ab_group_var.set(service.current_group.name)
        if hasattr(self, "ab_group_combo"):
            self.ab_group_combo.configure(values=tuple(group.name for group in service.groups))
        self.ab_reference_status_var.set(service.reference.source_path.name if service.reference else "未选择")
        self.update_ab_offset_status()
        for group in service.groups:
            label = self.ab_group_header_labels.get(group.group_id)
            widgets = self.ab_group_widgets.get(group.group_id)
            if widgets is not None:
                widgets["title"].set(self.ab_group_header_text(group))
                selected = group.group_id == service.current_group_id
                color = "#dceafb" if selected else "#e8eef5"
                widgets["frame"].configure(highlightbackground="#8eafd5" if selected else "#d3dce7")
                widgets["header"].configure(background=color)
                label.configure(style="Selected.ABGroupHeader.TLabel" if selected else "ABGroupHeader.TLabel")
                widgets["visible"].set(group.visible)
                for button in (widgets["visible_button"], widgets["remove_button"]):
                    button.configure(background=color)
                widgets["remove_button"].configure(state="normal" if service.can_remove_group(group.group_id) else "disabled")
        self.update_import_selection_styles()

    @staticmethod
    def ab_group_header_text(group) -> str:
        return f"{group.name} · {len(group.entries)} 份 · " + ("BG 已选择" if group.reference else "等待 BG") + (" · 隐藏" if not group.visible else "")

    def create_ab_group(self) -> None:
        group = self.historical_absorbance_service.create_group()
        self.ab_action_status_var.set(f"已新建{group.name}；请选择本组 BG 和样品。")
        self.rebuild_ab_file_list()
        self.refresh_plot()

    def on_ab_group_changed(self, _event=None) -> None:
        service = self.historical_absorbance_service
        group = next((group for group in service.groups if group.name == self.ab_group_var.get()), None)
        if group is None:
            return
        service.select_group(group.group_id)
        if group.reference is not None and group.visible:
            visible = next((entry for entry in group.entries if entry.visible), None)
            if visible is not None:
                service.set_active(visible.entry_id)
        self.sync_ab_group_controls()
        self.ab_action_status_var.set(f"当前编辑{group.name}；添加样品、更换 BG 仅作用于本组。")
        self.refresh_plot()

    def clear_ab_group(self) -> None:
        self.historical_absorbance_service.clear_current_group()
        self.ab_action_status_var.set(f"已清空{self.historical_absorbance_service.current_group.name}。")
        self.rebuild_ab_file_list()
        self.refresh_plot()

    def reload_ab_samples(self) -> None:
        from tkinter import messagebox

        service = self.historical_absorbance_service
        paths = tuple(entry.spectrum.source_path for entry in service.current_group.entries)
        result = service.load_samples(paths, reload=True)
        if result.errors:
            messagebox.showwarning(
                "部分样品未更新（保留原数据）",
                "\n".join(f"{path.name}：{message}" for path, message in result.errors), parent=self.root,
            )
        self.ab_action_status_var.set(f"{service.current_group.name}：重读 {result.duplicates} 份样品，失败 {len(result.errors)} 份。")
        self.refresh_plot()

    def select_ab_sample_files(self) -> None:
        from tkinter import filedialog, messagebox

        selected = filedialog.askopenfilenames(
            title=f"为{self.historical_absorbance_service.current_group.name}添加样品（可多选）",
            filetypes=(("光谱文件", "*.csv *.txt"), ("CSV 文件", "*.csv"), ("TXT 文件", "*.txt")),
            parent=self.root,
        )
        if not selected:
            return
        result = self.historical_absorbance_service.load_samples(Path(path) for path in selected)
        self.ab_active_var.set(self.historical_absorbance_service.active_id or "")
        if result.errors:
            messagebox.showwarning(
                "部分样品文件无法读取",
                "\n".join(f"{path.name}：{message}" for path, message in result.errors),
                parent=self.root,
            )
        if result.added:
            self.rebuild_ab_file_list()
        self.ab_action_status_var.set(
            f"{self.historical_absorbance_service.current_group.name}：添加 {result.added} 份，重复 {result.duplicates} 份，失败 {len(result.errors)} 份。"
            + ("重复文件保持原数据；更新内容请点击“重读本组样品”。" if result.duplicates else "")
        )
        self.refresh_plot()

    def select_ab_reference_file(self) -> None:
        from tkinter import filedialog, messagebox

        selected = filedialog.askopenfilename(
            title=f"选择{self.historical_absorbance_service.current_group.name}的 BG 参考",
            filetypes=(("光谱文件", "*.csv *.txt"), ("CSV 文件", "*.csv"), ("TXT 文件", "*.txt")),
            parent=self.root,
        )
        if not selected:
            return
        try:
            reference = self.historical_absorbance_service.load_reference(Path(selected))
        except (OSError, ValueError) as exc:
            messagebox.showerror("BG 参考无法读取", str(exc), parent=self.root)
            return
        self.ab_reference_status_var.set(reference.source_path.name)
        self.sync_ab_group_controls()
        self.ab_action_status_var.set(
            f"{self.historical_absorbance_service.current_group.name}：BG 已更新，仅重算本组 {len(self.historical_absorbance_service.current_group.entries)} 份样品。"
        )
        self.refresh_plot()

    def clear_ab_reference(self) -> None:
        self.historical_absorbance_service.clear_reference()
        self.ab_reference_status_var.set("未选择")
        self.sync_ab_group_controls()
        self.ab_action_status_var.set("已清除本组 BG；本组样品等待参考，其他组结果继续显示。")
        self.refresh_plot()

    def clear_historical_ab(self) -> None:
        self.historical_absorbance_service.clear()
        self.ab_active_var.set("")
        self.ab_reference_status_var.set("未选择")
        self.ab_result_status_var.set("请选择样品信号和 BG 参考")
        self.ab_action_status_var.set("已清空全部分析组。")
        self.rebuild_ab_file_list()
        self.plot.x_min = self.plot.x_max = self.plot.y_min = self.plot.y_max = None
        self.refresh_plot()

    def rebuild_ab_file_list(self) -> None:
        if not hasattr(self, "ab_list_frame"):
            return
        for child in self.ab_list_frame.winfo_children():
            child.destroy()
        self.ab_row_variables.clear()
        self.ab_group_header_labels.clear()
        self.ab_group_widgets.clear()
        for group in self.historical_absorbance_service.groups:
            group_id = group.group_id
            card = self.tk.Frame(self.ab_list_frame, background="#f4f7fb", highlightthickness=1, highlightbackground="#d3dce7")
            card.pack(fill="x", padx=3, pady=(4, 5))
            header = self.tk.Frame(card, background="#e8eef5", padx=5, pady=3)
            header.pack(fill="x")
            header.columnconfigure(2, weight=1)
            title = self.tk.StringVar(value=self.ab_group_header_text(group))
            label = EllipsisLabel(header, textvariable=title, style="ABGroupHeader.TLabel", cursor="hand2", takefocus=True)
            label.grid(row=0, column=2, sticky="ew", padx=(4, 0))
            self.attach_tooltip(label, lambda group=group: self.ab_group_header_text(group))
            for event in ("<Button-1>", "<Return>", "<space>"):
                label.bind(event, lambda _event, name=group.name: self.select_ab_group_header(name))
            visible = self.tk.BooleanVar(value=group.visible)
            eye = HistoryIconButton(header, icon="eye", variable=visible, toggle=True, background="#e8eef5",
                                    command=lambda group_id=group_id, visible=visible: self.set_ab_group_visible(group_id, visible))
            eye.grid(row=0, column=0, padx=(0, 2))
            self.attach_tooltip(eye, lambda visible=visible: "隐藏整组曲线（保留各曲线显隐选择）" if visible.get() else "显示此组曲线（保留各曲线显隐选择）")
            trash = HistoryIconButton(header, icon="trash", background="#e8eef5", command=lambda group_id=group_id: self.remove_ab_group(group_id))
            trash.grid(row=0, column=1)
            self.attach_tooltip(trash, lambda group_id=group_id: "删除此分析组（需确认）；源文件保留"
                                if self.historical_absorbance_service.can_remove_group(group_id) else "保留一个空分析组，无需删除")
            self.ab_group_header_labels[group_id] = label
            self.ab_group_widgets[group_id] = {"frame": card, "header": header, "visible": visible, "title": title,
                                               "visible_button": eye, "remove_button": trash}
            body = self.tk.Frame(card, background="#f4f7fb", padx=5, pady=3)
            body.pack(fill="x")
            for entry in group.entries:
                self.build_history_file_row("ab", entry, entry.color, parent=body, background="#f4f7fb")
            if not group.entries:
                self.ttk.Label(body, text="暂无样品", style="ABGroupName.TLabel").pack(anchor="w", pady=3)
            self.bind_list_mousewheel(card, self.ab_list_canvas)
        self.sync_ab_group_controls()

    def select_ab_group_header(self, name: str) -> None:
        self.ab_group_var.set(name)
        self.on_ab_group_changed()

    def set_ab_group_visible(self, group_id: str, variable) -> None:
        self.historical_absorbance_service.set_group_visible(group_id, variable.get())
        self.ab_action_status_var.set("整组已显示。" if variable.get() else "整组已隐藏；保留各曲线显隐选择。")
        self.sync_ab_group_controls()
        self.refresh_plot()

    def remove_ab_group(self, group_id: str) -> None:
        from tkinter import messagebox

        service = self.historical_absorbance_service
        group = next((group for group in service.groups if group.group_id == group_id), None)
        if group is None or not service.can_remove_group(group_id):
            return
        if not messagebox.askyesno(
            "删除分析组",
            f"删除“{group.name}”吗？\n\n本组的 {len(group.entries)} 份样品、BG 参考和偏移校正将从当前分析中移除。\n源文件和其他分析组保留。",
            parent=self.root, icon="warning", default="no",
        ):
            return
        if self.ab_offset_dialog is not None and self.ab_offset_dialog.group_id == group_id:
            self.ab_offset_dialog.close()
        name = group.name
        service.remove_group(group_id)
        self.ab_action_status_var.set(f"已移除{name}；源文件保留。")
        self.rebuild_ab_file_list()
        self.refresh_plot()

    def set_ab_visible(self, entry_id: str, variable) -> None:
        self.historical_absorbance_service.set_visible(entry_id, variable.get())
        self.ab_active_var.set(self.historical_absorbance_service.active_id or "")
        self.refresh_plot()

    def set_ab_active(self, entry_id: str) -> None:
        self.historical_absorbance_service.set_active(entry_id)
        self.ab_active_var.set(self.historical_absorbance_service.active_id or "")
        self.sync_ab_group_controls()
        row = self.ab_row_variables.get(entry_id)
        if row is not None:
            row["visible"].set(True)
        self.refresh_plot()

    def rename_ab_entry(self, entry_id: str, variable):
        from tkinter import messagebox

        try:
            self.historical_absorbance_service.rename(entry_id, variable.get())
        except ValueError as exc:
            messagebox.showerror("曲线名称无效", str(exc), parent=self.root)
            self.rebuild_ab_file_list()
        self.refresh_plot()
        return "break"

    def choose_ab_color(self, entry_id: str, current: str) -> None:
        from tkinter import colorchooser

        _rgb, color = colorchooser.askcolor(color=current, title="选择 AB 曲线颜色", parent=self.root)
        if color:
            self.historical_absorbance_service.set_color(entry_id, color)
            row = self.ab_row_variables.get(entry_id)
            if row is not None and "color_button" in row:
                row["color_button"].configure(
                    foreground=color,
                    command=lambda entry_id=entry_id, current=color: self.choose_ab_color(entry_id, current),
                )
            self.refresh_plot()

    def remove_ab_entry(self, entry_id: str) -> None:
        self.historical_absorbance_service.remove(entry_id)
        self.ab_active_var.set(self.historical_absorbance_service.active_id or "")
        self.rebuild_ab_file_list()
        self.refresh_plot()

    def load_oceanview_toolbar_icon(self, filename: str):
        """Load the bundled toolbar asset so OceanView is not a runtime dependency."""
        path = TOOLBAR_ICON_DIR / filename
        if not path.is_file():
            if filename == "crosshair_128px.png":
                return self.create_crosshair_toolbar_icon()
            return None
        try:
            image = self.tk.PhotoImage(file=str(path))
            divisor = 20 if self.compact_layout else 10
            return image.zoom(3, 3).subsample(divisor, divisor)
        except (OSError, self.tk.TclError):
            logger.warning("Unable to load bundled toolbar icon: %s", path, exc_info=True)
            return None

    def create_save_toolbar_icon(self, color):
        """Match the existing toolbar's image dimensions without emoji fonts."""
        image = self.tk.PhotoImage(width=128, height=128)
        for rectangle in (
            (18, 14, 94, 22), (18, 14, 26, 114), (105, 30, 113, 114), (18, 106, 113, 114),
            (38, 22, 46, 54), (38, 46, 88, 54), (80, 22, 88, 54),
            (36, 64, 94, 72), (36, 64, 44, 106), (86, 64, 94, 106),
            (49, 80, 81, 86), (49, 94, 81, 100),
        ):
            image.put(color, to=rectangle)
        for step in range(16):
            image.put(color, to=(90 + step, 14 + step, 98 + step, 22 + step))
        divisor = 20 if self.compact_layout else 10
        return image.zoom(3, 3).subsample(divisor, divisor)

    def create_crosshair_toolbar_icon(self):
        """Create the crosshair tool icon without adding another binary asset."""
        if self.compact_layout:
            image = self.tk.PhotoImage(width=22, height=22)
            color = "#111111"
            image.put(color, to=(10, 2, 12, 8))
            image.put(color, to=(10, 14, 12, 20))
            image.put(color, to=(2, 10, 8, 12))
            image.put(color, to=(14, 10, 20, 12))
            image.put(color, to=(8, 8, 14, 10))
            image.put(color, to=(8, 12, 14, 14))
            image.put(color, to=(8, 10, 10, 12))
            image.put(color, to=(12, 10, 14, 12))
            return image
        image = self.tk.PhotoImage(width=42, height=42)
        color = "#111111"
        image.put(color, to=(19, 3, 23, 15))
        image.put(color, to=(19, 27, 23, 39))
        image.put(color, to=(3, 19, 15, 23))
        image.put(color, to=(27, 19, 39, 23))
        image.put(color, to=(16, 16, 26, 18))
        image.put(color, to=(16, 24, 26, 26))
        image.put(color, to=(16, 18, 18, 24))
        image.put(color, to=(24, 18, 26, 24))
        return image

    def attach_tooltip(self, widget, text, delay_ms: int = 450, max_width=760):
        state = {"timer": None, "window": None}

        def hide(_event=None):
            if state["timer"] is not None:
                try:
                    self.root.after_cancel(state["timer"])
                except self.tk.TclError:
                    pass
                state["timer"] = None
            if state["window"] is not None:
                state["window"].destroy()
                state["window"] = None

        def show():
            state["timer"] = None
            if state["window"] is not None or not widget.winfo_exists():
                return
            tip = self.tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            self.tk.Label(
                tip,
                text=text() if callable(text) else text,
                background="#172033",
                foreground="#ffffff",
                padx=8,
                pady=4,
                font=("Microsoft YaHei UI", 9),
                justify="left",
                wraplength=(widget.winfo_screenwidth() - 24 if max_width is None
                            else min(max_width, widget.winfo_screenwidth() - 24)),
            ).pack()
            state["window"] = tip
            tip.update_idletasks()
            x = max(8, min(widget.winfo_rootx(), widget.winfo_screenwidth() - tip.winfo_reqwidth() - 8))
            y = widget.winfo_rooty() + widget.winfo_height() + 5
            if y + tip.winfo_reqheight() > widget.winfo_screenheight() - 8:
                y = max(8, widget.winfo_rooty() - tip.winfo_reqheight() - 5)
            tip.geometry(f"+{x}+{y}")

        def schedule(_event=None):
            hide()
            state["timer"] = self.root.after(delay_ms, show)

        widget.bind("<Enter>", schedule, add="+")
        widget.bind("<Leave>", hide, add="+")
        widget.bind("<ButtonPress>", hide, add="+")
        widget.bind("<FocusOut>", hide, add="+")
        widget.bind("<MouseWheel>", hide, add="+")
        widget.bind("<Button-4>", hide, add="+")
        widget.bind("<Button-5>", hide, add="+")
        widget.bind("<Destroy>", hide, add="+")
        widget._oceanlab_tooltip = state
        return state

    def update_plot_mode_buttons(self, active_mode: str) -> None:
        for mode, button in self.plot_tool_buttons.items():
            button.configure(style="PlotToolActive.TButton" if mode == active_mode else "PlotTool.TButton")
        if hasattr(self, "cursor_position_entry"):
            self.update_cursor_control_state()

    def toggle_plot_mode(self, mode: str) -> None:
        previous_mode = self.plot.interaction_mode
        next_mode = "normal" if previous_mode == mode else mode
        self.plot.set_interaction_mode(next_mode)
        if "crosshair" in {previous_mode, next_mode}:
            self.plot.draw()

    def fit_plot_both(self) -> None:
        self.plot.set_interaction_mode("normal")
        self.plot.fit_both()

    def fit_plot_height(self) -> None:
        self.plot.set_interaction_mode("normal")
        self.plot.reset_y()

    def open_plot_range_dialog(self) -> None:
        from tkinter import messagebox

        existing = getattr(self, "plot_range_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        x_bounds = self.plot.current_x_bounds() or (0.0, 1.0)
        if self.plot._current_y_bounds is None:
            self.plot.draw()
        y_bounds = self.plot._current_y_bounds or (0.0, 1.0)
        values = {
            "x_min": self.tk.StringVar(value=f"{x_bounds[0]:.8g}"),
            "x_max": self.tk.StringVar(value=f"{x_bounds[1]:.8g}"),
            "y_min": self.tk.StringVar(value=f"{y_bounds[0]:.8g}"),
            "y_max": self.tk.StringVar(value=f"{y_bounds[1]:.8g}"),
        }

        dialog = self.tk.Toplevel(self.root)
        self.plot_range_dialog = dialog
        dialog.title("手动设置数值范围")
        dialog.configure(bg="#ffffff")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        content = self.ttk.Frame(dialog, style="Card.TFrame", padding=18)
        content.pack(fill="both", expand=True)

        fields = (
            ("X 轴最小值", "x_min"),
            ("X 轴最大值", "x_max"),
            ("Y 轴最小值", "y_min"),
            ("Y 轴最大值", "y_max"),
        )
        first_entry = None
        for row, (label, key) in enumerate(fields):
            self.ttk.Label(content, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
            entry = self.ttk.Entry(content, textvariable=values[key], width=22)
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            first_entry = first_entry or entry

        buttons = self.ttk.Frame(content, style="Card.TFrame")
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="ew", pady=(14, 0))

        def close_dialog():
            close_modal(dialog)
            self.plot_range_dialog = None

        def apply_values():
            try:
                xmin = float(values["x_min"].get())
                xmax = float(values["x_max"].get())
                ymin = float(values["y_min"].get())
                ymax = float(values["y_max"].get())
                if not all(math.isfinite(value) for value in (xmin, xmax, ymin, ymax)) or xmin >= xmax or ymin >= ymax:
                    raise ValueError
            except ValueError:
                messagebox.showerror("范围错误", "请输入有效数值，并确保最小值小于最大值。", parent=dialog)
                return
            self.plot.x_min, self.plot.x_max = self.plot.clamp_x_bounds(xmin, xmax)
            self.plot.y_min, self.plot.y_max = ymin, ymax
            self.plot.draw()
            close_dialog()

        self.ttk.Button(buttons, text="取消", command=close_dialog).pack(side="right")
        self.ttk.Button(buttons, text="应用", style="Primary.TButton", command=apply_values).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Return>", lambda _event: apply_values())
        dialog.bind("<Escape>", lambda _event: close_dialog())
        center_dialog(self.root, dialog)
        if first_entry is not None:
            first_entry.focus_set()
            first_entry.selection_range(0, "end")

    def spin_field(self, parent, label: str, variable, minimum: float, maximum: float, increment: float, suffix: str):
        row = self.ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=4)
        row.columnconfigure(0, weight=1)
        self.ttk.Label(row, text=label, style="Card.TLabel").grid(row=0, column=0, sticky="w")

        shell = self.tk.Frame(
            row,
            width=104,
            height=24 if self.compact_layout else 36,
            background="#ffffff",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
            highlightcolor="#0f6cbd",
        )
        shell.grid(row=0, column=1, sticky="e", padx=(6, 4))
        shell.grid_propagate(False)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure((0, 2), weight=1, uniform="advanced_stepper")
        entry = self.tk.Entry(
            shell,
            textvariable=variable,
            width=7,
            justify="right",
            font=("Microsoft YaHei UI", 9 if self.compact_layout else 10),
            background="#ffffff",
            foreground="#172033",
            insertbackground="#172033",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        entry.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(7, 6), pady=1)
        self.tk.Frame(shell, width=1, background="#94a3b8").grid(row=0, column=1, rowspan=3, sticky="ns")

        def adjust(direction: int) -> None:
            try:
                current = Decimal(variable.get().strip())
            except InvalidOperation:
                current = Decimal(str(minimum))
            updated = min(Decimal(str(maximum)), max(Decimal(str(minimum)), current + Decimal(str(increment)) * direction))
            variable.set(self.decimal_text(updated))

        arrow_options = {
            "master": shell,
            "width": 3,
            "font": ("Segoe UI Symbol", 8, "bold"),
            "foreground": "#183b66",
            "background": "#eef2f6",
            "activeforeground": "#0f4c81",
            "activebackground": "#dbe7f3",
            "relief": "flat",
            "borderwidth": 0,
            "highlightthickness": 0,
            "takefocus": True,
            "repeatdelay": 400,
            "repeatinterval": 80,
        }
        up_button = self.tk.Button(text="▲", command=lambda: adjust(1), **arrow_options)
        up_button.grid(row=0, column=2, sticky="nsew")
        self.tk.Frame(shell, height=1, background="#94a3b8").grid(row=1, column=2, sticky="ew")
        down_button = self.tk.Button(text="▼", command=lambda: adjust(-1), **arrow_options)
        down_button.grid(row=2, column=2, sticky="nsew")
        entry.bind("<Up>", lambda _event: (adjust(1), "break")[1])
        entry.bind("<Down>", lambda _event: (adjust(-1), "break")[1])

        self.ttk.Label(row, text=suffix, style="Card.TLabel", width=5).grid(row=0, column=2, sticky="w")
        return entry

    def numeric_stepper_control(self, parent, variable, adjust_command, width: int = 124, height: int = 48):
        """Build the shared large numeric entry and two-button stepper."""
        if self.compact_layout and height == 48:
            height = 32
        shell = self.tk.Frame(
            parent,
            width=width,
            height=height,
            background="#ffffff",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
            highlightcolor="#0f6cbd",
        )
        shell.grid_propagate(False)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure((0, 2), weight=1, uniform="numeric_stepper")
        entry = self.tk.Entry(
            shell,
            textvariable=variable,
            width=6,
            justify="right",
            font=("Microsoft YaHei UI", 9 if self.compact_layout else 10),
            background="#ffffff",
            foreground="#172033",
            insertbackground="#172033",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        entry.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(7, 6), pady=1)
        self.tk.Frame(shell, width=1, background="#94a3b8").grid(row=0, column=1, rowspan=3, sticky="ns")
        arrow_options = {
            "master": shell,
            "width": 3,
            "font": ("Segoe UI Symbol", 8, "bold"),
            "foreground": "#183b66",
            "background": "#eef2f6",
            "activeforeground": "#0f4c81",
            "activebackground": "#dbe7f3",
            "relief": "flat",
            "borderwidth": 0,
            "highlightthickness": 0,
            "takefocus": True,
            "repeatdelay": 400,
            "repeatinterval": 80,
        }
        up_button = self.tk.Button(text="▲", command=lambda: adjust_command(1), **arrow_options)
        up_button.grid(row=0, column=2, sticky="nsew")
        self.tk.Frame(shell, height=1, background="#94a3b8").grid(row=1, column=2, sticky="ew")
        down_button = self.tk.Button(text="▼", command=lambda: adjust_command(-1), **arrow_options)
        down_button.grid(row=2, column=2, sticky="nsew")
        entry.bind("<Up>", lambda _event: (adjust_command(1), "break")[1])
        entry.bind("<Down>", lambda _event: (adjust_command(-1), "break")[1])
        return shell, entry, up_button, down_button

    def stepper_field(
        self,
        parent,
        label: str,
        variable,
        adjust_command,
        suffix: str | None = None,
        unit_variable=None,
    ):
        row = self.ttk.Frame(parent, style="Card.TFrame")
        if self.compact_layout:
            row.pack(fill="x", pady=(3, 5))
            row.columnconfigure(0, minsize=112)
            self.ttk.Label(row, text=label, style="Card.TLabel").grid(row=0, column=0, sticky="w")
            editor_row = self.ttk.Frame(row, style="Card.TFrame")
            editor_row.grid(row=0, column=1, sticky="w", padx=(8, 0))
        else:
            row.pack(fill="x", pady=(4, 7))
            header = self.ttk.Frame(row, style="Card.TFrame")
            header.pack(fill="x")
            self.ttk.Label(header, text=label, style="Card.TLabel").pack(side="left")
            editor_row = self.ttk.Frame(row, style="Card.TFrame")
            editor_row.pack(anchor="w", padx=(64, 0), pady=(5, 0))

        shell, entry, plus_button, minus_button = self.numeric_stepper_control(
            editor_row,
            variable,
            adjust_command,
            height=32 if self.compact_layout else 54,
        )
        shell.pack(side="left", padx=(0, 4))
        unit_cell = self.ttk.Frame(
            editor_row,
            width=72,
            height=32 if self.compact_layout else 54,
            style="Card.TFrame",
        )
        unit_cell.pack(side="left", fill="y")
        unit_cell.grid_propagate(False)
        unit_cell.columnconfigure(0, weight=1)
        unit_cell.rowconfigure(0, weight=1)
        if unit_variable is not None:
            self.integration_minus_button = minus_button
            self.integration_plus_button = plus_button
            self.integration_unit_combo = self.ttk.Combobox(
                unit_cell,
                textvariable=unit_variable,
                values=("ms", "s"),
                state="readonly",
                width=5,
            )
            self.integration_unit_combo.grid(row=0, column=0, sticky="nsew")
        elif suffix:
            self.averages_minus_button = minus_button
            self.averages_plus_button = plus_button
            self.ttk.Label(unit_cell, text=suffix, style="Card.TLabel", anchor="w").grid(
                row=0, column=0, sticky="nsew", padx=(5, 0)
            )
        return entry

    def build_acquisition_preset_selector(self, parent) -> None:
        """Place the common acquisition preset selector in a section header."""
        self.preset_selector_label = self.tk.Label(
            parent,
            text="预设",
            background="#ffffff",
            foreground="#0f4c81",
            font=("Microsoft YaHei UI", 11, "bold"),
            padx=0,
            pady=0,
        )
        self.preset_selector_label.pack(side="left", padx=(0, 7))
        preset_group = self.tk.Frame(
            parent,
            background="#94a3b8",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#94a3b8",
        )
        preset_group.pack(side="left")
        self.integration_preset_buttons = []
        for index, name in enumerate(INTEGRATION_PRESET_NAMES):
            if index:
                self.tk.Frame(preset_group, width=1, background="#cbd5e1").pack(side="left", fill="y")
            preset_width = 48 if self.compact_layout else 56
            preset_height = 38 if self.compact_layout else 44
            cell = self.tk.Frame(preset_group, width=preset_width, height=preset_height, background="#ffffff")
            cell.pack(side="left")
            cell.pack_propagate(False)
            button = self.tk.Radiobutton(
                cell,
                text=str(index + 1),
                variable=self.integration_preset_var,
                value=name,
                command=self.on_integration_preset_changed,
                indicatoron=False,
                font=("Microsoft YaHei UI", 13, "bold"),
                relief="flat",
                offrelief="flat",
                overrelief="flat",
                borderwidth=0,
                highlightthickness=0,
                padx=0,
                pady=0,
                takefocus=True,
            )
            button.pack(fill="both", expand=True)
            self.integration_preset_buttons.append(button)
        self.refresh_preset_segments()

    def toggle_advanced_options(self) -> None:
        self.advanced_expanded = not self.advanced_expanded
        if self.advanced_expanded:
            self.advanced_frame.pack(fill="x", pady=(6, 0))
            self.advanced_toggle_button.configure(text="收起高级采集选项")
        else:
            self.advanced_frame.pack_forget()
            self.advanced_toggle_button.configure(text="展开高级采集选项")
            self.acquisition_canvas.yview_moveto(0.0)

    def set_controls(self, enabled: bool) -> None:
        self.controls_enabled = enabled
        state = "normal" if enabled else "disabled"
        for widget in (
            self.processing_reference_button,
            self.processing_clear_baselines_button,
            self.range_button,
        ):
            widget.configure(state=state)
        self.mode_combo.configure(state="readonly" if enabled else "disabled")
        self.update_advanced_control_states()
        self.update_acquisition_buttons()
        self.update_save_availability()

    def update_advanced_control_states(self) -> None:
        local_state = "readonly" if self.controls_enabled else "disabled"
        self.x_axis_combo.configure(state=local_state)
        real_device = (self.controls_enabled and self.connected and not self.simulation_mode
                       and not self.connection_pending and not self.acquisition_pending
                       and not self.single_in_progress and not self.acquiring)
        self.trigger_combo.configure(
            state="readonly" if real_device and self.device_capabilities["trigger"] else "disabled"
        )
        self.edc_check.configure(
            state="normal" if real_device and self.device_capabilities["electric_dark"] else "disabled"
        )
        self.nl_check.configure(
            state="normal" if real_device and self.device_capabilities["nonlinearity"] else "disabled"
        )
        self.light_source_check.configure(
            state="normal" if real_device and self.device_capabilities["light_source"] else "disabled"
        )
        self.decouple_check.configure(state="disabled")
        if not self.connected:
            message = "默认使用按需 / 正常触发、波长 X 轴并启用暗噪声校正。"
        elif self.simulation_mode:
            message = "模拟模式固定为按需 / 正常；X 轴可切换，硬件项目不执行。"
        else:
            unsupported = []
            labels = {
                "trigger": "触发",
                "electric_dark": "暗噪声校正",
                "nonlinearity": "非线性校正",
                "light_source": "内置光源",
            }
            for key, label in labels.items():
                if not self.device_capabilities[key]:
                    unsupported.append(label)
            message = "灰色项目为当前设备不支持。" if unsupported else "当前设备支持全部可配置硬件项目。"
        self.advanced_capability_var.set(message)

    def update_connection_buttons(self) -> None:
        self.update_acquisition_buttons()
        if self.closing:
            self.connect_button.configure(state="disabled")
            self.simulation_button.configure(state="disabled")
            return
        if self.connected and not self.simulation_mode:
            self.connect_button.configure(text="断开仪器", state="normal", style="Connected.TButton")
            self.simulation_button.configure(text="模拟模式", state="disabled")
            return
        if self.connection_pending:
            self.connect_button.configure(text="连接中…", state="disabled", style="Connecting.TButton")
            self.simulation_button.configure(
                text="退出模拟" if self.simulation_mode else "模拟模式",
                state="disabled",
            )
            return
        if self.simulation_mode:
            self.connect_button.configure(
                text="连接仪器",
                state="normal" if self.sdk_available else "disabled",
                style="ConnectIdle.TButton",
            )
            self.simulation_button.configure(text="退出模拟", state="normal")
            return
        self.connect_button.configure(
            text="连接仪器",
            state="normal" if self.sdk_available else "disabled",
            style="ConnectIdle.TButton",
        )
        self.simulation_button.configure(text="模拟模式", state="normal")

    def update_acquisition_buttons(self) -> None:
        self.update_advanced_control_states()
        preset_state = (
            "disabled"
            if self.closing or self.connection_pending or self.acquiring or self.single_in_progress or self.acquisition_pending
            else "normal"
        )
        for button in self.integration_preset_buttons:
            button.configure(state=preset_state)
        if self.closing or not self.controls_enabled or self.connection_pending:
            self.start_button.configure(state="disabled")
            self.single_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            return
        if self.cancel_requested:
            self.start_button.configure(state="disabled")
            self.single_button.configure(state="disabled")
            self.stop_button.configure(text="正在取消" if self.single_in_progress else "正在暂停", state="disabled")
            return
        if self.single_in_progress:
            self.start_button.configure(state="disabled")
            self.single_button.configure(state="disabled")
            self.stop_button.configure(
                text="取消单次",
                state="normal",
            )
            return
        if self.acquisition_pending:
            self.start_button.configure(state="disabled")
            self.single_button.configure(state="disabled")
            self.stop_button.configure(text="取消", state="normal")
            return
        self.stop_button.configure(text="暂停")
        self.start_button.configure(state="disabled" if self.acquiring else "normal")
        self.single_button.configure(state="disabled" if self.acquiring else "normal")
        self.stop_button.configure(state="normal" if self.acquiring else "disabled")

    def refresh_preset_segments(self) -> None:
        selected_name = self.integration_preset_var.get()
        for button, name in zip(self.integration_preset_buttons, INTEGRATION_PRESET_NAMES):
            selected = name == selected_name
            background = "#bfdbfe" if selected else "#f8fafc"
            button.configure(
                background=background,
                selectcolor=background,
                foreground="#0f4c81" if selected else "#52657a",
                activebackground="#e8f1fb",
                activeforeground="#0f4c81",
                disabledforeground="#94a3b8",
            )

    def on_setting_changed(self, *_args) -> None:
        if self._syncing_settings:
            return
        if self.simulation_mode:
            message = "模拟参数有改动，将在采集前自动应用"
        elif self.connected:
            message = "参数有改动，将在采集前自动应用"
        else:
            message = "参数有改动，连接仪器或进入模拟模式后在采集前应用"
        self.settings_state_var.set(message)

    def commit_current_acquisition_preset(self) -> bool:
        """Store the visible common acquisition and save settings in the active preset."""
        try:
            value = Decimal(self.integration_var.get().strip())
            averages = min(1000, max(1, int(self.averages_var.get().strip())))
            sequence_digits = min(9, max(1, int(self.sequence_digits_var.get().strip())))
            position_text = self.position_var.get().strip()
            position = max(0, int(position_text)) if position_text else None
        except (InvalidOperation, TypeError, ValueError):
            return False
        unit = self.integration_unit_var.get()
        save_format = self.save_format_var.get()
        suffix_mode = self.save_suffix_var.get()
        if (
            value <= 0
            or unit not in ("ms", "s")
            or save_format not in SAVE_FORMAT_OPTIONS
            or suffix_mode not in (SAVE_SUFFIX_TIME, SAVE_SUFFIX_SEQUENCE, SAVE_SUFFIX_NONE)
        ):
            return False
        if self.share_variable_name_var.get():
            self.shared_base_name = self.base_name_var.get().strip()
            preset_base_name = self.acquisition_presets[self._active_integration_preset]["base_name"]
        else:
            preset_base_name = self.base_name_var.get().strip()
        self.acquisition_presets[self._active_integration_preset] = {
            "integration_value": self.decimal_text(value),
            "integration_unit": unit,
            "averages": str(averages),
            "base_name": preset_base_name,
            "name_prefix": self.name_prefix_var.get().strip(),
            "name_suffix": self.name_suffix_var.get().strip(),
            "position": str(position).zfill(max(2, len(position_text))) if position is not None else "",
            "save_format": save_format,
            "save_directory": self.save_directory_var.get().strip(),
            "suffix_mode": suffix_mode,
            "sequence_digits": str(sequence_digits),
        }
        return True

    def commit_current_integration_preset(self) -> bool:
        """Compatibility wrapper for the former integration-only preset API."""
        return self.commit_current_acquisition_preset()

    def load_integration_preset(self, index: int) -> None:
        preset = self.acquisition_presets[index]
        self._active_integration_preset = index
        self._syncing_settings = True
        try:
            self.integration_preset_var.set(INTEGRATION_PRESET_NAMES[index])
            self.integration_unit_var.set(preset["integration_unit"])
            self._integration_unit_last = preset["integration_unit"]
            self.integration_var.set(preset["integration_value"])
            self.averages_var.set(preset["averages"])
            self.base_name_var.set(self.shared_base_name if self.share_variable_name_var.get() else preset["base_name"])
            self.name_prefix_var.set(preset["name_prefix"])
            self.name_suffix_var.set(preset["name_suffix"])
            self.position_var.set(preset["position"])
            self.save_format_var.set(preset["save_format"])
            self.save_directory_var.set(preset["save_directory"])
            self.save_suffix_var.set(preset["suffix_mode"])
            self.sequence_digits_var.set(preset["sequence_digits"])
            self.update_save_button_label()
        finally:
            self._syncing_settings = False
        self.refresh_save_verification_state()

    def on_integration_preset_changed(self, _event=None) -> None:
        if self.acquisition_pending or self.single_in_progress or self.acquiring or self.connection_pending:
            self.integration_preset_var.set(INTEGRATION_PRESET_NAMES[self._active_integration_preset])
            self.refresh_preset_segments()
            return
        try:
            requested_index = INTEGRATION_PRESET_NAMES.index(self.integration_preset_var.get())
        except ValueError:
            requested_index = self._active_integration_preset
        if requested_index == self._active_integration_preset:
            self.refresh_preset_segments()
            self.refresh_save_verification_state()
            return
        if not self.commit_current_acquisition_preset():
            self.integration_preset_var.set(INTEGRATION_PRESET_NAMES[self._active_integration_preset])
            self.refresh_preset_segments()
            self.status_var.set("当前常用采集参数或保存设置无效，修正后再切换预设")
            self.root.bell()
            return

        self.load_integration_preset(requested_index)
        self.refresh_preset_segments()
        self.on_setting_changed()
        self.save_preferences()
        if self.connected and not self.acquiring and not self.single_in_progress:
            self.apply_settings()
        else:
            self.status_var.set(f"已切换到{INTEGRATION_PRESET_NAMES[requested_index]}")
        self.restore_active_preset_single_capture()

    def render_shared_variable_check(self) -> None:
        if not hasattr(self, "shared_variable_check"):
            return
        self.shared_variable_check.render()

    def toggle_shared_variable_name(self) -> None:
        self.shared_variable_check.invoke()

    def on_share_variable_name_changed(self) -> None:
        self.render_shared_variable_check()
        current_name = self.base_name_var.get().strip()
        if self.share_variable_name_var.get():
            self.acquisition_presets[self._active_integration_preset]["base_name"] = current_name
            self.shared_base_name = current_name
            self.status_var.set("三个预设已共用当前变量名")
        else:
            self.shared_base_name = current_name
            self.base_name_var.set(
                self.acquisition_presets[self._active_integration_preset]["base_name"]
            )
            self.status_var.set("已恢复各预设独立的变量名")
        self.save_preferences()

    def clear_single_capture_history(self) -> None:
        self.single_capture_candidates.clear()
        self.single_capture_history = {
            index: [] for index in range(len(INTEGRATION_PRESET_NAMES))
        }
        self.preset_save_verified = {
            index: False for index in range(len(INTEGRATION_PRESET_NAMES))
        }
        self.refresh_save_verification_state()
        self._next_single_capture_id = 1
        self._displayed_single_capture_id = None
        self._show_single_capture_history = False

    def register_single_capture_candidate(self, frame: SpectrumFrame) -> None:
        preset_index = self._active_integration_preset
        snapshot = SingleCaptureSnapshot(
            capture_id=self._next_single_capture_id,
            frame=frame,
            wavelengths=tuple(self.wavelengths),
            display_name=self.compose_save_base_name(),
        )
        self._next_single_capture_id += 1
        self.single_capture_candidates[preset_index] = snapshot
        self._displayed_single_capture_id = snapshot.capture_id
        self._show_single_capture_history = True

    def commit_saved_single_capture(self, display_name: str) -> bool:
        preset_index = self._active_integration_preset
        candidate = self.single_capture_candidates.get(preset_index)
        if candidate is None or candidate.capture_id != self._displayed_single_capture_id:
            return False
        confirmed = replace(candidate, display_name=display_name)
        history = self.single_capture_history[preset_index]
        history.insert(0, confirmed)
        del history[3:]
        self.single_capture_candidates.pop(preset_index, None)
        self._displayed_single_capture_id = confirmed.capture_id
        self._show_single_capture_history = True
        return True

    def restore_active_preset_single_capture(self) -> None:
        preset_index = self._active_integration_preset
        snapshot = self.single_capture_candidates.get(preset_index)
        if snapshot is None:
            history = self.single_capture_history[preset_index]
            snapshot = history[0] if history else None
        self._show_single_capture_history = True
        if snapshot is None:
            self.raw_spectrum = []
            self.frame_context = None
            self.last_timestamp = None
            self._displayed_single_capture_id = None
            self.refresh_save_verification_state()
            self.refresh_plot()
            return

        cleared = list(self.spectrum_service.accept(snapshot.frame))
        self.raw_spectrum = list(snapshot.frame.values)
        self.frame_context = snapshot.frame.context
        self.last_timestamp = snapshot.frame.timestamp
        self._displayed_single_capture_id = snapshot.capture_id
        if self.spectrum_service.baselines.reference is None:
            self.reference_spectrum = None
            self.reference_context = None
            self.reference_timestamp = None
        if cleared:
            self.update_baseline_state()
        if preset_index in self.single_capture_candidates:
            self.mark_save_pending()
        else:
            self.refresh_save_verification_state()
        self.refresh_plot()

    def active_preset_single_capture_series(self) -> list[PlotSeries]:
        snapshots = self.active_preset_single_capture_snapshots()
        candidate = self.single_capture_candidates.get(self._active_integration_preset)
        colors = ("#0f6cbd", "#3f6f99", "#a8b7c5")
        dashes: tuple[tuple[int, ...] | None, ...] = (None, (8, 5), (2, 5))
        result: list[PlotSeries] = []
        for position, snapshot in enumerate(snapshots):
            if candidate is not None:
                prefix = "待保存" if position == 0 else f"前{position}次已保存"
            else:
                prefix = "最新已保存" if position == 0 else f"前{position}次已保存"
            x_values = (
                tuple(float(index) for index in range(len(snapshot.frame.values)))
                if self.x_axis_var.get() == "像素索引"
                else snapshot.wavelengths
            )
            result.append(
                PlotSeries(
                    f"{prefix:<6}｜{snapshot.display_name}",
                    x_values,
                    snapshot.frame.values,
                    colors[position],
                    active=position == 0,
                    dash=dashes[position],
                    metadata=self.curve_context_metadata(snapshot.frame.context, snapshot.frame.timestamp),
                )
            )
        return result

    def active_preset_single_capture_snapshots(self) -> list[SingleCaptureSnapshot]:
        preset_index = self._active_integration_preset
        candidate = self.single_capture_candidates.get(preset_index)
        history = self.single_capture_history[preset_index]
        return ([candidate] + history[:2]) if candidate is not None else history[:3]

    def single_capture_parameter_differences(self) -> list[str]:
        snapshots = self.active_preset_single_capture_snapshots()
        if len(snapshots) < 2:
            return []
        contexts = [snapshot.frame.context for snapshot in snapshots]
        checks = (
            ("仪器", [(context.source, context.serial) for context in contexts]),
            ("积分时间", [context.integration_us for context in contexts]),
            ("扫描平均次数", [context.averages for context in contexts]),
            ("滑动平均宽度", [context.boxcar for context in contexts]),
            ("触发模式", [context.trigger_mode for context in contexts]),
            ("暗噪声校正", [context.electric_dark for context in contexts]),
            ("非线性校正", [context.nonlinearity for context in contexts]),
            ("内置光源", [context.light_source for context in contexts]),
            ("波长网格", [snapshot.wavelengths for snapshot in snapshots]),
        )
        return [label for label, values in checks if any(value != values[0] for value in values[1:])]

    def update_single_history_warning(self, workspace: str) -> None:
        differences = (
            self.single_capture_parameter_differences()
            if workspace == "采集" and self._show_single_capture_history
            else []
        )
        if differences:
            self.single_history_warning_var.set(f"采集参数不一致：{'、'.join(differences)}")
            if not self.single_history_warning_label.winfo_manager():
                self.single_history_warning_label.pack(
                    fill="x",
                    padx=2,
                    pady=(0, 7),
                    before=self.plot.canvas,
                )
        else:
            self.single_history_warning_var.set("")
            if self.single_history_warning_label.winfo_manager():
                self.single_history_warning_label.pack_forget()

    def confirm_integration_preset_edit(self, event=None):
        if self.commit_current_acquisition_preset():
            self.save_preferences()
        if event is not None and getattr(event, "keysym", "") == "Return":
            return "break"
        return None

    @staticmethod
    def decimal_text(value: Decimal) -> str:
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text

    def integration_limits_ms(self) -> tuple[Decimal, Decimal]:
        if self.device_info:
            return (
                Decimal(self.device_info.integration_min_us) / Decimal(1000),
                Decimal(self.device_info.integration_max_us) / Decimal(1000),
            )
        return Decimal("0.001"), Decimal("65000")

    def adjust_integration(self, direction: int) -> None:
        unit = self.integration_unit_var.get()
        try:
            current = Decimal(self.integration_var.get().strip())
        except InvalidOperation:
            current = Decimal(1 if unit == "s" else 10)
        minimum_ms, maximum_ms = self.integration_limits_ms()
        if unit == "s":
            if direction < 0 and current <= Decimal(1):
                self.integration_unit_var.set("ms")
                self._integration_unit_last = "ms"
                self.integration_var.set("900")
                self.commit_current_integration_preset()
                self.on_setting_changed()
                return
            minimum = Decimal(1)
            maximum = max(Decimal(1), maximum_ms // Decimal(1000))
            step = Decimal(1)
        else:
            minimum = minimum_ms
            maximum = maximum_ms
            step = Decimal(10)
        updated = min(maximum, max(minimum, current + step * direction))
        self.integration_var.set(self.decimal_text(updated))
        self.commit_current_integration_preset()

    def adjust_averages(self, direction: int) -> None:
        try:
            current = int(self.averages_var.get())
        except ValueError:
            current = 1
        self.averages_var.set(str(min(1000, max(1, current + direction))))

    def on_integration_unit_changed(self, _event=None) -> None:
        new_unit = self.integration_unit_var.get()
        old_unit = self._integration_unit_last
        if new_unit == old_unit:
            return
        try:
            current = Decimal(self.integration_var.get().strip())
        except InvalidOperation:
            current = Decimal(1 if new_unit == "s" else 1000)
        if old_unit == "ms" and new_unit == "s":
            if current < Decimal(1000):
                converted = Decimal(1)
            else:
                converted = (current / Decimal(1000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        elif old_unit == "s" and new_unit == "ms":
            converted = current * Decimal(1000)
        else:
            converted = current
        self._integration_unit_last = new_unit
        self._syncing_settings = True
        try:
            self.integration_var.set(self.decimal_text(converted))
        finally:
            self._syncing_settings = False
        self.on_setting_changed()
        self.commit_current_integration_preset()

    def integration_display_value(self, integration_us: int) -> str:
        milliseconds = Decimal(integration_us) / Decimal(1000)
        if self.integration_unit_var.get() == "s":
            if milliseconds < Decimal(1000):
                return "1"
            seconds = (milliseconds / Decimal(1000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            return self.decimal_text(seconds)
        return self.decimal_text(milliseconds)

    def sync_setting_values(self, settings: dict[str, Any], state_text: str = "参数已与仪器同步") -> None:
        self.applied_settings = {
            "integration_us": int(settings["integration_us"]),
            "averages": max(1, int(settings["averages"])),
            "boxcar": max(0, int(settings["boxcar"])),
            "interval_ms": max(1, int(settings.get("interval_ms", 80))),
        }
        self._syncing_settings = True
        try:
            self.integration_var.set(self.integration_display_value(self.applied_settings["integration_us"]))
            self.averages_var.set(str(self.applied_settings["averages"]))
            self.interval_var.set(str(self.applied_settings["interval_ms"]))
            self.boxcar_var.set(str(self.applied_settings["boxcar"]))
        finally:
            self._syncing_settings = False
        self.settings_state_var.set(state_text)

    def sync_device_option_values(self, settings: dict[str, Any]) -> None:
        self.device_capabilities = {
            "electric_dark": bool(settings.get("electric_dark_supported", False)),
            "nonlinearity": bool(settings.get("nonlinearity_supported", False)),
            "trigger": bool(settings.get("trigger_supported", False)),
            "light_source": bool(settings.get("light_source_supported", False)),
        }
        self._syncing_settings = True
        try:
            # Match OceanView's initial device defaults rather than inheriting
            # optional settings left behind by an earlier SDK session. These
            # values are written to the instrument and verified by the first
            # atomic configuration after connection.
            self.trigger_mode_var.set(TRIGGER_MODE_OPTIONS[0])
            self.electric_dark_var.set(self.device_capabilities["electric_dark"])
            self.nonlinearity_var.set(False)
            self.light_source_var.set(False)
            self.decouple_var.set(False)
        finally:
            self._syncing_settings = False
        self.update_advanced_control_states()

    def reset_device_capabilities(self) -> None:
        self.device_capabilities = {
            "electric_dark": False,
            "nonlinearity": False,
            "trigger": False,
            "light_source": False,
        }
        self._syncing_settings = True
        try:
            self.trigger_mode_var.set(TRIGGER_MODE_OPTIONS[0])
            self.electric_dark_var.set(True)
            self.nonlinearity_var.set(False)
            self.light_source_var.set(False)
            self.decouple_var.set(False)
        finally:
            self._syncing_settings = False
        self.update_advanced_control_states()

    def parse_setting_values(self) -> dict[str, int]:
        try:
            integration_value = Decimal(self.integration_var.get().strip())
            integration_ms = integration_value * (
                Decimal(1000) if self.integration_unit_var.get() == "s" else Decimal(1)
            )
            averages = int(self.averages_var.get())
            interval_ms = int(float(self.interval_var.get()))
            boxcar = int(self.boxcar_var.get())
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError from exc
        if integration_ms <= 0 or averages < 1 or interval_ms < 1 or boxcar < 0:
            raise ValueError
        integration_us = int((integration_ms * Decimal(1000)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if self.device_info:
            if not self.device_info.integration_min_us <= integration_us <= self.device_info.integration_max_us:
                raise ValueError(
                    f"积分时间范围为 {self.device_info.integration_min_us / 1000:g}–"
                    f"{self.device_info.integration_max_us / 1000:g} ms"
                )
        return {
            "integration_us": integration_us,
            "averages": averages,
            "boxcar": boxcar,
            "interval_ms": interval_ms,
        }

    def plot_x_values(self) -> tuple[list[float], str, str]:
        if self.current_workspace() in ("采集", "处理") and self.x_axis_var.get() == "像素索引":
            return [float(index) for index in range(len(self.wavelengths))], "像素索引", "pixel"
        return self.wavelengths, "波长 (nm)", "nm"

    def reset_plot_view(self) -> None:
        self.plot.set_interaction_mode("normal")
        self.plot.reset_view()
        if not self.device_info or self.current_workspace() not in ("采集", "处理"):
            return
        if self.x_axis_var.get() == "像素索引":
            self.xmin_var.set("0")
            self.xmax_var.set(str(max(0, self.device_info.pixels - 1)))
            self.range_name_var.set("像素范围")
            self.range_unit_var.set("pixel")
        else:
            self.xmin_var.set(f"{self.device_info.wavelength_min:.0f}")
            self.xmax_var.set(f"{self.device_info.wavelength_max:.0f}")
            self.range_name_var.set("波段")
            self.range_unit_var.set("nm")

    def on_x_axis_changed(self, _event=None) -> None:
        self.reset_plot_view()
        self.update_cursor_axis_labels()
        self.refresh_plot()

    def update_cursor_readout(self, x: float, y: float) -> None:
        if self.plot.interaction_mode == "crosshair":
            return
        if self.current_plot_uses_pixels():
            self.cursor_position_var.set(f"{x:.0f}")
        elif self.current_plot_uses_energy():
            self.cursor_position_var.set(f"{x:.4f}")
        else:
            self.cursor_position_var.set(f"{x:.2f}")
        self.cursor_var.set(f"强度: {y:.2f}" if math.isfinite(y) else "强度: 无效")

    def update_crosshair_readout(self, x: float, y: float) -> None:
        if self.plot.interaction_mode != "crosshair":
            return
        self.cursor_position_var.set(f"{x:.2f}")
        self.cursor_var.set(f"Y: {y:.2f}")

    def update_cursor_axis_labels(self) -> None:
        if self.current_plot_uses_pixels():
            self.cursor_axis_name_var.set("像素")
            self.cursor_unit_var.set("pixel")
        elif self.current_plot_uses_energy():
            self.cursor_axis_name_var.set("光子能量")
            self.cursor_unit_var.set("eV")
        else:
            self.cursor_axis_name_var.set("波长")
            self.cursor_unit_var.set("nm")

    def update_cursor_control_state(self) -> None:
        has_data = bool(self.plot.wavelengths and self.plot.values)
        crosshair_active = self.plot.interaction_mode == "crosshair"
        state = "normal" if has_data and not crosshair_active else "disabled"
        self.cursor_position_entry.configure(state=state)
        self.cursor_up_button.configure(state=state)
        self.cursor_down_button.configure(state=state)
        if crosshair_active:
            self.cursor_tool_name_var.set("十字架")
            self.cursor_axis_name_var.set("X")
            self.cursor_unit_var.set(
                "pixel" if self.current_plot_uses_pixels() else "eV" if self.current_plot_uses_energy() else "nm"
            )
            if self.plot.crosshair_position is None:
                self.cursor_position_var.set("")
                self.cursor_var.set("Y: —")
            else:
                self.update_crosshair_readout(*self.plot.crosshair_position)
        else:
            self.cursor_tool_name_var.set("数据游标")
            self.update_cursor_axis_labels()
            if has_data and self.plot.cursor_index is not None:
                index = self.plot.cursor_index
                self.update_cursor_readout(self.plot.wavelengths[index], self.plot.values[index])
        if not has_data:
            self.cursor_position_var.set("")
            self.cursor_var.set("Y: —" if crosshair_active else "光标: —")

    def apply_cursor_position(self, _event=None):
        if not self.plot.wavelengths:
            return "break"
        try:
            requested = float(self.cursor_position_var.get().strip())
            if not math.isfinite(requested):
                raise ValueError
        except ValueError:
            if self.plot.cursor_index is not None:
                index = self.plot.cursor_index
                self.update_cursor_readout(self.plot.wavelengths[index], self.plot.values[index])
            return "break"
        self.plot.set_cursor_nearest_x(requested)
        return "break"

    def step_data_cursor(self, direction: int) -> None:
        if not self.plot.step_cursor(direction):
            if self.plot.cursor_index == 0:
                self.status_var.set("数据游标已到第一个采样点")
            elif self.plot.cursor_index is not None:
                self.status_var.set("数据游标已到最后一个采样点")

    def set_sdk_selector_visible(self, visible: bool, enabled: bool = True) -> None:
        if visible:
            if not self.sdk_select_button.winfo_manager():
                self.sdk_select_button.pack(side="left", padx=(8, 0))
            self.sdk_select_button.configure(
                text="选择 OceanDirect…" if enabled else "正在加载…",
                state="normal" if enabled else "disabled",
            )
        elif self.sdk_select_button.winfo_manager():
            self.sdk_select_button.pack_forget()

    def choose_oceandirect_home(self) -> None:
        from tkinter import filedialog, messagebox

        initial = self.sdk_home if self.sdk_home.is_dir() else Path(os.environ.get("ProgramFiles", Path.home()))
        selected = filedialog.askdirectory(
            title="选择 OceanDirect SDK 根目录",
            initialdir=str(initial),
            mustexist=True,
            parent=self.root,
        )
        if not selected:
            return

        chosen = self.sdk_normalizer(selected)
        possible_roots = [chosen, *list(chosen.parents)[:3]]
        sdk_home = next((candidate for candidate in possible_roots if self.sdk_validator(candidate)), None)
        if sdk_home is None:
            messagebox.showerror(
                "OceanDirect 目录无效",
                "所选目录中没有找到完整的 OceanDirect SDK。\n\n"
                "请选择包含以下文件的 SDK 根目录：\n"
                "Python\\oceandirect\\OceanDirectAPI.py\n"
                "Python\\oceandirect\\lib\\OceanDirect.dll",
                parent=self.root,
            )
            return
        self.activate_oceandirect_home(sdk_home)

    def activate_oceandirect_home(self, sdk_home: Path) -> None:
        """Replace a failed SDK worker after a manually selected installation validates."""
        if self.closing:
            return
        if self.worker.is_alive():
            self.sdk_footer_var.set(f"SDK: {sdk_home}")
            self.set_sdk_selector_visible(True, enabled=False)
            self.root.after(100, lambda: self.activate_oceandirect_home(sdk_home))
            return

        self.sdk_home = sdk_home
        self.sdk_available = False
        self.connection_pending = False
        self.sdk_footer_var.set(f"SDK: {self.sdk_home}")
        self.set_sdk_selector_visible(True, enabled=False)
        self.status_var.set("正在加载所选 OceanDirect SDK…")
        self.save_preferences()
        self.events = CoalescingEventQueue()
        self.worker = self.service_factory(self.sdk_home, self.events)
        self.worker.start()
        self.update_connection_buttons()

    def toggle_connection(self) -> None:
        if self.connected and not self.simulation_mode:
            self.connection_pending = True
            self.update_acquisition_buttons()
            self.status_var.set("正在断开仪器…")
            self.connect_button.configure(text="断开中…", state="disabled", style="Connected.TButton")
            self.worker.send("disconnect")
            return
        if self.connection_pending or not self.sdk_available:
            return
        self.connection_pending = True
        if self.single_in_progress:
            self.cancel_single_acquisition()
        if self.simulation_mode and self.acquiring:
            self.cancel_simulation_timer()
            self.set_acquisition_state(False, announce=False)
        if not self.simulation_mode:
            self.device_status_label.configure(text="正在连接", style="DevicePending.TLabel")
        self.status_var.set("正在搜索真实仪器，连接成功后将自动退出模拟模式…" if self.simulation_mode else "正在连接仪器…")
        self.update_connection_buttons()
        self.worker.send("connect")

    def toggle_simulation_mode(self) -> None:
        if self.closing or self.connection_pending or (self.connected and not self.simulation_mode):
            return
        if self.simulation_mode:
            self.exit_simulation_mode()
        else:
            self.enter_simulation_mode()

    def enter_simulation_mode(self) -> None:
        if self.connected or self.connection_pending:
            return
        self.clear_single_capture_history()
        simulated = self.simulation_spectrometer.connect()
        info = simulated.info
        self.simulation_mode = True
        self.connected = True
        self.device_info = info
        self.wavelengths = list(simulated.wavelengths)
        self.raw_spectrum = []
        self.reference_spectrum = None
        self.frame_context = None
        self.reference_context = None
        self.reference_timestamp = None
        self.spectrum_service = SpectrumService()
        self.mode_var.set("原始强度")
        self.last_timestamp = None
        self.frame_times.clear()
        self.simulation_frame_index = 0
        try:
            self.simulation_settings = self.parse_setting_values()
        except (TypeError, ValueError):
            self.simulation_settings = SIMULATION_SETTINGS.copy()
        self.apply_simulation_configuration(self.simulation_settings)
        self.sync_setting_values(self.simulation_settings, "模拟参数已应用")
        self.reset_device_capabilities()
        self.device_var.set("QE65000 · SIMULATION")
        self.device_status_label.configure(text="模拟模式", style="DeviceSim.TLabel")
        self.acquiring = False
        self.reset_acquisition_indicator()
        self.set_controls(True)
        self.update_baseline_state()
        self.update_connection_buttons()
        self.reset_plot_view()
        self.apply_range()
        self.accept_spectrum(self.generate_simulated_spectrum(), time.time())
        self.status_var.set("模拟模式已就绪，已生成单帧模拟光谱（未访问 USB）")

    def exit_simulation_mode(self, switching_to_device: bool = False) -> None:
        if not self.simulation_mode:
            return
        self.cancel_simulation_timer()
        if self.single_in_progress:
            self.cancel_single_acquisition()
        self.simulation_mode = False
        self.simulation_spectrometer.disconnect()
        self.clear_single_capture_history()
        self.simulation_active_context = None
        self.connected = False
        self.acquiring = False
        self.device_info = None
        self.wavelengths = []
        self.raw_spectrum = []
        self.reference_spectrum = None
        self.frame_context = None
        self.reference_context = None
        self.reference_timestamp = None
        self.spectrum_service = SpectrumService()
        self.mode_var.set("原始强度")
        self.last_timestamp = None
        self.frame_times.clear()
        self.device_var.set("")
        self.device_status_label.configure(text="未连接", style="DeviceOff.TLabel")
        self.reset_acquisition_indicator()
        self.settings_state_var.set("连接仪器或进入模拟模式后可应用参数")
        self.update_baseline_state()
        self.set_controls(False)
        self.reset_device_capabilities()
        self.plot.reset_view()
        self.refresh_plot()
        if switching_to_device:
            return
        self.connection_pending = False
        self.status_var.set("已退出模拟模式")
        self.update_connection_buttons()

    def simulation_delay_ms(self) -> int:
        settings = self.simulation_settings
        acquisition_ms = settings["integration_us"] / 1000.0 * settings["averages"]
        return max(settings["interval_ms"], int(math.ceil(acquisition_ms)))

    def cancel_simulation_timer(self) -> None:
        if self.simulation_after_id is None:
            return
        try:
            self.root.after_cancel(self.simulation_after_id)
        except Exception:
            pass
        self.simulation_after_id = None

    def set_status_lamp(self, color: str, outline: str | None = None) -> None:
        self.status_lamp_color = color
        self.status_lamp.itemconfigure(
            self.status_lamp_dot,
            fill=color,
            outline=outline or color,
        )

    def reset_acquisition_indicator(
        self,
        state_text: str = "采集未运行",
        lamp_color: str = "#94a3b8",
        label_style: str = "RunIdle.TLabel",
    ) -> None:
        self.acquisition_progress.stop()
        self.acquisition_progress.configure(
            mode="determinate",
            style="Acquisition.Horizontal.TProgressbar",
        )
        self.single_progress_var.set(0.0)
        self.run_state_var.set(state_text)
        self.run_state_label.configure(style=label_style)
        self.set_status_lamp(lamp_color)

    def cancel_single_timers(self) -> None:
        for attribute in ("single_progress_after_id", "simulation_single_after_id"):
            after_id = getattr(self, attribute)
            if after_id is None:
                continue
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            setattr(self, attribute, None)

    def cancel_single_acquisition(self) -> None:
        self.cancel_single_timers()
        self.single_in_progress = False
        self.single_started_at = None
        self.single_expected_s = 0.0
        self.active_acquisition_request_id = None
        self.acquisition_pending = False
        self.update_acquisition_buttons()
        self.reset_acquisition_indicator()

    def estimated_single_duration_s(self, settings: dict[str, int] | None = None) -> float:
        effective = settings or self.applied_settings
        integration_s = max(0.0, int(effective["integration_us"]) / 1_000_000.0)
        averages = max(1, int(effective["averages"]))
        return max(0.12, integration_s * averages)

    def begin_single_acquisition(self, settings: dict[str, int] | None = None) -> None:
        self.cancel_single_timers()
        self.single_in_progress = True
        self.single_started_at = time.monotonic()
        self.single_expected_s = self.estimated_single_duration_s(settings)
        self.frame_times.clear()
        self.acquisition_progress.stop()
        self.acquisition_progress.configure(
            mode="determinate",
            style="Acquisition.Horizontal.TProgressbar",
        )
        self.single_progress_var.set(0.0)
        self.run_state_var.set("单次采集中")
        self.run_state_label.configure(style="RunPending.TLabel")
        self.set_status_lamp("#0f6cbd")
        self.update_acquisition_buttons()
        self.single_progress_after_id = self.root.after(40, self.update_single_progress)

    def update_single_progress(self) -> None:
        self.single_progress_after_id = None
        if not self.single_in_progress or self.single_started_at is None:
            return
        elapsed = time.monotonic() - self.single_started_at
        progress = min(95.0, 95.0 * elapsed / max(0.001, self.single_expected_s))
        self.single_progress_var.set(progress)
        if elapsed > self.single_expected_s + 1.0:
            self.status_var.set(f"单次采集仍在等待仪器返回，已等待 {elapsed:.1f} 秒；可取消本次数据并等待设备就绪")
        self.single_progress_after_id = self.root.after(40, self.update_single_progress)

    def complete_simulated_single(self) -> None:
        self.simulation_single_after_id = None
        if not self.single_in_progress or not self.simulation_mode or self.closing:
            return
        self.accept_spectrum(self.generate_simulated_spectrum(), time.time())

    def finish_single_acquisition(self, success: bool = True) -> None:
        if not self.single_in_progress:
            return
        self.cancel_single_timers()
        self.single_in_progress = False
        self.single_started_at = None
        self.single_expected_s = 0.0
        self.active_acquisition_request_id = None
        self.acquisition_pending = False
        self.acquisition_progress.stop()
        self.acquisition_progress.configure(
            mode="determinate",
            style=(
                "AcquisitionComplete.Horizontal.TProgressbar"
                if success
                else "Acquisition.Horizontal.TProgressbar"
            ),
        )
        self.single_progress_var.set(100.0 if success else 0.0)
        if success:
            completion_time = datetime.now().strftime("%H:%M:%S")
            self.run_state_var.set("单次采集完成")
            self.run_state_label.configure(style="RunActive.TLabel")
            self.set_status_lamp("#198754")
            completion_text = "模拟单次采集完成" if self.simulation_mode else "单次采集完成"
            self.status_var.set(f"{completion_text} · 完成时间 {completion_time}")
        else:
            self.run_state_var.set("采集失败")
            self.run_state_label.configure(style="RunError.TLabel")
            self.set_status_lamp("#d13438")
        self.update_acquisition_buttons()

    def simulation_tick(self) -> None:
        self.simulation_after_id = None
        if not self.simulation_mode or not self.acquiring or self.closing:
            return
        self.accept_spectrum(self.generate_simulated_spectrum(), time.time())
        self.simulation_after_id = self.root.after(self.simulation_delay_ms(), self.simulation_tick)

    def generate_simulated_spectrum(self) -> list[float]:
        if self.simulation_active_context is None:
            self.apply_simulation_configuration(self.simulation_settings)
        frame = self.simulation_spectrometer.acquire(self.simulation_active_context)
        return list(frame.values)

    def apply_simulation_configuration(self, settings: dict[str, int]) -> None:
        request = AcquisitionRequest(
            integration_us=settings["integration_us"],
            averages=settings["averages"],
            boxcar=settings["boxcar"],
            interval_ms=settings["interval_ms"],
        )
        self.simulation_active_context = self.simulation_spectrometer.apply_configuration(request)

    def set_acquisition_state(self, running: bool, announce: bool = True) -> None:
        was_acquiring = self.acquiring
        self.acquiring = running
        if running and not was_acquiring:
            self.frame_times.clear()
        self.update_acquisition_buttons()
        self.run_state_var.set("连续采集中" if running else "采集已暂停")
        self.run_state_label.configure(style="RunActive.TLabel" if running else "RunIdle.TLabel")
        self.acquisition_progress.stop()
        if running:
            self.acquisition_progress.configure(
                mode="indeterminate",
                style="Acquisition.Horizontal.TProgressbar",
            )
            self.single_progress_var.set(0.0)
            self.acquisition_progress.start(14)
            self.set_status_lamp("#198754")
        else:
            self.acquisition_progress.configure(
                mode="determinate",
                style="Acquisition.Horizontal.TProgressbar",
            )
            self.single_progress_var.set(0.0)
            self.set_status_lamp("#d97706")
        if announce:
            if self.simulation_mode:
                self.status_var.set("模拟光谱连续生成中…" if running else "模拟采集已暂停")
            else:
                self.status_var.set("连续采集中…" if running else "采集已暂停")

    def simulation_context(self) -> SpectrumContext:
        if self.simulation_active_context is None:
            self.apply_simulation_configuration(self.simulation_settings)
        return self.simulation_active_context

    def accept_spectrum(
        self,
        spectrum: list[float],
        timestamp: float,
        context: SpectrumContext | None = None,
        request_id: str | None = None,
    ) -> None:
        if self.cancel_requested:
            logger.info("Discarding spectrum after cancellation: request=%s", request_id)
            return
        if not self.simulation_mode and request_id != self.active_acquisition_request_id:
            logger.info("Discarding stale spectrum: request=%s active=%s", request_id, self.active_acquisition_request_id)
            return
        if len(spectrum) != len(self.wavelengths):
            self.status_var.set(
                f"已忽略长度不匹配的光谱：{len(spectrum)} 点，当前波长轴 {len(self.wavelengths)} 点"
            )
            return
        if context is None and self.simulation_mode:
            context = self.simulation_context()
        if context is None:
            self.status_var.set("已忽略缺少采集配置标识的光谱")
            return

        completes_single = self.single_in_progress
        frame = SpectrumFrame(tuple(spectrum), timestamp, context)
        cleared = list(self.spectrum_service.accept(frame))
        self.raw_spectrum = list(spectrum)
        self.frame_context = context
        if completes_single:
            self.register_single_capture_candidate(frame)
        else:
            self._displayed_single_capture_id = None
            self._show_single_capture_history = False
        if self.spectrum_service.baselines.reference is None:
            self.reference_spectrum = None
            self.reference_context = None
            self.reference_timestamp = None
        self.mark_save_pending()
        self.last_timestamp = timestamp
        self.frame_times.append(time.monotonic())
        self.frame_times = self.frame_times[-20:]
        self.refresh_plot()
        if cleared:
            self.update_baseline_state()
            self.status_var.set(f"采集配置已变化，已自动清除{'和'.join(cleared)}")
        if completes_single:
            self.finish_single_acquisition(success=True)

    def start_acquisition(self) -> None:
        if self.connected and not self.connection_pending and not self.acquiring and not self.single_in_progress and not self.acquisition_pending:
            settings = self.prepare_settings_for_acquisition()
            if settings is None:
                return
            self._show_single_capture_history = False
            self._displayed_single_capture_id = None
            self.refresh_plot()
            if self.simulation_mode:
                self.set_acquisition_state(True)
                self.simulation_tick()
                return
            self.status_var.set("正在同步当前参数并开始连续采集…")
            self.send_configuration("configure_start", settings)

    def single_acquisition(self) -> None:
        if self.connected and not self.connection_pending and not self.acquiring and not self.single_in_progress and not self.acquisition_pending:
            settings = self.prepare_settings_for_acquisition()
            if settings is None:
                return
            self.begin_single_acquisition(settings)
            if self.simulation_mode:
                delay_ms = max(1, int(math.ceil(self.single_expected_s * 1000)))
                self.simulation_single_after_id = self.root.after(delay_ms, self.complete_simulated_single)
                self.status_var.set(f"模拟单次采集中，预计 {self.single_expected_s:.2f} 秒")
                return
            self.status_var.set(f"正在采集单帧光谱，预计至少 {self.single_expected_s:.2f} 秒…")
            self.send_configuration("configure_single", settings)

    def send_configuration(self, command: str, settings: dict[str, int]) -> None:
        self.cancel_requested = False
        self.active_acquisition_request_id = str(self._next_acquisition_request_id)
        self._next_acquisition_request_id += 1
        self.acquisition_pending = True
        self.update_acquisition_buttons()
        self.worker.send(command, request_id=self.active_acquisition_request_id, **self.acquisition_payload(settings))

    def stop_acquisition(self) -> None:
        if self.single_in_progress and self.simulation_mode:
            self.cancel_single_acquisition()
            self.run_state_var.set("单次采集已取消")
            self.run_state_label.configure(style="RunIdle.TLabel")
            self.set_status_lamp("#d97706")
            self.status_var.set("模拟单次采集已取消；未生成或保存本次数据")
            return
        if self.cancel_requested:
            return
        if not self.simulation_mode and (self.single_in_progress or self.acquisition_pending or self.acquiring):
            self.cancel_requested = True
            self.acquisition_pending = True
            self.cancel_single_timers()
            self.update_acquisition_buttons()
            waiting = "正在取消单次" if self.single_in_progress else "正在暂停"
            self.run_state_var.set(waiting)
            self.run_state_label.configure(style="RunPending.TLabel")
            self.status_var.set(f"{waiting}，等待当前扫描或参数同步结束；不再继续剩余平均，仪器保持连接…")
            self.worker.send("stop", request_id=self.active_acquisition_request_id)
            return
        if self.acquiring and self.simulation_mode:
            self.cancel_simulation_timer()
            self.set_acquisition_state(False)

    def prepare_settings_for_acquisition(self) -> dict[str, int] | None:
        """Validate the visible settings; hardware application is one atomic worker command."""
        from tkinter import messagebox

        try:
            settings = self.parse_setting_values()
        except ValueError as exc:
            messagebox.showerror(
                "参数错误",
                str(exc) or "请输入有效的积分时间、平均次数、刷新间隔和平滑宽度。",
            )
            return None
        self.commit_current_integration_preset()

        if self.simulation_mode:
            self.simulation_settings = settings
            self.apply_simulation_configuration(settings)
            self.sync_setting_values(settings, "模拟参数已自动应用")
            return settings

        self.settings_state_var.set("采集前正在同步当前参数…")
        return settings

    def acquisition_payload(self, settings: dict[str, int]) -> dict[str, Any]:
        return {
            **settings,
            "electric_dark": bool(self.electric_dark_var.get()),
            "nonlinearity": bool(self.nonlinearity_var.get()),
            "trigger_mode": TRIGGER_MODE_TO_VALUE.get(self.trigger_mode_var.get(), 0),
            "light_source": bool(self.light_source_var.get()),
            "apply_electric_dark": self.device_capabilities["electric_dark"],
            "apply_nonlinearity": self.device_capabilities["nonlinearity"],
            "apply_trigger": self.device_capabilities["trigger"],
            "apply_light_source": self.device_capabilities["light_source"],
        }

    def apply_settings(self) -> None:
        from tkinter import messagebox

        if self.single_in_progress or self.acquisition_pending:
            self.status_var.set("单次采集完成后再应用参数")
            return
        if self.acquiring and not self.simulation_mode:
            self.status_var.set("请先暂停连续采集，再应用仪器参数")
            return
        try:
            settings = self.parse_setting_values()
            self.commit_current_integration_preset()
            if self.simulation_mode:
                self.simulation_settings = settings
                self.apply_simulation_configuration(settings)
                self.sync_setting_values(settings, "模拟参数已应用")
                if self.acquiring:
                    self.cancel_simulation_timer()
                    self.simulation_tick()
                elif self.raw_spectrum:
                    self.frame_times.clear()
                    self.accept_spectrum(self.generate_simulated_spectrum(), time.time())
                self.status_var.set("模拟采集参数已应用")
                return
            self.send_configuration("configure", settings)
            self.status_var.set("正在应用采集参数…")
            self.settings_state_var.set("正在同步参数…")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc) or "请输入有效的积分时间、平均次数、刷新间隔和平滑宽度。")

    def apply_corrections(self) -> None:
        if not self.simulation_mode and self.connected:
            self.apply_settings()

    def apply_device_options(self) -> None:
        if not self.simulation_mode and self.connected:
            self.apply_settings()

    def capture_reference(self) -> None:
        from tkinter import messagebox

        if not self.raw_spectrum or self.frame_context is None:
            messagebox.showwarning("没有数据", "请先采集一帧光谱。")
            return
        self.reference_spectrum = self.raw_spectrum.copy()
        self.reference_context = self.frame_context
        self.reference_timestamp = self.last_timestamp
        self.spectrum_service.capture_reference()
        self.mark_save_pending()
        self.status_var.set("已记录参考谱")
        self.update_baseline_state()
        self.refresh_plot()

    def clear_baselines(self) -> None:
        self.reference_spectrum = None
        self.reference_context = None
        self.reference_timestamp = None
        self.spectrum_service.clear_baselines()
        self.mark_save_pending()
        self.status_var.set("已清除参考谱")
        self.update_baseline_state()
        self.refresh_plot()

    def update_baseline_state(self) -> None:
        reference_recorded = self.reference_spectrum is not None
        self.baseline_state_var.set("参考谱：已记录" if reference_recorded else "参考谱：未记录")
        if hasattr(self, "baseline_state_label"):
            self.baseline_state_label.configure(
                style="BaselineReady.TLabel" if reference_recorded else "BaselineEmpty.TLabel"
            )
            self.processing_reference_button.configure(
                style="Selected.TButton" if reference_recorded else "TButton"
            )

    def processed_spectrum(self) -> tuple[list[float], str]:
        workspace = self.current_workspace()
        if workspace == "采集":
            return list(self.raw_spectrum), "强度 (counts)"
        if workspace == "处理":
            mode = self.mode_var.get()
            if mode == "峰值归一化":
                try:
                    return list(normalize_peak(self.raw_spectrum)), "归一化强度"
                except ValueError:
                    return [], "归一化强度"
            if mode == "原始强度":
                return list(self.raw_spectrum), "强度 (counts)"
            exponent = tauc_exponent(mode)
            if exponent is not None:
                absorbance, label = self.spectrum_service.processed(ProcessingMode.ABSORBANCE)
                if not absorbance:
                    return [], label
                values, _invalid = calculate_tauc_values(self.wavelengths, absorbance, exponent)
                return list(values), tauc_label(exponent)
            return self.spectrum_service.processed(ProcessingMode(mode))
        return [], "强度 (counts)"

    def preferred_plot_y_bounds(self, label: str) -> tuple[float, float] | None:
        if "counts" in label.lower():
            saturation = self.saturation_limit()
            return 0.0, float(saturation + 50.0)
        if label == "透过率 (%)":
            return 0.0, 100.0
        if label == "吸光度 (A)":
            return 0.0, 3.0
        if label == "归一化强度":
            return 0.0, 1.05
        return None

    def saturation_limit(self) -> float:
        """Return the detector's usable saturation reference in counts."""
        if self.device_info is None:
            return 65_535.0
        candidate = self.device_info.saturation_intensity
        if candidate is not None and math.isfinite(candidate) and candidate > 0:
            return float(candidate)
        if self.device_info.max_intensity > 0 and math.isfinite(self.device_info.max_intensity):
            return float(self.device_info.max_intensity)
        return 65_535.0

    def refresh_plot(self) -> None:
        if not hasattr(self, "plot"):
            return
        workspace = self.current_workspace()
        historical = False
        if workspace == "PL荧光":
            self.plot.set_empty_message("尚未载入 PL 文件", "点击左侧“选择 PL 文件”，可一次选择多个 CSV/TXT")
            result = self.pl_result_service.build_plot(
                bool(self.pl_normalize_var.get()),
                bool(self.pl_turbo_var.get()),
            )
            label = "归一化强度" if self.pl_normalize_var.get() else "强度 (counts)"
            self.plot.set_series(list(result.series), label, "波长 (nm)", self.preferred_plot_y_bounds(label))
            historical = True
            if result.skipped:
                skipped = "；".join(f"{name}：{reason}" for name, reason in result.skipped[:3])
                self.pl_status_var.set(f"已显示 {len(result.series)} 条曲线；未显示 {len(result.skipped)} 条（{skipped}）")
            elif self.pl_result_service.entries:
                self.pl_status_var.set(f"已显示 {len(result.series)} 条 PL 曲线")
        elif workspace == "AB吸收":
            self.update_ab_offset_status()
            self.plot.set_empty_message("尚未载入 AB 数据", "选择本组样品信号和 BG 参考")
            mode = self.ab_mode_var.get()
            exponent = tauc_exponent(mode)
            tauc = exponent is not None
            result = self.historical_absorbance_service.build_plot(
                mode != "透过率 (%)", tauc=tauc, tauc_exponent=exponent if tauc else 2.0)
            self.ab_active_var.set(self.historical_absorbance_service.active_id or "")
            label = tauc_label(exponent) if tauc else mode
            series = list(result.series)
            x_label = "波长 (nm)"
            if self.ab_x_axis_var.get() == "光子能量 (eV)":
                energy_series: list[PlotSeries] = []
                for item in series:
                    points: list[tuple[float, float]] = []
                    for wavelength, value in zip(item.wavelengths, item.values):
                        energy = photon_energy_ev(wavelength)
                        if math.isfinite(energy):
                            points.append((energy, value))
                    points.sort()
                    energy_series.append(replace(
                        item,
                        wavelengths=tuple(point[0] for point in points),
                        values=tuple(point[1] for point in points),
                    ))
                series = energy_series
                x_label = "光子能量 (eV)"
            self.plot.set_series(series, label, x_label)
            historical = True
            displayed = sum(any(math.isfinite(value) for value in item.values) for item in result.series)
            current_group = self.historical_absorbance_service.current_group
            current_hint = ""
            if not current_group.entries:
                current_hint = f"{current_group.name}等待样品；"
            elif current_group.reference is None:
                current_hint = f"{current_group.name}等待 BG；"
            if not self.historical_absorbance_service.entries:
                self.ab_result_status_var.set("请选择一份或多份带样品信号")
            elif result.skipped:
                skipped = "；".join(f"{name}：{reason}" for name, reason in result.skipped[:3])
                self.ab_result_status_var.set(f"{current_hint}已显示 {displayed} 条；未显示 {len(result.skipped)} 条（{skipped}）")
            elif result.series:
                invalid = sum(count for _name, count in result.invalid_counts)
                self.ab_result_status_var.set(f"{current_hint}已显示 {displayed} 条 {label} 曲线；无效点共 {invalid}")
            else:
                self.ab_result_status_var.set("样品曲线均已隐藏")
            if result.signal_warnings:
                warnings = dict(result.signal_warnings)
                active_name = next((item.name for item in result.series if item.active), "")
                warning = warnings.get(active_name, result.signal_warnings[0][1])
                self.ab_result_status_var.set(self.ab_result_status_var.get() +
                                              f"\n{warning}；请检查原始信号")
        else:
            if workspace == "采集" and self._show_single_capture_history:
                series = self.active_preset_single_capture_series()
                self.plot.set_empty_message(
                    "当前预设尚无单次数据",
                    "执行单次采集；保存成功后才会进入最近三次历史",
                )
                x_label = "像素索引" if self.x_axis_var.get() == "像素索引" else "波长 (nm)"
                self.plot.set_series(
                    series,
                    "强度 (counts)",
                    x_label,
                    self.preferred_plot_y_bounds("强度 (counts)"),
                )
            else:
                self.plot.set_empty_message("等待光谱数据", "连接仪器或进入模拟模式，然后开始连续采集或执行单次采集")
                values, label = self.processed_spectrum()
                x_values, x_label, _x_unit = self.plot_x_values()
                self.plot.set_data(
                    x_values,
                    values,
                    label,
                    x_label,
                    self.preferred_plot_y_bounds(label),
                    metadata=self.curve_context_metadata(self.frame_context, self.last_timestamp),
                )
        self.refresh_curve_save_state()
        self.update_single_history_warning(workspace)
        self.update_cursor_control_state()
        finite = [(x, y) for x, y in zip(self.plot.wavelengths, self.plot.values) if math.isfinite(y)]
        if finite:
            peak_x, peak_y = max(finite, key=lambda p: p[1])
            fps = self.current_fps()
            frame_text = "历史文件" if historical else f"{fps:.2f} fps" if fps > 0 else "单帧"
            if self.current_plot_uses_pixels():
                peak_text = f"pixel {peak_x:.0f}"
            elif self.current_plot_uses_energy():
                peak_text = f"{peak_x:.4f} eV"
            else:
                peak_text = f"{peak_x:.2f} nm"
            peak_metric = f"峰值: {peak_y:.2f}"
            position_metric = f"峰位: {peak_text}"
            frame_metric = f"帧率: {frame_text}"
            self.metrics_var.set(f"{peak_metric}    {position_metric}    {frame_metric}")
            self.peak_metric_var.set(peak_metric)
            self.position_metric_var.set(position_metric)
            self.frame_metric_var.set(frame_metric)
            self.peak_value_var.set(f"{peak_y:.2f}")
            self.position_value_var.set(peak_text)
            self.frame_value_var.set(frame_text)
        else:
            self.metrics_var.set("峰值: —    峰位: —    帧率: —")
            self.peak_metric_var.set("峰值: —")
            self.position_metric_var.set("峰位: —")
            self.frame_metric_var.set("帧率: —")
            self.peak_value_var.set("—")
            self.position_value_var.set("—")
            self.frame_value_var.set("—")

        raw_values = [value for value in self.raw_spectrum if math.isfinite(value)]
        saturation_limit = self.saturation_limit()
        if not historical and raw_values and saturation_limit > 0:
            ratio = max(raw_values) / saturation_limit * 100.0
            self.saturation_var.set(f"饱和度: {ratio:.1f}%")
            if ratio >= 95:
                self.saturation_label.configure(style="MetricDanger.TLabel")
            elif ratio >= 80:
                self.saturation_label.configure(style="MetricWarn.TLabel")
            else:
                self.saturation_label.configure(style="MetricSafe.TLabel")
        else:
            self.saturation_var.set("饱和度: —")
            self.saturation_label.configure(style="Metric.TLabel")

    def current_fps(self) -> float:
        if len(self.frame_times) < 2:
            return 0.0
        elapsed = self.frame_times[-1] - self.frame_times[0]
        return (len(self.frame_times) - 1) / elapsed if elapsed > 0 else 0.0

    def apply_range(self) -> None:
        from tkinter import messagebox

        try:
            xmin = float(self.xmin_var.get()) if self.xmin_var.get().strip() else None
            xmax = float(self.xmax_var.get()) if self.xmax_var.get().strip() else None
            if xmin is not None and xmax is not None and xmin >= xmax:
                raise ValueError
            self.plot.x_min = xmin
            self.plot.x_max = xmax
            self.plot.y_min = None
            self.plot.y_max = None
            self.refresh_plot()
        except ValueError:
            messagebox.showerror("范围错误", "请输入有效范围，且起始值应小于结束值。")

    @staticmethod
    def sanitize_base_name(value: str) -> str:
        invalid = '<>:"/\\|?*'
        cleaned = "".join("_" if character in invalid or ord(character) < 32 else character for character in value.strip())
        cleaned = cleaned.rstrip(". ")
        if not cleaned:
            cleaned = "spectrum"
        if cleaned.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
            cleaned = f"_{cleaned}"
        return cleaned

    def adjust_position_number(self, direction: int) -> None:
        """Adjust the generic trailing number while preserving zero padding."""
        current = self.position_var.get().strip()
        if not current:
            if direction < 0:
                self.status_var.set("末尾编号当前为空")
                return
            self.position_var.set("01")
            self.status_var.set("末尾编号已调整为：01")
            return
        try:
            number = max(0, int(current))
        except ValueError:
            number = 1
            current = "01"
        updated = max(0, number + direction)
        if direction < 0 and number <= 1:
            self.position_var.set("")
            self.status_var.set("末尾编号已清空")
            return
        width = max(2, len(current))
        self.position_var.set(str(updated).zfill(width))
        self.status_var.set(f"末尾编号已调整为：{self.position_var.get()}")

    @staticmethod
    def save_extension(save_format: str) -> str:
        return ".txt" if save_format == SAVE_FORMAT_TXT else ".csv"

    def save_instrument_token(self) -> str:
        serial = ""
        if self.device_info is not None:
            serial = self.device_info.serial.strip()
        elif self.frame_context is not None:
            serial = self.frame_context.serial.strip()
        serial = serial or "INSTRUMENT"
        return f"{serial}1"

    def compose_save_base_name(
        self,
        variable: str | None = None,
        name_prefix: str | None = None,
        name_suffix: str | None = None,
        integration_value: str | None = None,
        integration_unit: str | None = None,
        averages: str | None = None,
        position: str | None = None,
    ) -> str:
        """Compose fixed/variable name segments and expand live acquisition placeholders."""
        value_text = integration_value if integration_value is not None else self.integration_var.get()
        unit_text = integration_unit if integration_unit is not None else self.integration_unit_var.get()
        averages_text = averages if averages is not None else self.averages_var.get()
        position_text = position if position is not None else self.position_var.get()
        try:
            integration_token = f"{self.decimal_text(Decimal(value_text.strip()))}{unit_text}"
        except (InvalidOperation, AttributeError, ValueError):
            integration_token = f"{value_text.strip()}{unit_text}"
        try:
            averages_token = f"{max(1, int(averages_text.strip()))}times"
        except (AttributeError, TypeError, ValueError):
            averages_token = f"{averages_text.strip()}times"
        try:
            stripped_position = position_text.strip()
            clean_position = (
                str(max(0, int(stripped_position))).zfill(max(2, len(stripped_position)))
                if stripped_position
                else ""
            )
        except (AttributeError, TypeError, ValueError):
            clean_position = position_text.strip()

        replacements = {
            "{integration}": integration_token,
            "{averages}": averages_token,
            "{number}": clean_position,
            "{position}": clean_position,
        }
        prefix_segment = self.name_prefix_var.get() if name_prefix is None else name_prefix
        variable_segment = self.base_name_var.get() if variable is None else variable
        suffix_segment = self.name_suffix_var.get() if name_suffix is None else name_suffix
        # The position number belongs to the variable itself (for example
        # ``sample01``), even when an older saved suffix still contains the
        # legacy {number}/{position} placeholder.
        variable_segment = f"{str(variable_segment).strip()}{clean_position}"
        raw_segments = (prefix_segment, variable_segment, suffix_segment)
        segments: list[str] = []
        for segment in raw_segments:
            expanded = str(segment).strip()
            for placeholder, replacement in replacements.items():
                if placeholder in ("{number}", "{position}"):
                    replacement = ""
                expanded = expanded.replace(placeholder, replacement)
            expanded = expanded.strip().strip("-").strip()
            if expanded:
                segments.append(expanded)
        combined = "-".join(segments)
        while "--" in combined:
            combined = combined.replace("--", "-")
        return self.sanitize_base_name(combined)

    def build_save_filename(
        self,
        base_name: str | None = None,
        save_format: str | None = None,
        suffix_mode: str | None = None,
        sequence_digits: int | None = None,
        sequence: int | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        base = self.sanitize_base_name(base_name) if base_name is not None else self.compose_save_base_name()
        file_format = save_format or self.save_format_var.get()
        suffix = suffix_mode or self.save_suffix_var.get()
        if suffix == SAVE_SUFFIX_NONE:
            return f"{base}{self.save_extension(file_format)}"
        if suffix == SAVE_SUFFIX_SEQUENCE:
            digits = sequence_digits if sequence_digits is not None else int(self.sequence_digits_var.get())
            number = sequence if sequence is not None else self.save_sequence
            suffix_text = str(max(1, number)).zfill(max(1, min(9, digits)))
        else:
            current = timestamp or datetime.now()
            if file_format == SAVE_FORMAT_TXT:
                time_text = current.strftime("%H-%M-%S-%f")[:-3]
                suffix_text = f"{self.save_instrument_token()}_{time_text}"
            else:
                suffix_text = current.strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return f"{base}_{suffix_text}{self.save_extension(file_format)}"

    def update_save_button_label(self) -> None:
        self.save_button_text_var.set("保存 TXT" if self.save_format_var.get() == SAVE_FORMAT_TXT else "保存 CSV")

    def refresh_save_verification_state(self) -> None:
        """Restore the independent saved-state color for the active preset."""
        self.save_verified = bool(
            self.preset_save_verified.get(self._active_integration_preset, False)
        )
        if hasattr(self, "save_button"):
            self.save_button.configure(
                style="Saved.TButton" if self.save_verified else "Primary.TButton"
            )

    def mark_save_pending(self, *_args) -> None:
        """Show that the current spectrum/settings have not passed file verification yet."""
        if getattr(self, "_syncing_settings", False):
            return
        self.preset_save_verified[self._active_integration_preset] = False
        self.refresh_save_verification_state()

    def mark_save_verified(self) -> None:
        """Green is reserved for a file that was written, reopened, and verified."""
        self.preset_save_verified[self._active_integration_preset] = True
        self.refresh_save_verification_state()

    def on_processing_mode_changed(self, _event=None) -> None:
        self.mark_save_pending()
        self.plot.y_min = self.plot.y_max = None
        self.refresh_plot()

    def open_save_settings(self) -> None:
        from tkinter import filedialog, messagebox

        if self.save_dialog is not None and self.save_dialog.winfo_exists():
            self.save_dialog.lift()
            self.save_dialog.focus_force()
            return

        dialog = self.tk.Toplevel(self.root)
        self.save_dialog = dialog
        dialog.title("保存设置")
        dialog.configure(bg="#ffffff")
        dialog.resizable(False, False)
        dialog.transient(self.root)

        draft_format = self.tk.StringVar(value=self.save_format_var.get())
        draft_directory = self.tk.StringVar(value=self.save_directory_var.get())
        draft_base = self.tk.StringVar(value=self.base_name_var.get())
        draft_name_prefix = self.tk.StringVar(value=self.name_prefix_var.get())
        draft_name_suffix = self.tk.StringVar(value=self.name_suffix_var.get())
        draft_position = self.tk.StringVar(value=self.position_var.get())
        draft_suffix = self.tk.StringVar(value=self.save_suffix_var.get())
        draft_digits = self.tk.StringVar(value=self.sequence_digits_var.get())
        preview_var = self.tk.StringVar()

        content = self.ttk.Frame(dialog, style="Card.TFrame", padding=18)
        content.pack(fill="both", expand=True)
        title_row = self.ttk.Frame(content, style="Card.TFrame")
        title_row.pack(fill="x", pady=(0, 14))
        self.ttk.Label(title_row, text="保存参数", style="Title.TLabel").pack(side="left")
        self.save_preset_hint_label = self.ttk.Label(
            title_row,
            text=f"（{INTEGRATION_PRESET_NAMES[self._active_integration_preset]}）",
            style="PresetHint.TLabel",
        )
        self.save_preset_hint_label.pack(side="left", padx=(6, 0), pady=(4, 0))

        file_box = self.ttk.LabelFrame(content, text="文件选项", style="Card.TLabelframe", padding=12)
        file_box.pack(fill="x")
        file_box.columnconfigure(1, weight=1)
        self.ttk.Label(file_box, text="文件格式", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=5)
        format_combo = self.ttk.Combobox(
            file_box,
            textvariable=draft_format,
            values=SAVE_FORMAT_OPTIONS,
            state="readonly",
            width=29,
        )
        format_combo.grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)
        self.ttk.Label(file_box, text="目标位置", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=5)
        directory_entry = self.ttk.Entry(file_box, textvariable=draft_directory, width=46)
        directory_entry.grid(row=1, column=1, sticky="ew", pady=5)

        def browse_directory() -> None:
            initial = draft_directory.get().strip()
            if not initial or not Path(initial).is_dir():
                initial = str(Path.home())
            chosen = filedialog.askdirectory(title="选择光谱保存文件夹", initialdir=initial, parent=dialog)
            if chosen:
                draft_directory.set(chosen)

        self.ttk.Button(file_box, text="浏览…", command=browse_directory).grid(row=1, column=2, padx=(8, 0), pady=5)
        self.ttk.Label(
            file_box,
            text="未设置目标位置时，点击保存仍会弹出文件选择窗口。",
            style="Hint.TLabel",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(3, 0))

        name_box = self.ttk.LabelFrame(content, text="文件名处理", style="Card.TLabelframe", padding=12)
        name_box.pack(fill="x", pady=(12, 0))
        name_box.columnconfigure(1, weight=1)
        self.ttk.Label(name_box, text="变量前固定参数", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=5
        )
        self.save_name_prefix_entry = self.ttk.Entry(name_box, textvariable=draft_name_prefix)
        self.save_name_prefix_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=5)
        self.ttk.Label(name_box, text="当前变量", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=5
        )
        self.save_current_variable_entry = self.ttk.Entry(name_box, textvariable=draft_base)
        self.save_current_variable_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=5)
        self.ttk.Label(name_box, text="变量后固定参数", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=5
        )
        self.save_name_suffix_entry = self.ttk.Entry(name_box, textvariable=draft_name_suffix)
        self.save_name_suffix_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)

        def restore_default_name_format() -> None:
            draft_name_suffix.set(DEFAULT_NAME_SUFFIX)

        self.save_name_default_button = self.ttk.Button(
            name_box,
            text="默认",
            style="Tool.TButton",
            command=restore_default_name_format,
        )
        self.save_name_default_button.grid(row=2, column=3, sticky="e", padx=(8, 0), pady=5)
        self.ttk.Label(
            name_box,
            text="可使用 {integration}、{averages} 和 {number}；例如 100ms、3times、02。",
            style="Hint.TLabel",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 6))
        self.ttk.Label(name_box, text="文件后缀", style="Card.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 12), pady=5)
        suffix_options = self.ttk.Frame(name_box, style="Card.TFrame")
        suffix_options.grid(row=4, column=1, columnspan=3, sticky="ew", pady=5)
        self.save_suffix_time_radio = LargeRadiobutton(
            suffix_options,
            text="按时间",
            variable=draft_suffix,
            value=SAVE_SUFFIX_TIME,
        )
        self.save_suffix_time_radio.pack(side="left", padx=(0, 14))
        self.save_suffix_sequence_radio = LargeRadiobutton(
            suffix_options,
            text="按序号",
            variable=draft_suffix,
            value=SAVE_SUFFIX_SEQUENCE,
        )
        self.save_suffix_sequence_radio.pack(side="left", padx=(0, 14))
        self.save_suffix_none_radio = LargeRadiobutton(
            suffix_options,
            text="无后缀",
            variable=draft_suffix,
            value=SAVE_SUFFIX_NONE,
        )
        self.save_suffix_none_radio.pack(side="left", padx=(0, 14))
        digits_row = self.ttk.Frame(suffix_options, style="Card.TFrame")
        digits_row.pack(side="right")
        self.ttk.Label(digits_row, text="位数", style="Card.TLabel").pack(side="left", padx=(0, 5))

        def adjust_draft_digits(direction: int) -> None:
            try:
                current = int(draft_digits.get())
            except ValueError:
                current = 5
            draft_digits.set(str(min(9, max(1, current + direction))))

        digits_shell, digits_entry, digits_up_button, digits_down_button = self.numeric_stepper_control(
            digits_row,
            draft_digits,
            adjust_draft_digits,
            width=86,
        )
        digits_shell.pack(side="left")
        self.save_sequence_digits_shell = digits_shell
        self.save_sequence_digits_entry = digits_entry
        self.save_sequence_digits_up_button = digits_up_button
        self.save_sequence_digits_down_button = digits_down_button

        preview_row = self.ttk.Frame(content, style="Card.TFrame")
        preview_row.pack(fill="x", pady=(14, 0))
        self.ttk.Label(preview_row, text="完整文件名预览", style="Card.TLabel").pack(anchor="w", pady=(0, 5))
        self.save_preview_label = self.ttk.Label(
            preview_row,
            textvariable=preview_var,
            style="Preview.TLabel",
            justify="left",
            anchor="w",
            wraplength=760,
        )
        self.save_preview_label.pack(fill="x")

        def update_preview(*_args) -> None:
            try:
                digits = min(9, max(1, int(draft_digits.get())))
            except ValueError:
                digits = 5
            preview_var.set(
                self.build_save_filename(
                    base_name=self.compose_save_base_name(
                        variable=draft_base.get(),
                        name_prefix=draft_name_prefix.get(),
                        name_suffix=draft_name_suffix.get(),
                        position=draft_position.get(),
                    ),
                    save_format=draft_format.get(),
                    suffix_mode=draft_suffix.get(),
                    sequence_digits=digits,
                    sequence=self.save_sequence,
                )
            )
            state = "normal" if draft_suffix.get() == SAVE_SUFFIX_SEQUENCE else "disabled"
            for widget in (digits_entry, digits_up_button, digits_down_button):
                widget.configure(state=state)

        for variable in (
            draft_format,
            draft_base,
            draft_name_prefix,
            draft_name_suffix,
            draft_position,
            draft_suffix,
            draft_digits,
        ):
            variable.trace_add("write", update_preview)
        update_preview()

        buttons = self.ttk.Frame(content, style="Card.TFrame")
        buttons.pack(fill="x", pady=(16, 0))

        def close_dialog() -> None:
            if dialog.winfo_exists():
                try:
                    dialog.grab_release()
                except Exception:
                    pass
                dialog.destroy()
            self.save_dialog = None

        def apply_save_settings() -> None:
            directory = draft_directory.get().strip()
            if directory and not Path(directory).is_dir():
                messagebox.showerror("目标位置无效", "请选择一个已存在的文件夹。", parent=dialog)
                return
            try:
                digits = min(9, max(1, int(draft_digits.get())))
            except ValueError:
                messagebox.showerror("序号位数无效", "序号位数应为 1–9。", parent=dialog)
                return
            self.base_name_var.set(self.sanitize_base_name(draft_base.get()))
            self.name_prefix_var.set(draft_name_prefix.get().strip())
            self.name_suffix_var.set(draft_name_suffix.get().strip())
            self.position_var.set(draft_position.get().strip())
            self.save_format_var.set(draft_format.get())
            self.save_directory_var.set(directory)
            self.save_suffix_var.set(draft_suffix.get())
            self.sequence_digits_var.set(str(digits))
            self.update_save_button_label()
            self.save_preferences()
            self.status_var.set("保存参数已应用")
            close_dialog()

        self.ttk.Button(buttons, text="取消", command=close_dialog).pack(side="right")
        self.save_settings_apply_button = self.ttk.Button(
            buttons,
            text="应用",
            style="Primary.TButton",
            command=apply_save_settings,
        )
        self.save_settings_apply_button.pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.update_idletasks()
        width = max(840, dialog.winfo_reqwidth() + 20)
        height = max(650, dialog.winfo_reqheight() + 24)
        width = min(width, max(640, dialog.winfo_screenwidth() - 80))
        height = min(height, max(560, dialog.winfo_screenheight() - 80))
        center_dialog(self.root, dialog, width, height)
        dialog.grab_set()
        dialog.focus_force()

    @staticmethod
    def curve_context_metadata(context, timestamp=None) -> tuple[tuple[str, str], ...]:
        if context is None:
            return ()
        metadata = tuple((key, str(value)) for key, value in context.as_dict().items())
        if timestamp is not None:
            metadata += (("Frame time", datetime.fromtimestamp(timestamp).isoformat(timespec="milliseconds")),)
        return metadata

    @staticmethod
    def curve_filename_part(value: str) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(". ")[:48]
        if not name:
            name = "curve"
        if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)), *("LPT" + str(i) for i in range(1, 10))}:
            name = "_" + name
        return name

    def displayed_export_curves(self) -> tuple[PlotSeries, ...]:
        curves = tuple(item for item in self.plot.series if item.visible)
        if self.current_workspace() == "采集" and curves:
            curves = (next((item for item in curves if item.active), curves[0]),)
        return tuple(item for item in curves if any(math.isfinite(y) for y in item.values))

    def refresh_curve_save_state(self) -> None:
        if not hasattr(self, "curve_save_button"):
            return
        saved = self.curve_export_verified.get(self.current_workspace())
        curves = self.displayed_export_curves()
        verified = bool(saved and saved[:2] == (self.plot.x_label, self.plot.y_label) and
                        len(saved[2]) == len(curves) and curves and all(
                            before.name == after.name and before.metadata == after.metadata and
                            before.wavelengths == after.wavelengths and
                            self.plot.series_values_equal(before.values, after.values)
                            for before, after in zip(saved[2], curves)))
        self.curve_save_button.configure(
            style="Saved.PlotTool.TButton" if verified else "PlotTool.TButton",
            image=self.curve_save_verified_icon if verified else self.curve_save_icon,
        )

    def curve_source_name(self, curve: PlotSeries) -> str:
        source = dict(curve.metadata).get("Source file", "")
        return Path(source).stem if source else curve.name.split("｜", 1)[-1].strip() or self.compose_save_base_name()

    def curve_export_default_name(self, curve: PlotSeries, x_label: str, y_label: str) -> str:
        group = dict(curve.metadata).get("Group", "")
        mode = {"Tauc (A·eV)²": "Tauc_Direct_2", "Tauc (A·eV)^(1/2)": "Tauc_Indirect_1-2",
                "归一化强度": "归一化", "强度 (counts)": "强度",
                "吸光度 (A)": "吸光度", "透过率 (%)": "透过率"}.get(y_label, y_label)
        axis = {"光子能量 (eV)": "eV", "波长 (nm)": "nm", "像素索引": "pixel"}.get(x_label, x_label)
        parts = [self.curve_source_name(curve)] + ([group] if group else []) + [mode, axis]
        return "_".join(self.curve_filename_part(part) for part in parts) + ".txt"

    def save_plot_curves(self) -> None:
        from tkinter import filedialog, messagebox

        # Freeze displayed transformed arrays BEFORE a native dialog pumps Tk events.
        workspace = self.current_workspace()
        x_label, y_label = self.plot.x_label, self.plot.y_label
        curves = self.displayed_export_curves()
        if not curves:
            messagebox.showwarning("没有数据", "当前图没有可保存的有效曲线。", parent=self.root)
            return
        verified_snapshot = (x_label, y_label, curves)
        stamp = datetime.now()
        common = (("OceanLab version", APP_VERSION), ("Workspace", workspace),
                  ("Export time", stamp.isoformat(timespec="milliseconds")))
        if x_label == "光子能量 (eV)" or "Tauc" in y_label:
            common += (("Energy conversion", f"E_eV = {PHOTON_ENERGY_EV_NM} / wavelength_nm"),)
        if workspace in ("采集", "处理"):
            common += self.curve_context_metadata(self.frame_context, self.last_timestamp)
            if workspace == "处理":
                common += (("Display mode", self.mode_var.get()),
                           ("Reference time", datetime.fromtimestamp(self.reference_timestamp).isoformat(timespec="milliseconds") if self.reference_timestamp else "None"),
                           ("Formula", {
                               "峰值归一化": "sample / max(sample)",
                               "透过率 (%)": "sample / reference * 100",
                               "吸光度 (A)": "-log10(sample / reference)",
                               "Tauc 计算结果": "(-log10(sample / reference) * E_eV)^2",
                               TAUC_DIRECT: "(-log10(sample / reference) * E_eV)^2",
                               TAUC_INDIRECT: "(-log10(sample / reference) * E_eV)^(1/2)",
                           }.get(self.mode_var.get(), "sample")))
        # Give unnamed real-time data a useful name without changing the screen.
        curves = tuple(replace(item, name=item.name or self.compose_save_base_name(),
                               wavelengths=tuple(item.wavelengths), values=tuple(item.values)) for item in curves)
        source = dict(curves[0].metadata).get("Source file", "")
        folder = next((Path(value) for value in (self.curve_export_directory, str(Path(source).parent) if source else "",
                                               self.save_directory_var.get(), str(Path.home() / "Desktop"), str(Path.home()))
                       if value and Path(value).is_dir()), Path.home())
        if len(curves) == 1:
            selected = filedialog.asksaveasfilename(
                parent=self.root, title="另存为", initialdir=str(folder),
                initialfile=self.curve_export_default_name(curves[0], x_label, y_label),
                defaultextension=".txt", filetypes=[("Text (*.txt)", "*.txt")], confirmoverwrite=True,
            )
        else:
            selected = filedialog.askdirectory(parent=self.root, title=f"保存 {len(curves)} 条曲线 TXT：选择文件夹", initialdir=str(folder), mustexist=True)
        if not selected:
            return
        chosen = Path(selected)
        if len(curves) == 1 and chosen.suffix.lower() != ".txt":
            chosen = chosen.with_name(chosen.name + ".txt")
            if len(curves) == 1 and chosen.exists() and not messagebox.askyesno(
                "覆盖文件", f"文件已存在，是否覆盖？\n{chosen}", parent=self.root,
            ):
                return
        saved = []
        try:
            for curve in curves:
                path = chosen if len(curves) == 1 else chosen / self.curve_export_default_name(curve, x_label, y_label)
                if len(curves) > 1:
                    original, duplicate = path, 2
                    while path.exists():
                        path = original.with_name(f"{original.stem}_{duplicate}.txt")
                        duplicate += 1
                result = self.spectrum_exporter.export_curve(CurveExportRequest(path, curve, x_label, y_label, common))
                saved.append(result.path)
            self.curve_export_directory = str((chosen.parent if len(curves) == 1 else chosen).resolve())
            self.curve_export_verified[workspace] = verified_snapshot
            self.refresh_curve_save_state()
            self.save_preferences()
            self.status_var.set(f"已保存并验证 {len(saved)} 条曲线 TXT：{saved[0] if len(saved) == 1 else chosen}")
        except Exception as exc:
            self.curve_export_verified.pop(workspace, None)
            self.refresh_curve_save_state()
            logger.exception("Current curve TXT export failed")
            self.status_var.set(f"曲线保存失败；已完成 {len(saved)}/{len(curves)} 个文件")
            messagebox.showerror("保存失败", f"{exc}\n\n已完成 {len(saved)}/{len(curves)} 个文件。", parent=self.root)

    def save_csv(self) -> None:
        from tkinter import filedialog, messagebox

        if self.current_workspace() != "采集":
            messagebox.showinfo("仅采集页保存", "保存设置与文件保存只在采集页提供。")
            return
        if not self.raw_spectrum:
            messagebox.showwarning("没有数据", "当前没有可保存的光谱。")
            return
        processed, label = self.processed_spectrum()
        if not processed:
            messagebox.showwarning("无法保存", "当前显示方式缺少必要数据，或没有可保存的有限光谱。")
            return

        base_name = self.compose_save_base_name()
        filename = self.build_save_filename(base_name=base_name)
        directory = self.save_directory_var.get().strip()
        path: Path | None = None
        if directory:
            target_directory = Path(directory)
            if not target_directory.is_dir():
                messagebox.showerror("保存位置不可用", "保存设置中的目标文件夹不存在，请重新选择。")
                return
            path = target_directory / filename
            if self.save_suffix_var.get() == SAVE_SUFFIX_SEQUENCE:
                while path.exists():
                    self.save_sequence += 1
                    path = target_directory / self.build_save_filename(base_name=base_name)
            elif path.exists():
                stem, suffix = path.stem, path.suffix
                duplicate = 2
                while path.exists():
                    path = target_directory / f"{stem}_{duplicate}{suffix}"
                    duplicate += 1
        else:
            extension = self.save_extension(self.save_format_var.get())
            filetypes = (
                [("TXT 文件", "*.txt"), ("所有文件", "*.*")]
                if extension == ".txt"
                else [("CSV 文件", "*.csv"), ("所有文件", "*.*")]
            )
            selected = filedialog.asksaveasfilename(
                title="保存光谱",
                defaultextension=extension,
                initialfile=filename,
                filetypes=filetypes,
            )
            if not selected:
                return
            path = Path(selected)

        path = path.resolve()
        context = self.frame_context
        if context is None:
            messagebox.showerror("保存失败", "当前光谱缺少采集上下文，无法可靠导出。")
            return
        try:
            frame = SpectrumFrame(tuple(self.raw_spectrum), self.last_timestamp or time.time(), context)
            reference_frame = (
                SpectrumFrame(tuple(self.reference_spectrum), self.reference_timestamp or frame.timestamp, self.reference_context)
                if self.reference_spectrum is not None and self.reference_context is not None
                else None
            )
            metadata = {
                "base_name": base_name,
                "model": self.device_info.model if self.device_info else "",
                "serial": self.device_info.serial if self.device_info else context.serial,
                "source": context.source,
                "integration_ms": f"{context.integration_us / 1000:g}",
                "integration_display_unit": self.integration_unit_var.get(),
                "averages": str(context.averages),
                "refresh_interval_ms": str(self.applied_settings["interval_ms"]),
                "boxcar_half_width": str(context.boxcar),
                "trigger_mode": str(TRIGGER_VALUE_TO_LABEL.get(context.trigger_mode, context.trigger_mode)),
                "electric_dark_correction": str(context.electric_dark),
                "nonlinearity_correction": str(context.nonlinearity),
                "light_source": str(context.light_source),
                "frame_context_json": json.dumps(context.as_dict(), ensure_ascii=False),
                "dark_context_json": "",
                "dark_timestamp": "",
                "reference_context_json": json.dumps(self.reference_context.as_dict(), ensure_ascii=False) if self.reference_context else "",
                "reference_timestamp": datetime.fromtimestamp(self.reference_timestamp).isoformat(timespec="milliseconds") if self.reference_timestamp else "",
                "x_axis_display": "波长 (nm)" if self.current_workspace() == "AB吸收" else self.x_axis_var.get(),
            }
            result = self.spectrum_exporter.export(ExportRequest(
                path=path,
                frame=frame,
                wavelengths=self.wavelengths,
                processed=processed,
                processed_label=label,
                baselines=BaselineSet(None, reference_frame),
                metadata=metadata,
            ))
            verified_size = result.size
            if self.save_suffix_var.get() == SAVE_SUFFIX_SEQUENCE:
                self.save_sequence += 1
            self.save_preferences()
            self.mark_save_verified()
            if self.commit_saved_single_capture(base_name):
                self.refresh_plot()
            self.status_var.set(f"已保存并验证：{path}（{verified_size} 字节）")
        except Exception as exc:
            self.mark_save_pending()
            self.status_var.set(f"保存验证失败：{path}")
            messagebox.showerror("保存失败", f"文件未通过保存验证，不会显示为保存成功。\n\n{exc}\n\n目标位置：{path}")

    def handle_sdk_ready(self, version_numbers: tuple[int, ...]) -> None:
        version = ".".join(map(str, version_numbers))
        self.sdk_available = True
        self.sdk_footer_var.set(f"SDK: {self.sdk_home}")
        self.set_sdk_selector_visible(False)
        self.save_preferences()
        if not self.simulation_mode and not self.auto_connect_attempted:
            self.auto_connect_attempted = True
            self.status_var.set(f"OceanDirect SDK {version} 已就绪；正在自动检测仪器…")
            self.root.after(100, self.toggle_connection)
        elif not self.simulation_mode:
            self.status_var.set(f"OceanDirect SDK {version} 已就绪；请选择连接仪器或模拟模式")
        self.update_connection_buttons()

    def poll_events(self) -> None:
        from tkinter import messagebox

        processed_events = 0
        deadline = time.monotonic() + 0.008
        try:
            while processed_events < 128 and time.monotonic() < deadline:
                event, payload = self.events.get_nowait()
                processed_events += 1
                if event == "sdk_ready":
                    self.handle_sdk_ready(tuple(payload["version"]))
                elif event == "connected":
                    if self.simulation_mode:
                        self.exit_simulation_mode(switching_to_device=True)
                    self.clear_single_capture_history()
                    self.connection_pending = False
                    self.connected = True
                    self.device_info = payload["info"]
                    self.wavelengths = payload["wavelengths"]
                    settings = payload["settings"]
                    self.applied_settings = {
                        "integration_us": int(settings["integration_us"]),
                        "averages": max(1, int(settings["averages"])),
                        "boxcar": max(0, int(settings["boxcar"])),
                        "interval_ms": max(1, int(settings.get("interval_ms", 80))),
                    }
                    self.sync_device_option_values(settings)
                    info = self.device_info
                    self.device_var.set(f"{info.model} · {info.serial}")
                    self.device_status_label.configure(text="已连接", style="DeviceOn.TLabel")
                    self.set_controls(True)
                    self.update_connection_buttons()
                    self.reset_acquisition_indicator()
                    self.status_var.set(f"已连接 {info.model}，正在应用当前预设")
                    self.reset_plot_view()
                    self.apply_range()
                    requested = self.prepare_settings_for_acquisition()
                    if requested is not None:
                        self.send_configuration("configure", requested)
                elif event == "disconnected":
                    if self.single_in_progress:
                        self.cancel_single_acquisition()
                    self.connected = False
                    self.acquisition_pending = False
                    self.active_acquisition_request_id = None
                    self.connection_pending = False
                    self.acquiring = False
                    self.device_info = None
                    self.wavelengths = []
                    self.raw_spectrum = []
                    self.frame_context = None
                    self.reference_spectrum = None
                    self.reference_context = None
                    self.reference_timestamp = None
                    self.spectrum_service = SpectrumService()
                    self.clear_single_capture_history()
                    self.device_var.set("")
                    self.device_status_label.configure(text="未连接", style="DeviceOff.TLabel")
                    self.set_controls(False)
                    self.reset_device_capabilities()
                    self.update_connection_buttons()
                    self.reset_acquisition_indicator()
                    self.settings_state_var.set("连接仪器或进入模拟模式后可应用参数")
                    self.update_baseline_state()
                    self.refresh_plot()
                    self.status_var.set("正在安全关闭 OceanDirect…" if self.closing else "仪器已断开")
                elif event == "acquisition_state":
                    if payload.get("request_id") != self.active_acquisition_request_id:
                        continue
                    if payload["running"]:
                        if self.cancel_requested:
                            continue
                        self.acquisition_pending = False
                    elif self.cancel_requested:
                        was_single = self.single_in_progress
                        self.cancel_requested = False
                        self.acquiring = False
                        self.cancel_single_acquisition()
                        self.set_acquisition_state(False, announce=False)
                        if was_single:
                            self.run_state_var.set("单次采集已取消")
                        self.status_var.set("单次采集已取消，本次数据未保留；仪器保持连接" if was_single
                                            else "采集已暂停；仪器保持连接")
                        self.update_connection_buttons()
                        continue
                    if not (self.single_in_progress and not payload["running"]):
                        self.set_acquisition_state(payload["running"])
                elif event == "spectrum":
                    self.accept_spectrum(payload["spectrum"], payload["timestamp"], payload.get("context"), payload.get("request_id"))
                elif event == "configuration_applied":
                    if payload.get("request_id") != self.active_acquisition_request_id:
                        continue
                    settings = payload["settings"]
                    context = payload["context"]
                    self.sync_setting_values(settings, "采集配置已由仪器确认")
                    self._syncing_settings = True
                    try:
                        self.trigger_mode_var.set(TRIGGER_VALUE_TO_LABEL.get(context.trigger_mode, TRIGGER_MODE_OPTIONS[0]))
                        self.electric_dark_var.set(context.electric_dark)
                        self.nonlinearity_var.set(context.nonlinearity)
                        self.light_source_var.set(context.light_source)
                    finally:
                        self._syncing_settings = False
                    if self.cancel_requested:
                        self.update_acquisition_buttons()
                        continue
                    if payload["action"] != "start":
                        self.acquisition_pending = False
                    if payload["action"] == "configure":
                        self.active_acquisition_request_id = None
                        self.status_var.set("仪器配置已确认，可以开始采集")
                    else:
                        self.status_var.set("仪器配置已确认，开始连续采集…" if payload["action"] == "start" else "仪器配置已确认，正在采集单帧…")
                    self.update_acquisition_buttons()
                elif event == "settings_applied":
                    settings = payload["settings"]
                    self.sync_setting_values(settings)
                    self.status_var.set("采集参数已应用")
                elif event == "device_options_applied":
                    settings = payload["settings"]
                    self._syncing_settings = True
                    try:
                        if "trigger_mode" in settings:
                            mode = int(settings["trigger_mode"])
                            self.trigger_mode_var.set(TRIGGER_VALUE_TO_LABEL.get(mode, TRIGGER_MODE_OPTIONS[0]))
                        if "light_source" in settings:
                            self.light_source_var.set(bool(settings["light_source"]))
                    finally:
                        self._syncing_settings = False
                    self.status_var.set("高级设备选项已应用")
                elif event == "status":
                    self.status_var.set(payload["message"])
                elif event == "warning":
                    messagebox.showwarning("OceanDirect", payload["message"])
                elif event == "error":
                    logger.error("Device error: request=%s message=%s", payload.get("request_id"), payload["message"])
                    self.acquisition_pending = False
                    self.cancel_requested = False
                    self.active_acquisition_request_id = None
                    self.connection_pending = False
                    self.status_var.set(payload["message"])
                    if self.single_in_progress:
                        self.finish_single_acquisition(success=False)
                    elif "采集失败" in payload["message"]:
                        self.reset_acquisition_indicator(
                            state_text="采集失败",
                            lamp_color="#d13438",
                            label_style="RunError.TLabel",
                        )
                    if not self.connected and not self.closing:
                        self.device_status_label.configure(text="未连接", style="DeviceOff.TLabel")
                    self.update_connection_buttons()
                    if not payload.get("cancelled") and not self.closing:
                        messagebox.showerror("OceanDirect 错误", payload["message"])
                elif event == "fatal":
                    self.sdk_available = False
                    self.connection_pending = False
                    if self.sdk_validator(self.sdk_home):
                        self.sdk_footer_var.set(f"SDK 加载失败: {self.sdk_home}")
                    else:
                        self.sdk_footer_var.set("SDK: 未检测到 OceanDirect")
                    self.set_sdk_selector_visible(True)
                    if self.simulation_mode:
                        self.status_var.set("模拟模式可继续使用；真实仪器连接不可用")
                    else:
                        self.device_status_label.configure(text="SDK 不可用", style="DeviceOff.TLabel")
                        self.status_var.set(payload["message"])
                    self.update_connection_buttons()
                    messagebox.showwarning(
                        "OceanDirect SDK",
                        f"{payload['message']}\n\n模拟模式仍可正常使用，也可以点击右下角“选择 OceanDirect…”重新指定目录。",
                    )
                elif event == "shutdown_complete":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        except Exception as exc:
            logger.exception("Acquisition event processing failed")
            if self.single_in_progress:
                self.finish_single_acquisition(success=False)
            self.acquisition_pending = False
            self.status_var.set(f"数据处理失败：{exc}；日志：{LOG_FILE}")
            self.update_acquisition_buttons()
            if not self.closing:
                messagebox.showerror("OceanLab 数据处理错误", f"{exc}\n\n日志：{LOG_FILE}")
        finally:
            # Rendering or event-handler errors must never stop receiving
            # device state and spectrum events for the rest of the session.
            try:
                if self.root.winfo_exists():
                    self.root.after(1 if not self.events.empty() else 50, self.poll_events)
            except self.tk.TclError:
                pass

    def on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.save_preferences()
        self.cancel_simulation_timer()
        self.cancel_single_timers()
        self.single_in_progress = False
        self.status_var.set("正在停止采集并安全关闭仪器…")
        self.update_connection_buttons()
        self.set_controls(False)
        if self.worker.is_alive():
            self.worker.send("stop")
            self.worker.send("shutdown")
            self.root.after(1800, self.force_close)
        else:
            self.root.destroy()

    def force_close(self) -> None:
        if self.root.winfo_exists():
            self.root.destroy()
