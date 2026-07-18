from __future__ import annotations

import tempfile
import time
import tkinter as tk
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from oceanlab.adapters.filesystem import DelimitedSpectrumExporter, JsonPreferencesRepository
from oceanlab.adapters.spectrum_files import DelimitedSpectrumImporter
from oceanlab.adapters.oceandirect import is_oceandirect_home, normalize_sdk_home
from oceanlab.adapters.simulation import SimulatedSpectrometer
from oceanlab.bootstrap import service_factory
from oceanlab.config import SAVE_FORMAT_TXT, SAVE_SUFFIX_SEQUENCE, SAVE_SUFFIX_TIME
from oceanlab.domain.results import PlotSeries
from oceanlab.ui.app import OceanLabApp


class IntegrationPresetSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.preferences_path = Path(self.temp_dir.name) / "settings.json"
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = OceanLabApp(
            self.root,
            Path(self.temp_dir.name) / "missing-sdk",
            service_factory=service_factory,
            sdk_validator=is_oceandirect_home,
            sdk_normalizer=normalize_sdk_home,
            preferences_repository=JsonPreferencesRepository(self.preferences_path),
            spectrum_exporter=DelimitedSpectrumExporter(),
            spectrum_importer=DelimitedSpectrumImporter(),
            simulation_factory=SimulatedSpectrometer,
        )
        self.root.deiconify()
        self.root.update_idletasks()

    def tearDown(self) -> None:
        self.app.closing = True
        if self.app.worker.is_alive():
            self.app.worker.send("shutdown")
            self.app.worker.join(timeout=1.0)
        self.root.destroy()
        self.temp_dir.cleanup()

    def switch_to(self, name: str) -> None:
        self.app.integration_preset_var.set(name)
        self.app.on_integration_preset_changed()

    def select_workspace(self, name: str) -> None:
        self.app.sidebar_notebook.select(self.app.workspace_tabs[name])
        self.app.on_workspace_changed()
        self.root.update_idletasks()

    def test_layout_switch_persistence_and_state(self) -> None:
        self.assertEqual(self.app.integration_spin.cget("width"), 6)
        self.assertEqual(self.app.averages_spin.cget("width"), 6)
        self.assertEqual(self.app.integration_spin.winfo_width(), self.app.averages_spin.winfo_width())
        self.assertEqual(self.app.integration_spin.winfo_rootx(), self.app.averages_spin.winfo_rootx())
        self.assertEqual(self.app.integration_spin.master.master.pack_info()["padx"], (64, 0))
        self.assertGreaterEqual(int(self.app.integration_unit_combo.master.cget("width")), 70)
        self.assertGreaterEqual(self.app.integration_unit_combo.winfo_width(), 70)
        self.assertEqual(len(self.app.integration_preset_buttons), 3)
        self.assertTrue(all(str(button.cget("state")) == "normal" for button in self.app.integration_preset_buttons))
        self.assertTrue(all(int(button.master.cget("width")) == 42 for button in self.app.integration_preset_buttons))
        self.assertTrue(all(int(button.master.cget("height")) == 34 for button in self.app.integration_preset_buttons))
        self.assertEqual(
            self.app.acquisition_preset_header.winfo_rooty(),
            self.app.common_parameter_header.winfo_rooty(),
        )
        self.assertLess(
            self.app.acquisition_preset_header.winfo_rooty(),
            self.app.integration_spin.master.master.winfo_rooty(),
        )

        self.app.integration_var.set("100")
        self.app.integration_unit_var.set("ms")
        self.app.averages_var.set("3")
        self.app.base_name_var.set("preset-one")
        self.app.name_prefix_var.set("sample")
        self.app.name_suffix_var.set("{integration}-{averages}-{number}")
        self.app.position_var.set("02")
        self.app.save_format_var.set(SAVE_FORMAT_TXT)
        self.app.save_directory_var.set(self.temp_dir.name)
        self.app.save_suffix_var.set(SAVE_SUFFIX_SEQUENCE)
        self.app.sequence_digits_var.set("4")
        self.app.confirm_integration_preset_edit()
        self.switch_to("预设2")
        self.app.integration_var.set("2")
        self.app.integration_unit_var.set("s")
        self.app._integration_unit_last = "s"
        self.app.averages_var.set("7")
        self.app.base_name_var.set("preset-two")
        self.app.name_prefix_var.set("treated")
        self.app.name_suffix_var.set("{averages}")
        self.app.position_var.set("")
        self.app.save_format_var.set(SAVE_FORMAT_TXT)
        self.app.save_directory_var.set("")
        self.app.save_suffix_var.set(SAVE_SUFFIX_TIME)
        self.app.sequence_digits_var.set("8")
        self.app.confirm_integration_preset_edit()

        self.switch_to("预设1")
        self.assertEqual(self.app.integration_var.get(), "100")
        self.assertEqual(self.app.integration_unit_var.get(), "ms")
        self.assertEqual(self.app.averages_var.get(), "3")
        self.assertEqual(self.app.base_name_var.get(), "preset-one")
        self.assertEqual(self.app.name_prefix_var.get(), "sample")
        self.assertEqual(self.app.name_suffix_var.get(), "{integration}-{averages}-{number}")
        self.assertEqual(self.app.position_var.get(), "02")
        self.assertEqual(self.app.save_format_var.get(), SAVE_FORMAT_TXT)
        self.assertEqual(self.app.save_directory_var.get(), self.temp_dir.name)
        self.assertEqual(self.app.save_suffix_var.get(), SAVE_SUFFIX_SEQUENCE)
        self.assertEqual(self.app.sequence_digits_var.get(), "4")
        self.switch_to("预设2")
        self.assertEqual(self.app.integration_var.get(), "2")
        self.assertEqual(self.app.integration_unit_var.get(), "s")
        self.assertEqual(self.app.averages_var.get(), "7")
        self.assertEqual(self.app.base_name_var.get(), "preset-two")
        self.assertEqual(self.app.name_prefix_var.get(), "treated")
        self.assertEqual(self.app.position_var.get(), "")
        self.assertEqual(self.app.save_suffix_var.get(), SAVE_SUFFIX_TIME)
        self.assertEqual(self.app.sequence_digits_var.get(), "8")

        self.app.acquiring = True
        self.app.update_acquisition_buttons()
        self.assertTrue(all(str(button.cget("state")) == "disabled" for button in self.app.integration_preset_buttons))
        self.app.acquiring = False
        self.app.single_in_progress = True
        self.app.update_acquisition_buttons()
        self.assertTrue(all(str(button.cget("state")) == "disabled" for button in self.app.integration_preset_buttons))
        self.app.single_in_progress = False
        self.app.update_acquisition_buttons()
        self.assertTrue(all(str(button.cget("state")) == "normal" for button in self.app.integration_preset_buttons))

        self.app.save_preferences()
        saved = self.app.load_preferences()
        self.assertEqual(saved["selected_integration_preset"], 1)
        self.assertEqual(saved["acquisition_presets"][0]["integration_value"], "100")
        self.assertEqual(saved["acquisition_presets"][0]["averages"], "3")
        self.assertEqual(saved["acquisition_presets"][0]["base_name"], "preset-one")
        self.assertEqual(saved["acquisition_presets"][0]["name_prefix"], "sample")
        self.assertEqual(saved["acquisition_presets"][0]["position"], "02")
        self.assertEqual(saved["acquisition_presets"][0]["sequence_digits"], "4")
        self.assertEqual(saved["acquisition_presets"][1]["integration_value"], "2")
        self.assertEqual(saved["acquisition_presets"][1]["integration_unit"], "s")
        self.assertEqual(saved["acquisition_presets"][1]["averages"], "7")

    def test_switch_applies_immediately_in_idle_simulation(self) -> None:
        self.app.acquisition_presets[2] = {
            **self.app.acquisition_presets[2],
            "integration_value": "500",
            "integration_unit": "ms",
            "averages": "9",
        }
        self.app.simulation_mode = True
        self.app.connected = True
        self.app.controls_enabled = True
        self.switch_to("预设3")
        self.assertEqual(self.app.simulation_settings["integration_us"], 500_000)
        self.assertEqual(self.app.simulation_settings["averages"], 9)

    def test_shared_variable_name_can_be_enabled_without_losing_private_names(self) -> None:
        self.app.base_name_var.set("PresetOne")
        self.app.confirm_integration_preset_edit()
        self.switch_to("预设2")
        self.app.base_name_var.set("PresetTwo")
        self.app.confirm_integration_preset_edit()

        self.app.share_variable_name_var.set(True)
        self.app.on_share_variable_name_changed()
        self.assertEqual(self.app.shared_variable_box.itemcget(self.app.shared_variable_box_check, "state"), "normal")
        self.switch_to("预设1")
        self.assertEqual(self.app.base_name_var.get(), "PresetTwo")
        self.app.base_name_var.set("SharedName")
        self.switch_to("预设2")
        self.assertEqual(self.app.base_name_var.get(), "SharedName")

        self.app.share_variable_name_var.set(False)
        self.app.on_share_variable_name_changed()
        self.assertEqual(self.app.shared_variable_box.itemcget(self.app.shared_variable_box_check, "state"), "hidden")
        self.assertEqual(self.app.base_name_var.get(), "PresetTwo")
        self.switch_to("预设1")
        self.assertEqual(self.app.base_name_var.get(), "PresetOne")
        saved = self.app.load_preferences()
        self.assertFalse(saved["share_variable_name"])
        self.assertEqual(saved["shared_base_name"], "SharedName")

    def test_acquisition_option_checkboxes_match_large_shared_control(self) -> None:
        controls = (
            self.app.edc_check,
            self.app.nl_check,
            self.app.decouple_check,
            self.app.light_source_check,
        )
        self.assertTrue(all(control.box.winfo_reqwidth() >= 32 for control in controls))
        self.assertTrue(all(control.box.winfo_reqheight() >= 32 for control in controls))
        self.assertTrue(all(control.box.winfo_reqwidth() == self.app.shared_variable_box.winfo_reqwidth() for control in controls))
        self.assertEqual(self.app.edc_check.cget("text"), "启用暗噪声校正")
        self.assertEqual(self.app.decouple_check.cget("state"), "disabled")
        self.assertEqual(self.app.pl_normalize_check.cget("text"), "峰值归一化")
        self.assertEqual(self.app.pl_turbo_check.cget("text"), "Turbo 渐变色")
        self.assertEqual(
            self.app.pl_normalize_check.box.winfo_reqwidth(),
            self.app.shared_variable_box.winfo_reqwidth(),
        )
        self.assertEqual(
            self.app.pl_turbo_check.box.winfo_reqheight(),
            self.app.shared_variable_box.winfo_reqheight(),
        )

        self.app.edc_check.configure(state="normal")
        before = bool(self.app.electric_dark_var.get())
        self.app.edc_check.invoke()
        self.assertEqual(bool(self.app.electric_dark_var.get()), not before)
        expected_check_state = "normal" if not before else "hidden"
        self.assertEqual(
            self.app.edc_check.box.itemcget(self.app.edc_check.box_check, "state"),
            expected_check_state,
        )

        self.app.edc_check.configure(state="disabled")
        disabled_value = bool(self.app.electric_dark_var.get())
        self.app.edc_check.invoke()
        self.assertEqual(bool(self.app.electric_dark_var.get()), disabled_value)
        self.assertEqual(self.app.edc_check.text_label.cget("foreground"), "#94a3b8")

    def test_saved_single_capture_history_is_per_preset_and_continuous_is_live_only(self) -> None:
        self.app.enter_simulation_mode()
        self.app.save_directory_var.set(self.temp_dir.name)
        self.app.save_suffix_var.set(SAVE_SUFFIX_SEQUENCE)
        self.app.sequence_digits_var.set("3")
        self.app.name_prefix_var.set("")
        self.app.name_suffix_var.set("")
        self.app.position_var.set("")

        def capture_single(variable: str) -> int:
            self.app.base_name_var.set(variable)
            self.app.single_in_progress = True
            self.app.accept_spectrum(self.app.generate_simulated_spectrum(), time.time())
            candidate = self.app.single_capture_candidates[self.app._active_integration_preset]
            return candidate.capture_id

        first_candidate_id = capture_single("0000GPa")
        replacement_candidate_id = capture_single("0250GPa")
        self.assertNotEqual(first_candidate_id, replacement_candidate_id)
        self.assertEqual(self.app.single_capture_history[0], [])
        self.assertEqual(len(self.app.plot.series), 1)
        self.assertTrue(self.app.plot.series[0].name.startswith("待保存｜"))

        self.app.save_csv()
        self.assertNotIn(0, self.app.single_capture_candidates)
        self.assertEqual(len(self.app.single_capture_history[0]), 1)
        self.assertEqual(self.app.single_capture_history[0][0].capture_id, replacement_candidate_id)
        self.app.save_csv()
        self.assertEqual(len(self.app.single_capture_history[0]), 1)

        for variable in ("0500GPa", "0750GPa", "1000GPa"):
            capture_single(variable)
            self.app.save_csv()
        history = self.app.single_capture_history[0]
        self.assertEqual(len(history), 3)
        self.assertEqual([item.display_name for item in history], ["1000GPa", "0750GPa", "0500GPa"])
        self.assertEqual([item.active for item in self.app.plot.series], [True, False, False])
        self.assertEqual([item.dash for item in self.app.plot.series], [None, (8, 5), (2, 5)])
        self.assertTrue(self.app.plot.series[0].name.startswith("最新已保存｜"))
        changed_context = replace(
            history[1].frame.context,
            integration_us=history[1].frame.context.integration_us * 2,
            averages=history[1].frame.context.averages + 1,
        )
        history[1] = replace(history[1], frame=replace(history[1].frame, context=changed_context))
        self.app.refresh_plot()
        self.assertIn("积分时间", self.app.single_history_warning_var.get())
        self.assertIn("扫描平均次数", self.app.single_history_warning_var.get())
        self.assertTrue(self.app.single_history_warning_label.winfo_manager())
        self.assertEqual(self.app.plot.series[1].color, "#3f6f99")

        saved_ids = [item.capture_id for item in history]
        self.app.acquiring = True
        self.app.accept_spectrum(self.app.generate_simulated_spectrum(), time.time())
        self.app.acquiring = False
        self.assertEqual([item.capture_id for item in self.app.single_capture_history[0]], saved_ids)
        self.assertEqual(len(self.app.plot.series), 1)
        self.assertEqual(self.app.plot.series[0].name, "")
        self.assertEqual(self.app.single_history_warning_var.get(), "")

        self.switch_to("预设2")
        self.assertEqual(self.app.single_capture_history[1], [])
        self.assertEqual(self.app.raw_spectrum, [])
        self.switch_to("预设1")
        self.assertEqual(len(self.app.plot.series), 3)
        self.assertTrue(self.app.plot.series[0].name.startswith("最新已保存｜1000GPa"))

    def test_integration_position_and_composed_name(self) -> None:
        self.assertEqual(self.app.name_prefix_var.get(), "")
        self.assertEqual(self.app.base_name_var.get(), "spectrum")
        self.assertEqual(self.app.name_suffix_var.get(), "{integration}-{averages}-{number}")
        self.assertEqual(self.app.position_var.get(), "")
        self.app.integration_unit_var.set("ms")
        self.app._integration_unit_last = "ms"
        self.app.integration_var.set("100")
        self.app.adjust_integration(1)
        self.assertEqual(self.app.integration_var.get(), "110")
        self.app.adjust_integration(-1)
        self.assertEqual(self.app.integration_var.get(), "100")

        self.app.position_var.set("09")
        self.app.adjust_position_number(1)
        self.assertEqual(self.app.position_var.get(), "10")
        self.app.adjust_position_number(-1)
        self.assertEqual(self.app.position_var.get(), "09")
        self.app.position_var.set("")
        self.app.adjust_position_number(1)
        self.assertEqual(self.app.position_var.get(), "01")
        self.app.adjust_position_number(-1)
        self.assertEqual(self.app.position_var.get(), "")

        self.app.name_prefix_var.set("KCaCl3Eu")
        self.app.base_name_var.set("P2.0GPa")
        self.app.name_suffix_var.set("L360nm-{integration}-{averages}-{number}")
        self.app.integration_var.set("100")
        self.app.integration_unit_var.set("ms")
        self.app.averages_var.set("3")
        self.app.position_var.set("02")
        self.assertEqual(
            self.app.compose_save_base_name(),
            "KCaCl3Eu-P2.0GPa-L360nm-100ms-3times-02",
        )
        self.app.position_var.set("")
        self.assertEqual(
            self.app.compose_save_base_name(),
            "KCaCl3Eu-P2.0GPa-L360nm-100ms-3times",
        )
        self.app.name_suffix_var.set("L360nm-{number}-100ms")
        self.assertEqual(self.app.compose_save_base_name(), "KCaCl3Eu-P2.0GPa-L360nm-100ms")

        original_device = self.app.device_info
        self.app.device_info = SimpleNamespace(serial="TESTSERIAL")
        try:
            filename = self.app.build_save_filename(
                base_name="sample",
                save_format=SAVE_FORMAT_TXT,
                suffix_mode=SAVE_SUFFIX_TIME,
                timestamp=datetime(2026, 7, 12, 21, 41, 2, 58_000),
            )
        finally:
            self.app.device_info = original_device
        self.assertEqual(filename, "sample_TESTSERIAL1_21-41-02-058.txt")

    def test_save_panel_is_fixed_below_scrollable_acquisition_area(self) -> None:
        self.root.update_idletasks()
        self.assertEqual(int(self.app.sidebar_notebook.master.cget("width")), 570)
        self.assertEqual(int(self.app.cursor_shell.cget("width")), 190)
        self.assertEqual(int(self.app.cursor_shell.cget("height")), 48)
        self.assertGreaterEqual(self.app.stop_button.winfo_width(), 92)
        self.assertEqual(int(self.app.integration_spin.master.cget("height")), 48)
        self.assertEqual(int(self.app.averages_spin.master.cget("height")), 48)
        self.assertEqual(int(self.app.integration_unit_combo.master.cget("height")), 48)
        self.assertGreaterEqual(int(self.app.status_lamp.cget("width")), 30)
        self.assertGreaterEqual(int(self.app.status_lamp.cget("height")), 26)
        self.assertEqual(len(self.app.plot_toolbar_images), 8)
        self.assertTrue(all(image.width() >= 38 and image.height() >= 38 for image in self.app.plot_toolbar_images))
        self.assertEqual(len(self.app.plot_toolbar.winfo_children()), 8)
        self.assertEqual(int(self.app.peak_metric_label.cget("width")), 8)
        self.assertEqual(int(self.app.position_metric_label.cget("width")), 10)
        self.assertEqual(int(self.app.frame_metric_label.cget("width")), 9)
        self.assertEqual(self.app.peak_metric_group.cget("background"), "#eef6ff")
        self.assertEqual(self.app.position_metric_group.cget("background"), "#f5f3ff")
        self.assertEqual(self.app.frame_metric_group.cget("background"), "#f0fdf4")
        self.assertEqual(self.app.peak_metric_label.cget("anchor"), "w")
        self.assertEqual(self.app.position_metric_label.cget("anchor"), "w")
        self.assertEqual(self.app.frame_metric_label.cget("anchor"), "w")
        self.assertEqual(int(self.app.saturation_label.cget("width")), 12)
        self.assertEqual(int(self.app.cursor_value_label.cget("width")), 15)
        self.assertEqual(int(self.app.base_name_shell.cget("height")), 54)
        self.assertGreaterEqual(int(self.app.base_name_shell.grid_columnconfigure(2)["minsize"]), 82)
        self.assertGreaterEqual(self.app.shared_variable_box.winfo_reqwidth(), 32)
        self.assertGreaterEqual(self.app.shared_variable_box.winfo_reqheight(), 32)
        panel_y = self.app.sidebar_save_panel.winfo_rooty()
        panel_height = self.app.sidebar_save_panel.winfo_height()
        notebook_bottom = self.app.sidebar_notebook.winfo_rooty() + self.app.sidebar_notebook.winfo_height()
        self.assertGreaterEqual(panel_y + 2, notebook_bottom)
        self.assertIs(self.app.save_controls, self.app.sidebar_save_panel)
        save_button_row = self.app.save_button.master
        self.assertEqual(int(save_button_row.grid_columnconfigure(0)["weight"]), 0)
        self.assertEqual(int(save_button_row.grid_columnconfigure(1)["weight"]), 1)

        self.app.toggle_advanced_options()
        self.root.update_idletasks()
        self.assertEqual(self.app.sidebar_save_panel.winfo_rooty(), panel_y)
        self.assertEqual(self.app.sidebar_save_panel.winfo_height(), panel_height)
        expanded_notebook_bottom = self.app.sidebar_notebook.winfo_rooty() + self.app.sidebar_notebook.winfo_height()
        self.assertGreaterEqual(self.app.sidebar_save_panel.winfo_rooty() + 2, expanded_notebook_bottom)

    def test_save_preview_wraps_full_source_style_filename(self) -> None:
        self.app.enter_simulation_mode()
        long_base = "KCaCl3Eu-d0000GPa-L360nm-1s-very-long-sample-name"
        self.app.name_prefix_var.set("KeepPrefix")
        self.app.base_name_var.set(long_base)
        self.app.position_var.set("07")
        self.app.save_format_var.set(SAVE_FORMAT_TXT)
        self.app.save_suffix_var.set(SAVE_SUFFIX_TIME)
        self.app.open_save_settings()
        self.root.update_idletasks()
        try:
            self.assertEqual(self.app.save_preset_hint_label.cget("text"), "（预设1）")
            variable_name = self.app.save_preview_label.cget("textvariable")
            preview = self.root.getvar(variable_name)
            self.assertIn(long_base, preview)
            self.assertIn("SIMULATION1", preview)
            self.assertEqual(int(self.app.save_preview_label.cget("wraplength")), 760)
            self.assertGreaterEqual(self.app.save_dialog.winfo_width(), 800)
            self.assertGreaterEqual(self.app.save_dialog.winfo_height(), 650)
            dialog_bottom = self.app.save_dialog.winfo_rooty() + self.app.save_dialog.winfo_height()
            apply_bottom = (
                self.app.save_settings_apply_button.winfo_rooty()
                + self.app.save_settings_apply_button.winfo_height()
            )
            self.assertLess(apply_bottom, dialog_bottom)
            self.app.save_name_default_button.invoke()
            self.root.update_idletasks()
            self.assertEqual(self.app.save_name_prefix_entry.get(), "KeepPrefix")
            self.assertEqual(self.app.save_current_variable_entry.get(), long_base)
            self.assertEqual(
                self.app.save_name_suffix_entry.get(),
                "{integration}-{averages}-{number}",
            )
            reset_preview = self.root.getvar(variable_name)
            self.assertIn(f"KeepPrefix-{long_base}-", reset_preview)
            self.assertIn("-1times-07_", reset_preview)
        finally:
            self.app.save_dialog.destroy()
            self.app.save_dialog = None

    def test_save_sequence_stepper_matches_acquisition_controls(self) -> None:
        self.app.save_suffix_var.set(SAVE_SUFFIX_SEQUENCE)
        self.app.open_save_settings()
        self.root.update_idletasks()
        try:
            self.assertEqual(
                int(self.app.save_sequence_digits_shell.cget("height")),
                int(self.app.integration_spin.master.cget("height")),
            )
            self.assertEqual(self.app.save_sequence_digits_up_button.cget("text"), "▲")
            self.assertEqual(self.app.save_sequence_digits_down_button.cget("text"), "▼")
            self.assertEqual(str(self.app.save_sequence_digits_entry.cget("state")), "normal")
            self.app.save_sequence_digits_up_button.invoke()
            self.assertEqual(self.app.save_sequence_digits_entry.get(), "6")
            self.app.save_sequence_digits_down_button.invoke()
            self.assertEqual(self.app.save_sequence_digits_entry.get(), "5")
        finally:
            self.app.save_dialog.destroy()
            self.app.save_dialog = None

    def test_collapsed_acquisition_does_not_scroll_and_cursor_aligns_with_plot_edge(self) -> None:
        self.assertFalse(self.app.advanced_expanded)
        self.app.acquisition_canvas.yview_moveto(0.0)
        before = self.app.acquisition_canvas.yview()
        result = self.app.scroll_acquisition(SimpleNamespace(delta=-120))
        self.assertEqual(result, "break")
        self.assertEqual(self.app.acquisition_canvas.yview(), before)
        self.assertIs(self.app.cursor_bar.master, self.app.metrics_bar)
        self.assertEqual(self.app.cursor_bar.pack_info()["padx"], (12, 20))

        self.app.toggle_advanced_options()
        self.app.acquisition_canvas.yview_moveto(0.5)
        self.app.toggle_advanced_options()
        self.assertEqual(self.app.acquisition_canvas.yview()[0], 0.0)

    def test_canvas_configure_redraws_are_size_filtered_and_coalesced(self) -> None:
        plot = self.app.plot
        plot._drawn_canvas_size = (800, 500)
        plot._pending_canvas_size = None
        plot._resize_draw_after_id = None
        draw_count = 0

        def draw_stub() -> None:
            nonlocal draw_count
            draw_count += 1
            plot._drawn_canvas_size = (820, 500)

        original_draw = plot.draw
        plot.draw = draw_stub
        try:
            for _ in range(200):
                plot.on_canvas_configure(SimpleNamespace(width=800, height=500))
            self.assertEqual(draw_count, 0)
            self.assertIsNone(plot._resize_draw_after_id)

            plot.on_canvas_configure(SimpleNamespace(width=810, height=500))
            first_after_id = plot._resize_draw_after_id
            plot.on_canvas_configure(SimpleNamespace(width=815, height=500))
            plot.on_canvas_configure(SimpleNamespace(width=820, height=500))
            self.assertEqual(plot._resize_draw_after_id, first_after_id)
            self.assertEqual(plot._pending_canvas_size, (820, 500))
            self.app.plot.canvas.after_cancel(first_after_id)
            plot.flush_canvas_resize()
            self.assertEqual(draw_count, 1)
            self.assertIsNone(plot._pending_canvas_size)

            plot.request_interaction_draw()
            interaction_after_id = plot._interaction_draw_after_id
            for _ in range(50):
                plot.request_interaction_draw()
            self.assertEqual(plot._interaction_draw_after_id, interaction_after_id)
            plot.canvas.after_cancel(interaction_after_id)
            plot._interaction_draw_after_id = None
        finally:
            plot.draw = original_draw

    def test_multiseries_plot_keeps_single_series_compatibility(self) -> None:
        self.app.plot.set_series(
            [
                PlotSeries("spectrum-afga-aths-asth-fhsrt-afd1", (400.0, 410.0), (1.0, 2.0), "#ff0000"),
                PlotSeries("spectrum-afga-aths-asth-fhsrt-afd2", (390.0, 420.0), (3.0, 4.0), "#0000ff", active=True),
            ],
            "强度 (counts)",
        )
        self.app.plot.draw()
        self.assertEqual(self.app.plot.full_x_bounds(), (390.0, 420.0))
        self.assertEqual(self.app.plot.wavelengths, [390.0, 420.0])
        line_widths = {
            float(self.app.plot.canvas.itemcget(item_id, "width"))
            for item_id in self.app.plot.canvas.find_withtag("spectrum_line")
        }
        self.assertEqual(line_widths, {self.app.plot.LINE_WIDTH, self.app.plot.ACTIVE_LINE_WIDTH})
        self.assertEqual(self.app.plot.ACTIVE_LINE_WIDTH, self.app.plot.LINE_WIDTH * 2)
        self.assertGreaterEqual(self.app.plot._plot_rect[0], 100)
        legend_box = self.app.plot.canvas.bbox("plot_legend")
        self.assertIsNotNone(legend_box)
        self.assertLessEqual(legend_box[2], self.app.plot._plot_rect[2])
        first_legend_line = self.app.plot.canvas.find_withtag("legend_line_0")[0]
        first_legend_text = self.app.plot.canvas.find_withtag("legend_text_0")[0]
        self.assertEqual(
            self.app.plot.canvas.itemcget(first_legend_text, "text"),
            "spectrum-afga-aths-asth-fhsrt-afd1",
        )
        self.assertLess(
            self.app.plot.canvas.coords(first_legend_line)[2],
            self.app.plot.canvas.bbox(first_legend_text)[0],
        )
        first_legend_box = self.app.plot.canvas.bbox("legend_text_0")
        second_legend_box = self.app.plot.canvas.bbox("legend_text_1")
        self.assertLess(first_legend_box[3], second_legend_box[1])
        y_title_box = self.app.plot.canvas.bbox("y_axis_title")
        y_tick_box = self.app.plot.canvas.bbox("y_tick_label")
        self.assertLess(y_title_box[2] + 10, y_tick_box[0])
        x_tick_box = self.app.plot.canvas.bbox("x_tick_label")
        x_title_box = self.app.plot.canvas.bbox("x_axis_title")
        self.assertLess(x_tick_box[3], x_title_box[1])
        self.app.plot.set_data([1.0, 2.0], [5.0, 6.0], "强度 (counts)")
        self.assertEqual(len(self.app.plot.series), 1)
        self.assertEqual(self.app.plot.values, [5.0, 6.0])

    def test_metric_values_use_fixed_decimals_and_independent_fields(self) -> None:
        self.app.wavelengths = [500.0, 890.4]
        self.app.raw_spectrum = [12.3, 1245.5]
        self.app.refresh_plot()
        self.assertEqual(self.app.peak_metric_var.get(), "峰值: 1245.50")
        self.assertEqual(self.app.position_metric_var.get(), "峰位: 890.40 nm")
        self.assertEqual(self.app.frame_metric_var.get(), "帧率: 单帧")
        self.assertEqual(self.app.peak_value_var.get(), "1245.50")
        self.assertEqual(self.app.position_value_var.get(), "890.40 nm")
        self.assertEqual(self.app.frame_value_var.get(), "单帧")
        self.app.update_cursor_readout(890.4, 1245.5)
        self.assertEqual(self.app.cursor_position_var.get(), "890.40")
        self.assertEqual(self.app.cursor_var.get(), "强度: 1245.50")

    def test_baselines_are_invalidated_when_acquisition_context_changes(self) -> None:
        self.app.enter_simulation_mode()
        self.app.capture_dark()
        self.app.capture_reference()
        original_context = self.app.frame_context
        self.assertIsNotNone(original_context)
        self.assertIsNotNone(self.app.dark_context)
        self.assertIsNotNone(self.app.reference_context)

        self.app.simulation_settings = {**self.app.simulation_settings, "integration_us": 20_000}
        self.app.apply_simulation_configuration(self.app.simulation_settings)
        self.app.accept_spectrum(self.app.generate_simulated_spectrum(), time.time())

        self.assertNotEqual(self.app.frame_context, original_context)
        self.assertIsNone(self.app.dark_spectrum)
        self.assertIsNone(self.app.reference_spectrum)
        self.assertIsNone(self.app.dark_context)
        self.assertIsNone(self.app.reference_context)

    def test_four_workspaces_and_contextual_save_state(self) -> None:
        tabs = [self.app.sidebar_notebook.tab(tab_id, "text") for tab_id in self.app.sidebar_notebook.tabs()]
        self.assertEqual(tabs, ["采集", "处理", "PL荧光", "AB吸收"])
        self.app.enter_simulation_mode()
        self.select_workspace("采集")
        self.assertEqual(str(self.app.save_button.cget("state")), "normal")
        self.assertTrue(self.app.sidebar_save_panel.winfo_manager())
        for workspace in ("处理", "PL荧光", "AB吸收"):
            self.select_workspace(workspace)
            self.assertFalse(self.app.sidebar_save_panel.winfo_manager())
        self.select_workspace("采集")
        self.assertTrue(self.app.sidebar_save_panel.winfo_manager())
        self.assertEqual(self.app.pl_background_button.cget("text"), "选择 BG 文件")
        self.assertEqual(self.app.ab_samples_button.cget("text"), "选择样品信号")
        self.assertEqual(self.app.ab_reference_file_button.cget("text"), "选择 BG 参考")
        self.assertEqual(self.app.edc_check.cget("text"), "启用暗噪声校正")
        self.assertEqual(self.app.nl_check.cget("text"), "启用非线性校正")
        self.assertEqual(self.app.ab_dark_file_button.cget("text"), "选择暗谱（可选）")
        self.assertIn("吸光度 (A)", self.app.mode_combo.cget("values"))
        self.assertEqual(self.app.processing_reference_button.cget("text"), "记录 AB 参考")
        self.assertEqual(str(self.app.pl_turbo_check.cget("state")), "normal")

    def test_device_options_match_oceanview_defaults_after_sync(self) -> None:
        self.app.sync_device_option_values(
            {
                "electric_dark_supported": True,
                "electric_dark": False,
                "nonlinearity_supported": True,
                "nonlinearity": True,
                "trigger_supported": True,
                "light_source_supported": True,
                "light_source": True,
                "trigger_mode": 1,
            }
        )

        self.assertTrue(self.app.electric_dark_var.get())
        self.assertFalse(self.app.nonlinearity_var.get())
        self.assertEqual(self.app.trigger_mode_var.get(), "按需 / 正常")
        self.assertFalse(self.app.light_source_var.get())
        payload = self.app.acquisition_payload(
            {"integration_us": 10_000, "averages": 1, "boxcar": 0, "interval_ms": 80}
        )
        self.assertTrue(payload["electric_dark"])
        self.assertTrue(payload["apply_electric_dark"])
        self.assertFalse(payload["nonlinearity"])
        self.assertTrue(payload["apply_nonlinearity"])
        self.assertEqual(payload["trigger_mode"], 0)
        self.assertTrue(payload["apply_trigger"])
        self.assertFalse(payload["light_source"])
        self.assertTrue(payload["apply_light_source"])

        self.app.sync_device_option_values(
            {
                "electric_dark_supported": False,
                "electric_dark": True,
                "nonlinearity_supported": False,
                "trigger_supported": False,
                "light_source_supported": False,
                "trigger_mode": 0,
            }
        )
        self.assertFalse(self.app.electric_dark_var.get())
        self.assertEqual(str(self.app.edc_check.cget("state")), "disabled")

    def test_sdk_ready_schedules_only_one_automatic_connection_attempt(self) -> None:
        calls: list[str] = []
        scheduled: list[tuple[int, object]] = []
        self.app.toggle_connection = lambda: calls.append("connect")
        original_after = self.root.after
        self.root.after = lambda delay, callback=None, *_args: scheduled.append((delay, callback)) or "test-after"
        try:
            self.app.handle_sdk_ready((3, 1, 4))
        finally:
            self.root.after = original_after
        auto_connect = [callback for delay, callback in scheduled if delay == 100 and callback is not None]
        self.assertEqual(len(auto_connect), 1)
        auto_connect[0]()
        self.assertEqual(calls, ["connect"])
        self.assertTrue(self.app.auto_connect_attempted)

        scheduled.clear()
        original_after = self.root.after
        self.root.after = lambda delay, callback=None, *_args: scheduled.append((delay, callback)) or "test-after"
        try:
            self.app.handle_sdk_ready((3, 1, 4))
        finally:
            self.root.after = original_after
        self.assertFalse(any(delay == 100 for delay, _callback in scheduled))
        self.assertEqual(calls, ["connect"])

    def test_processing_uses_dark_reference_normalization_and_absorbance(self) -> None:
        self.app.enter_simulation_mode()
        self.app.capture_dark()
        self.assertEqual(self.app.dark_state_var.get(), "共享暗谱：已记录")
        self.assertEqual(self.app.dark_button.cget("style"), "Selected.TButton")
        self.assertEqual(self.app.baseline_state_label.cget("style"), "BaselinePartial.TLabel")
        self.select_workspace("处理")
        self.app.mode_var.set("峰值归一化")
        self.app.refresh_plot()
        self.assertTrue(self.app.plot.values)
        self.assertAlmostEqual(max(self.app.plot.values), 1.0)
        self.assertIn("暗谱：已记录", self.app.baseline_state_var.get())
        reference = [value + 1000.0 for value in self.app.raw_spectrum]
        self.app.accept_spectrum(reference, time.time())
        self.app.capture_reference()
        self.assertEqual(self.app.processing_reference_button.cget("style"), "Selected.TButton")
        self.assertEqual(self.app.baseline_state_label.cget("style"), "BaselineReady.TLabel")
        sample = [dark + (reference_value - dark) * 0.5 for dark, reference_value in zip(self.app.dark_spectrum, reference)]
        self.app.accept_spectrum(sample, time.time())
        self.app.mode_var.set("吸光度 (A)")
        self.app.refresh_plot()
        self.assertTrue(self.app.plot.values)
        self.assertAlmostEqual(self.app.plot.values[0], 0.3010299957)
        self.assertIn("参考谱：已记录", self.app.baseline_state_var.get())
        self.app.clear_dark()
        self.assertIn("暗谱：未记录", self.app.baseline_state_var.get())
        self.assertEqual(self.app.dark_button.cget("style"), "TButton")
        self.assertEqual(self.app.baseline_state_label.cget("style"), "BaselinePartial.TLabel")

    def test_pl_overlay_is_not_replaced_by_live_frames(self) -> None:
        first = Path(self.temp_dir.name) / "pl_a.csv"
        second = Path(self.temp_dir.name) / "pl_b.txt"
        first.write_text("wavelength,intensity\n400,10\n410,20\n420,15\n", encoding="utf-8")
        second.write_text("wavelength\tintensity\n400\t5\n410\t25\n420\t10\n", encoding="utf-8")
        result = self.app.pl_result_service.load_many((first, second))
        self.assertEqual(result.added, 2)
        self.app.pl_active_var.set(self.app.pl_result_service.active_id or "")
        self.app.rebuild_pl_file_list()
        self.assertEqual(self.app.pl_open_button.cget("style"), "Selected.TButton")
        self.select_workspace("PL荧光")
        names_before = [series.name for series in self.app.plot.series]
        values_before = [series.values for series in self.app.plot.series]
        self.assertEqual(names_before, ["pl_a", "pl_b"])
        row_widgets_before = tuple(str(widget) for widget in self.app.pl_list_frame.winfo_children())
        line_items_before = self.app.plot.canvas.find_withtag("spectrum_line")
        self.assertTrue(self.app.pl_list_canvas.bind("<MouseWheel>"))
        first_row = self.app.pl_row_variables[self.app.pl_result_service.entries[0].entry_id]
        self.assertTrue(first_row["color_button"].bind("<MouseWheel>"))

        self.app.pl_turbo_var.set(True)
        self.app.on_pl_display_changed()
        self.assertEqual(tuple(str(widget) for widget in self.app.pl_list_frame.winfo_children()), row_widgets_before)
        self.assertEqual(self.app.plot.canvas.find_withtag("spectrum_line"), line_items_before)
        self.assertNotEqual(self.app.plot.series[0].color, self.app.plot.series[1].color)

        self.app.enter_simulation_mode()
        self.app.accept_spectrum(self.app.generate_simulated_spectrum(), time.time())
        self.assertEqual([series.name for series in self.app.plot.series], names_before)
        self.assertEqual([series.values for series in self.app.plot.series], values_before)

    def test_historical_ab_triplet_displays_absorbance(self) -> None:
        paths = []
        for name, values in (
            ("sample", (6, 11, 21)),
            ("dark", (1, 1, 1)),
            ("reference", (11, 21, 41)),
        ):
            path = Path(self.temp_dir.name) / f"{name}.csv"
            path.write_text(
                "wavelength,intensity\n" + "\n".join(f"{350 + index},{value}" for index, value in enumerate(values)),
                encoding="utf-8",
            )
            paths.append(path)
        self.app.historical_absorbance_service.load_samples((paths[0],))
        self.app.historical_absorbance_service.load_dark(paths[1])
        self.app.historical_absorbance_service.load_reference(paths[2])
        self.app.ab_active_var.set(self.app.historical_absorbance_service.active_id or "")
        self.app.rebuild_ab_file_list()
        self.assertEqual(self.app.ab_samples_button.cget("style"), "Selected.TButton")
        self.assertEqual(self.app.ab_reference_file_button.cget("style"), "Selected.TButton")
        self.assertEqual(self.app.ab_dark_file_button.cget("style"), "Selected.TButton")
        self.select_workspace("AB吸收")
        self.assertEqual(self.app.plot.y_label, "吸光度 (A)")
        self.assertAlmostEqual(self.app.plot.values[0], 0.3010299957)
        self.assertIn("无效点共 0", self.app.ab_result_status_var.get())

    def test_workspace_display_preferences_persist_without_file_paths(self) -> None:
        self.select_workspace("PL荧光")
        self.app.mode_var.set("峰值归一化")
        self.app.pl_normalize_var.set(True)
        self.app.on_pl_display_changed()
        self.app.pl_turbo_var.set(True)
        self.app.ab_mode_var.set("透过率 (%)")
        self.app.save_preferences()
        saved = self.app.load_preferences()
        self.assertEqual(saved["workspace"], "PL荧光")
        self.assertEqual(saved["processing_mode"], "峰值归一化")
        self.assertTrue(saved["pl_normalize"])
        self.assertTrue(saved["pl_turbo"])
        self.assertEqual(saved["ab_mode"], "透过率 (%)")
        self.assertNotIn("pl_files", saved)


if __name__ == "__main__":
    unittest.main()
