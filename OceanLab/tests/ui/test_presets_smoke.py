from __future__ import annotations

import math
import tempfile
import time
import tkinter as tk
from tkinter import font as tkfont
import unittest
from unittest import mock
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from oceanlab.adapters.filesystem import DelimitedSpectrumExporter, JsonPreferencesRepository
from oceanlab.adapters.spectrum_files import DelimitedSpectrumImporter
from oceanlab.adapters.oceandirect import is_oceandirect_home, normalize_sdk_home
from oceanlab.adapters.simulation import SimulatedSpectrometer
from oceanlab.bootstrap import service_factory
from oceanlab.config import SAVE_FORMAT_TXT, SAVE_SUFFIX_NONE, SAVE_SUFFIX_SEQUENCE, SAVE_SUFFIX_TIME
from oceanlab.domain.result_processing import photon_energy_ev
from oceanlab.domain.results import PlotSeries
from oceanlab.application.acquisition import CoalescingEventQueue
from oceanlab.ui.app import OceanLabApp
from oceanlab.ui.tauc_modes import TAUC_DIRECT, TAUC_INDIRECT


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
        for pending in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', pending)
        self.root.destroy()
        self.temp_dir.cleanup()

    def switch_to(self, name: str) -> None:
        self.app.integration_preset_var.set(name)
        self.app.on_integration_preset_changed()

    def select_workspace(self, name: str) -> None:
        self.app.sidebar_notebook.select(self.app.workspace_tabs[name])
        self.app.on_workspace_changed()
        self.root.update_idletasks()

    @staticmethod
    def read_curve_txt(path):
        text = path.read_text(encoding="gb18030")
        lines = text.splitlines()
        rows = lines[lines.index(">>>>>Begin Spectral Data<<<<<") + 1:]
        return text, [tuple(map(float, line.split("\t"))) for line in rows]

    def assert_curve_matches_export_precision(self, rows, expected):
        self.assertEqual(len(rows), len(expected))
        for (x, y), (expected_x, expected_y) in zip(rows, expected):
            self.assertTrue(math.isclose(x, expected_x, rel_tol=5.1e-12))
            if math.isnan(expected_y):
                self.assertTrue(math.isnan(y))
            else:
                self.assertTrue(math.isclose(y, expected_y, rel_tol=5.1e-8))

    def test_curve_save_button_is_hidden_in_acquisition_and_freezes_processing_before_dialog(self):
        self.select_workspace("采集")
        self.assertEqual(self.app.curve_save_button.winfo_manager(), "")
        self.assertTrue(self.app.sidebar_save_panel.winfo_manager())
        for workspace in ("处理", "PL荧光", "AB吸收", "采集", "处理"):
            self.select_workspace(workspace)
            if workspace == "采集":
                self.assertEqual(self.app.curve_save_button.winfo_manager(), "")
                continue
            self.assertEqual(self.app.curve_save_button.winfo_manager(), "pack")
            self.assertEqual(self.app.plot_toolbar.pack_slaves().index(self.app.curve_save_button), 9)
            self.assertEqual(self.app.curve_save_button.cget("text"), "")
            neighbor = self.app.plot_toolbar.pack_slaves()[0]
            self.assertEqual(self.app.curve_save_button.winfo_reqwidth(), neighbor.winfo_reqwidth())
            self.assertEqual(self.app.curve_save_button.winfo_reqheight(), neighbor.winfo_reqheight())
        self.select_workspace("采集")
        self.app.enter_simulation_mode()
        self.select_workspace("处理")
        before = tuple(zip(self.app.plot.wavelengths, self.app.plot.values))
        self.assertGreater(len(before), 1000)
        self.app.plot.x_min, self.app.plot.x_max = before[100][0], before[200][0]
        path = Path(self.temp_dir.name) / "current.txt"
        self.app.save_directory_var.set(self.temp_dir.name)

        def choose(**kwargs):
            self.assertEqual(kwargs["initialdir"], self.temp_dir.name)
            self.assertTrue(kwargs["initialfile"].endswith(".txt"))
            self.app.accept_spectrum([value + 1000 for value in self.app.raw_spectrum], time.time())
            return str(path)

        with mock.patch("tkinter.filedialog.asksaveasfilename", side_effect=choose):
            self.app.curve_save_button.invoke()
        text, rows = self.read_curve_txt(path)
        self.assert_curve_matches_export_precision(rows, before)
        self.assertIn("# Workspace: 处理", text)
        self.assertEqual(self.app.load_preferences()["curve_export_directory"], self.temp_dir.name)
        self.assertEqual(self.app.curve_save_button.cget("style"), "PlotTool.TButton")

    def test_curve_export_processing_tracks_verified_color(self):
        self.app.enter_simulation_mode()
        for index in range(3):
            self.app.base_name_var.set(f"sample{index}")
            self.app.single_in_progress = True
            self.app.accept_spectrum(self.app.generate_simulated_spectrum(), time.time())
            if index < 2:
                self.app.commit_saved_single_capture(f"sample{index}")
        self.assertEqual(len(self.app.plot.series), 3)
        self.select_workspace("处理")
        expected = self.app.plot.series[0]
        path = Path(self.temp_dir.name) / "latest.txt"
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_plot_curves()
        _, rows = self.read_curve_txt(path)
        self.assert_curve_matches_export_precision(rows, tuple(zip(expected.wavelengths, expected.values)))
        self.assertEqual(list(path.parent.glob("*.txt")), [path])
        self.assertEqual(self.app.curve_save_button.cget("style"), "Saved.PlotTool.TButton")
        self.assertEqual(self.app.ttk.Style().lookup("Saved.PlotTool.TButton", "background"), "#198754")
        self.app.single_in_progress = True
        self.app.accept_spectrum(self.app.generate_simulated_spectrum(), time.time())
        self.assertEqual(self.app.curve_save_button.cget("style"), "PlotTool.TButton")

    def test_curve_export_failure_cannot_turn_button_green(self):
        self.app.enter_simulation_mode()
        self.select_workspace("处理")
        path = Path(self.temp_dir.name) / "failed.txt"
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(path)), \
             mock.patch.object(self.app.spectrum_exporter, "export_curve", side_effect=ValueError("verification failed")), \
             mock.patch("tkinter.messagebox.showerror") as error:
            self.app.save_plot_curves()
        error.assert_called_once()
        self.assertEqual(self.app.curve_save_button.cget("style"), "PlotTool.TButton")
        self.assertFalse(path.exists())

    def test_curve_export_processing_writes_normalized_pixel_data(self):
        self.app.enter_simulation_mode()
        self.select_workspace("处理")
        self.app.mode_var.set("峰值归一化")
        self.app.x_axis_var.set("像素索引")
        self.app.refresh_plot()
        expected = tuple(zip(self.app.plot.wavelengths, self.app.plot.values))
        path = Path(self.temp_dir.name) / "normalized.txt"
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.save_plot_curves()
        text, rows = self.read_curve_txt(path)
        self.assert_curve_matches_export_precision(rows, expected)
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(max(y for x, y in rows), 1)
        self.assertIn("# X: 像素索引", text)
        self.assertIn("# Y: 归一化强度", text)

    def test_curve_export_pl_batch_preserves_bg_normalization_visibility_and_old_files(self):
        directory = Path(self.temp_dir.name)
        paths = []
        for name, values in (("one", (3, 5, 9)), ("two", (5, 9, 17)), ("hidden", (7, 11, 19)), ("bg", (1, 1, 1))):
            path = directory / (name + ".csv")
            path.write_text("x,y\n" + "\n".join(f"{400 + i * 10},{value}" for i, value in enumerate(values)), encoding="utf-8")
            paths.append(path)
        service = self.app.pl_result_service
        service.load_many(paths[:3])
        service.load_background(paths[3])
        service.set_visible(service.entries[2].entry_id, False)
        for entry in service.entries[:2]:
            service.rename(entry.entry_id, "重复名")
        self.select_workspace("PL荧光")
        self.app.pl_normalize_var.set(True)
        self.app.refresh_plot()
        expected = tuple(self.app.plot.series)
        output = directory / "out"
        output.mkdir()
        existing = output / "one_归一化_nm.txt"
        existing.write_bytes(b"old result")
        with mock.patch("tkinter.filedialog.askdirectory", return_value=str(output)) as dialog, mock.patch("tkinter.filedialog.asksaveasfilename") as single_dialog:
            self.app.save_plot_curves()
        self.assertEqual(dialog.call_count, 1)
        single_dialog.assert_not_called()
        self.assertEqual(existing.read_bytes(), b"old result")
        for path, curve in zip((output / "one_归一化_nm_2.txt", output / "two_归一化_nm.txt"), expected):
            text, rows = self.read_curve_txt(path)
            self.assert_curve_matches_export_precision(rows, tuple(zip(curve.wavelengths, curve.values)))
            self.assertIn("# BG: " + str(paths[3]), text)
            self.assertIn("# Peak normalization: True", text)
        self.assertEqual(len(list(output.glob("*.txt"))), 3)
        self.assertEqual(self.app.curve_save_button.cget("style"), "Saved.PlotTool.TButton")
        self.app.pl_normalize_var.set(False)
        self.app.refresh_plot()
        self.assertEqual(self.app.curve_save_button.cget("style"), "PlotTool.TButton")

    def test_curve_export_ab_batch_keeps_each_group_bg_and_ev_tauc_pairing(self):
        directory = Path(self.temp_dir.name)
        service = self.app.historical_absorbance_service
        for index, (start, sample_values, bg_values) in enumerate(((400, (50, 40, 150), (100, 0, 100)), (400, (50, 40, 150), (200, 200, 200))), 1):
            if index > 1:
                service.create_group()
            # The same source path is legal in two groups with different BGs.
            sample, bg = directory / "same-source.csv", directory / f"bg{index}.csv"
            for path, values in ((sample, sample_values), (bg, bg_values)):
                path.write_text("x,y\n" + "\n".join(f"{start + i * 10},{value}" for i, value in enumerate(values)), encoding="utf-8")
            service.load_samples((sample,))
            service.load_reference(bg)
        self.select_workspace("AB吸收")
        self.app.ab_mode_var.set("Tauc 计算结果")
        self.app.ab_x_axis_var.set("光子能量 (eV)")
        self.app.refresh_plot()
        expected = tuple(self.app.plot.series)
        output = directory / "out"
        output.mkdir()
        with mock.patch("tkinter.filedialog.askdirectory", return_value=str(output)):
            self.app.save_plot_curves()
        files = sorted(output.glob("*.txt"))
        self.assertEqual(len(files), 2)
        for index, (path, curve) in enumerate(zip(files, expected), 1):
            self.assertIn(f"same-source_分析组 {index}_Tauc_Direct_2_eV", path.stem)
            text, rows = self.read_curve_txt(path)
            self.assertIn("# BG: " + str(directory / f"bg{index}.csv"), text)
            self.assertIn("# X: 光子能量 (eV)", text)
            self.assertIn("# Y: Tauc (A·eV)²", text)
            self.assert_curve_matches_export_precision(rows, tuple(zip(curve.wavelengths, curve.values)))
            self.assertLess(rows[0][0], rows[-1][0])
        first_files = {path.name: path.read_bytes() for path in files}
        with mock.patch("tkinter.filedialog.askdirectory", return_value=""):
            self.app.save_plot_curves()
        self.assertEqual({path.name: path.read_bytes() for path in output.glob("*.txt")}, first_files)

    def test_ab_group_header_buttons_hide_restore_export_and_remove_only_that_group(self):
        directory = Path(self.temp_dir.name)
        service = self.app.historical_absorbance_service
        paths = []
        for index in range(2):
            if index:
                service.create_group()
            sample, hidden, bg = (directory / f"{name}{index}.csv" for name in ("sample", "hidden", "bg"))
            for path, value in ((sample, 50), (hidden, 40), (bg, 100)):
                path.write_text(f"x,y\n400,{value}\n410,{value}\n420,{value}\n", encoding="utf-8")
                paths.append(path)
            service.load_reference(bg)
            service.load_samples((sample, hidden))
            service.set_visible(service.current_group.entries[1].entry_id, False)
        original_files = {path: path.read_bytes() for path in paths}
        first, second = service.groups
        self.app.rebuild_ab_file_list()
        self.select_workspace("AB吸收")
        self.app.ab_mode_var.set(TAUC_DIRECT)
        self.app.refresh_plot()
        cards = tuple(self.app.ab_list_frame.winfo_children())
        second_values = self.app.plot.series[1].values
        self.app.ab_group_widgets[first.group_id]["visible_button"].invoke()
        self.assertFalse(first.visible)
        self.assertEqual(len(self.app.plot.series), 1)
        self.assertEqual(self.app.plot.series[0].values, second_values)
        self.assertEqual(tuple(self.app.ab_list_frame.winfo_children()), cards)
        self.assertEqual([entry.visible for entry in first.entries], [True, False])
        output = directory / 'visible-only.txt'
        with mock.patch('tkinter.filedialog.asksaveasfilename', return_value=str(output)) as dialog:
            self.app.save_plot_curves()
        self.assertIn('Tauc_Direct_2', dialog.call_args[1]['initialfile'])
        self.assertIn('# Group: ' + second.name, self.read_curve_txt(output)[0])
        self.app.ab_group_widgets[first.group_id]["visible_button"].invoke()
        self.assertEqual(len(self.app.plot.series), 2)
        self.assertEqual([entry.visible for entry in first.entries], [True, False])
        with mock.patch('tkinter.messagebox.askyesno', return_value=False) as confirmation:
            self.app.ab_group_widgets[first.group_id]["remove_button"].invoke()
        self.assertEqual(service.groups, [first, second])
        self.assertEqual(len(self.app.plot.series), 2)
        self.assertEqual(tuple(self.app.ab_list_frame.winfo_children()), cards)
        self.assertEqual(confirmation.call_args[1]['default'], 'no')
        self.assertIn(first.name, confirmation.call_args[0][1])
        with mock.patch('tkinter.messagebox.askyesno', return_value=True):
            self.app.ab_group_widgets[first.group_id]["remove_button"].invoke()
        self.assertEqual(service.groups, [second])
        self.assertEqual(self.app.plot.series[0].values, second_values)
        self.assertEqual({path: path.read_bytes() for path in paths}, original_files)
        self.assertEqual(tuple(self.app.ab_group_combo.cget('values')), (second.name,))
        with mock.patch('tkinter.messagebox.askyesno', return_value=True):
            self.app.ab_group_widgets[second.group_id]["remove_button"].invoke()
        self.assertEqual(service.entries, [])
        self.assertEqual(self.app.ab_active_var.get(), '')
        self.assertEqual(len(self.app.ab_group_widgets), 1)
        blank = service.current_group
        self.assertEqual(blank.name, '分析组 1')
        with mock.patch('tkinter.messagebox.askyesno') as confirmation:
            for _ in range(8):
                self.app.ab_group_widgets[blank.group_id]['remove_button'].invoke()
        confirmation.assert_not_called()
        self.assertEqual(self.app.ab_group_widgets[blank.group_id]['remove_button'].cget('state'), 'disabled')
        self.assertIs(service.current_group, blank)

    def test_tauc_export_defaults_distinguish_both_exponents_on_processing(self):
        self.app.enter_simulation_mode()
        self.app.single_acquisition()
        self.app.capture_reference()
        self.select_workspace('处理')
        names = []
        for mode in (TAUC_DIRECT, TAUC_INDIRECT):
            self.app.mode_var.set(mode)
            self.app.refresh_plot()
            with mock.patch('tkinter.filedialog.asksaveasfilename', return_value='') as dialog:
                self.app.save_plot_curves()
            names.append(dialog.call_args[1]['initialfile'])
        self.assertIn('Tauc_Direct_2', names[0])
        self.assertIn('Tauc_Indirect_1-2', names[1])
        self.assertNotEqual(names[0], names[1])

    def test_group_header_leading_icons_fit_narrow_widths_at_multiple_dpi_scales(self):
        root, app = self.root, self.app
        # Process layout events without startup dialogs or hardware polling.
        for pending in root.tk.call('after', 'info'):
            root.tk.call('after', 'cancel', pending)
        original_scaling = float(root.tk.call('tk', 'scaling'))
        group = app.historical_absorbance_service.current_group
        group.name = '分析组 123456 · 很长的分组标题用于检查文字省略 ' * 6
        self.select_workspace('AB吸收')
        root.geometry('1400x900')
        root.update()
        try:
            for scale in (1.0, 1.25, 1.5, 2.0):
                root.tk.call('tk', 'scaling', 96 / 72 * scale)
                app.rebuild_ab_file_list()
                root.update()
                for width in (round(180 * scale), round(300 * scale)):
                    with self.subTest(scale=scale, width=width):
                        app.ab_list_canvas.itemconfigure(app.ab_list_window, width=width)
                        root.update()
                        widgets = app.ab_group_widgets[group.group_id]
                        header = widgets['header']
                        eye, trash = widgets['visible_button'], widgets['remove_button']
                        label = app.ab_group_header_labels[group.group_id]
                        self.assertGreaterEqual(eye.winfo_x(), 0)
                        self.assertLessEqual(eye.winfo_x() + eye.winfo_width(), trash.winfo_x())
                        self.assertLessEqual(trash.winfo_x() + trash.winfo_width(), label.winfo_x())
                        self.assertLessEqual(label.winfo_x() + label.winfo_width(), header.winfo_width())
                        self.assertGreaterEqual(eye.winfo_width(), round(24 * scale) - 1)
                        self.assertTrue(label.cget('text').endswith('…'))
                        self.assertEqual(widgets['title'].get(), app.ab_group_header_text(group))
        finally:
            root.tk.call('tk', 'scaling', original_scaling)

    def test_curve_export_empty_and_cancel_do_not_write_or_mark_capture_saved(self):
        self.select_workspace("处理")
        with mock.patch("tkinter.messagebox.showwarning") as warning, mock.patch("tkinter.filedialog.asksaveasfilename") as dialog:
            self.app.save_plot_curves()
            warning.assert_called_once()
            dialog.assert_not_called()
        self.app.enter_simulation_mode()
        histories = {key: list(value) for key, value in self.app.single_capture_history.items()}
        with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=""):
            self.app.save_plot_curves()
        self.assertEqual(self.app.single_capture_history, histories)
        self.assertEqual(self.app.curve_export_directory, "")

    def test_layout_switch_persistence_and_state(self) -> None:
        self.assertEqual(self.app.save_format_var.get(), SAVE_FORMAT_TXT)
        self.assertEqual(self.app.save_button_text_var.get(), "保存 TXT")
        self.assertEqual(self.app.integration_spin.cget("width"), 6)
        self.assertEqual(self.app.averages_spin.cget("width"), 6)
        self.assertEqual(self.app.integration_spin.winfo_width(), self.app.averages_spin.winfo_width())
        self.assertEqual(self.app.integration_spin.winfo_rootx(), self.app.averages_spin.winfo_rootx())
        self.assertEqual(self.app.integration_spin.master.master.pack_info()["padx"], (64, 0))
        self.assertGreaterEqual(int(self.app.integration_unit_combo.master.cget("width")), 70)
        self.assertGreaterEqual(self.app.integration_unit_combo.winfo_width(), 70)
        self.assertEqual(len(self.app.integration_preset_buttons), 3)
        self.assertTrue(all(str(button.cget("state")) == "normal" for button in self.app.integration_preset_buttons))
        self.assertTrue(all(int(button.master.cget("width")) == 56 for button in self.app.integration_preset_buttons))
        self.assertTrue(all(int(button.master.cget("height")) == 44 for button in self.app.integration_preset_buttons))
        self.assertIn("bold", str(self.app.preset_selector_label.cget("font")))
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

    def test_save_verified_color_is_independent_for_each_preset(self) -> None:
        self.app.mark_save_verified()
        self.assertTrue(self.app.preset_save_verified[0])
        self.assertEqual(str(self.app.save_button.cget("style")), "Saved.TButton")

        self.switch_to("预设2")
        self.assertFalse(self.app.save_verified)
        self.assertEqual(str(self.app.save_button.cget("style")), "Primary.TButton")
        self.app.mark_save_verified()
        self.assertTrue(self.app.preset_save_verified[1])

        self.switch_to("预设1")
        self.assertTrue(self.app.save_verified)
        self.assertEqual(str(self.app.save_button.cget("style")), "Saved.TButton")
        self.app.base_name_var.set("preset-one-changed")
        self.assertFalse(self.app.preset_save_verified[0])
        self.assertEqual(str(self.app.save_button.cget("style")), "Primary.TButton")

        self.switch_to("预设2")
        self.assertTrue(self.app.save_verified)
        self.assertEqual(str(self.app.save_button.cget("style")), "Saved.TButton")

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
        self.assertEqual(self.app.pl_normalize_check.winfo_class(), "TCheckbutton")
        self.assertEqual(self.app.pl_turbo_check.winfo_class(), "TCheckbutton")
        before_normalize = self.app.pl_normalize_var.get()
        self.app.pl_normalize_check.invoke()
        self.assertNotEqual(self.app.pl_normalize_var.get(), before_normalize)

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
        self.assertTrue(self.app.plot.series[0].name.startswith("待保存   ｜"))

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
        self.assertTrue(self.app.plot.series[0].name.startswith("最新已保存 ｜"))
        self.assertEqual({item.name.index("｜") for item in self.app.plot.series}, {6})
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
        self.assertTrue(self.app.plot.series[0].name.startswith("最新已保存 ｜1000GPa"))

    def test_integration_position_and_composed_name(self) -> None:
        self.assertEqual(self.app.name_prefix_var.get(), "")
        self.assertEqual(self.app.base_name_var.get(), "spectrum")
        self.assertEqual(self.app.name_suffix_var.get(), "{integration}-{averages}")
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
            "KCaCl3Eu-P2.0GPa02-L360nm-100ms-3times",
        )
        self.app.position_var.set("")
        self.assertEqual(
            self.app.compose_save_base_name(),
            "KCaCl3Eu-P2.0GPa-L360nm-100ms-3times",
        )
        self.app.name_suffix_var.set("L360nm-{number}-100ms")
        self.app.position_var.set("02")
        self.assertEqual(self.app.compose_save_base_name(), "KCaCl3Eu-P2.0GPa02-L360nm-100ms")

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
        self.assertEqual(
            self.app.build_save_filename(
                base_name="sample",
                save_format=SAVE_FORMAT_TXT,
                suffix_mode=SAVE_SUFFIX_NONE,
            ),
            "sample.txt",
        )

    def test_save_panel_is_fixed_below_scrollable_acquisition_area(self) -> None:
        self.root.update_idletasks()
        self.assertEqual(int(self.app.sidebar_notebook.master.cget("width")), 570)
        self.assertEqual(int(self.app.cursor_shell.cget("width")), 190)
        self.assertEqual(int(self.app.cursor_shell.cget("height")), 48)
        self.assertEqual(str(self.app.start_button.cget("style")), "")
        self.assertEqual(str(self.app.single_button.cget("style")), "Primary.TButton")
        self.assertLess(self.app.start_button.winfo_width(), self.app.stop_button.winfo_width())
        self.assertLess(self.app.stop_button.winfo_width(), self.app.single_button.winfo_width())
        self.assertAlmostEqual(
            self.app.stop_button.winfo_width() / self.app.start_button.winfo_width(),
            2.0,
            delta=0.15,
        )
        self.assertAlmostEqual(
            self.app.single_button.winfo_width() / self.app.start_button.winfo_width(),
            3.0,
            delta=0.15,
        )
        self.assertFalse(hasattr(self.app, "apply_button"))
        self.assertGreaterEqual(self.app.acquisition_progress.winfo_reqheight(), 16)
        self.assertEqual(
            int(self.app.ttk.Style().lookup("Acquisition.Horizontal.TProgressbar", "thickness")),
            16,
        )
        self.assertEqual(int(self.app.integration_spin.master.cget("height")), 54)
        self.assertEqual(int(self.app.averages_spin.master.cget("height")), 54)
        self.assertEqual(int(self.app.integration_unit_combo.master.cget("height")), 54)
        self.assertGreaterEqual(int(self.app.status_lamp.cget("width")), 30)
        self.assertGreaterEqual(int(self.app.status_lamp.cget("height")), 26)
        self.assertEqual(len(self.app.plot_toolbar_images), 10)
        self.assertTrue(all(image.width() >= 38 and image.height() >= 38 for image in self.app.plot_toolbar_images))
        self.assertEqual(len(self.app.plot_toolbar.winfo_children()), 11)
        self.assertIs(self.app.plot_legend_canvas.master, self.app.plot_toolbar)
        self.assertIn("crosshair", self.app.plot_tool_buttons)
        self.assertEqual(int(self.app.peak_metric_label.cget("width")), 8)
        self.assertEqual(int(self.app.position_metric_label.cget("width")), 10)
        self.assertEqual(int(self.app.frame_metric_label.cget("width")), 9)
        self.assertEqual(self.app.peak_metric_group.cget("background"), "#eef6ff")
        self.assertEqual(self.app.position_metric_group.cget("background"), "#f5f3ff")
        self.assertEqual(self.app.frame_metric_group.cget("background"), "#f0fdf4")
        self.assertEqual(self.app.peak_metric_label.cget("anchor"), "w")
        self.assertEqual(self.app.position_metric_label.cget("anchor"), "w")
        self.assertEqual(self.app.frame_metric_label.cget("anchor"), "w")
        self.assertFalse(self.app.saturation_label.cget("width"))
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

    def test_save_panel_returns_to_original_pack_slot_after_workspace_switch(self) -> None:
        for workspace in ("处理", "PL荧光", "AB吸收", "采集"):
            self.select_workspace(workspace)

        self.root.update_idletasks()
        panel = self.app.sidebar_save_panel
        notebook = self.app.sidebar_notebook
        self.assertEqual(panel.winfo_manager(), "pack")
        pack_order = list(panel.master.pack_slaves())
        self.assertLess(pack_order.index(panel), pack_order.index(notebook))
        self.assertGreater(panel.winfo_height(), 0)
        self.assertGreater(
            self.app.save_button.winfo_height(),
            0,
            f"panel={panel.winfo_geometry()} row={self.app.save_button.master.winfo_geometry()}",
        )
        self.assertGreaterEqual(
            panel.winfo_rooty() + 2,
            notebook.winfo_rooty() + notebook.winfo_height(),
        )

    def test_successful_single_capture_leaves_progress_green(self) -> None:
        self.app.single_in_progress = True
        self.app.single_started_at = time.monotonic()
        self.app.finish_single_acquisition(success=True)
        self.root.update_idletasks()

        self.assertEqual(self.app.single_progress_var.get(), 100.0)
        self.assertEqual(
            str(self.app.acquisition_progress.cget("style")),
            "AcquisitionComplete.Horizontal.TProgressbar",
        )
        self.assertEqual(
            self.app.ttk.Style().lookup(
                "AcquisitionComplete.Horizontal.TProgressbar",
                "background",
            ),
            "#198754",
        )
        self.assertEqual(
            self.app.status_lamp.itemcget(self.app.status_lamp_dot, "fill"),
            "#198754",
        )

    def prepare_fake_device_events(self):
        self.app.enter_simulation_mode()
        self.app.simulation_mode = False
        self.app.events = CoalescingEventQueue()
        self.app.worker.send = mock.Mock()
        return self.app.simulation_context()

    def test_connect_configures_without_untracked_preview_and_blocks_early_click(self):
        from dataclasses import asdict

        connected = SimulatedSpectrometer().connect()
        self.app.events = CoalescingEventQueue()
        self.app.worker.send = mock.Mock()
        self.app.events.put(("connected", dict(info=connected.info, settings=asdict(connected.settings), wavelengths=list(connected.wavelengths))))
        self.app.poll_events()
        self.assertEqual(self.app.worker.send.call_args[0], ("configure",))
        self.assertTrue(self.app.acquisition_pending)
        self.assertFalse(self.app.single_in_progress)
        self.app.single_acquisition()
        self.app.start_acquisition()
        self.assertEqual(self.app.worker.send.call_count, 1)
        self.assertEqual(str(self.app.single_button.cget("state")), "disabled")

    def test_single_only_completes_from_its_own_request_and_drops_late_frames(self):
        context = self.prepare_fake_device_events()
        self.app.single_acquisition()
        request_id = self.app.active_acquisition_request_id
        self.app.single_acquisition()
        self.assertEqual(self.app.worker.send.call_count, 1)
        spectrum = [7.0] * len(self.app.wavelengths)
        self.app.accept_spectrum(spectrum, time.time(), context, "old-preview")
        self.assertTrue(self.app.single_in_progress)
        self.assertNotIn(0, self.app.single_capture_candidates)
        self.app.accept_spectrum(spectrum, time.time(), context, request_id)
        self.assertFalse(self.app.single_in_progress)
        candidate = self.app.single_capture_candidates[0]
        self.app.accept_spectrum([8.0] * len(spectrum), time.time(), context, request_id)
        self.assertIs(self.app.single_capture_candidates[0], candidate)
        self.assertEqual(self.app.raw_spectrum, spectrum)
        self.assertEqual(self.app.single_progress_var.get(), 100.0)

    def test_real_single_cancel_is_enabled_and_drops_already_queued_frame(self):
        context = self.prepare_fake_device_events()
        self.app.single_acquisition()
        request_id = self.app.active_acquisition_request_id
        self.assertEqual(str(self.app.stop_button.cget("state")), "normal")
        self.app.stop_acquisition()
        self.assertTrue(self.app.cancel_requested)
        self.assertTrue(self.app.connected)
        self.assertFalse(self.app.connection_pending)
        self.assertEqual(self.app.stop_button.cget('text'), '正在取消')
        self.assertEqual(str(self.app.stop_button.cget('state')), 'disabled')
        self.assertEqual(str(self.app.single_button.cget('state')), 'disabled')
        self.app.accept_spectrum([8.0] * len(self.app.wavelengths), time.time(), context, request_id)
        self.assertNotIn(0, self.app.single_capture_candidates)
        self.app.events.put(("acquisition_state", dict(running=False, request_id=request_id, cancelled=True)))
        self.app.poll_events()
        self.assertFalse(self.app.single_in_progress)
        self.assertFalse(self.app.connection_pending)
        self.assertFalse(self.app.acquisition_pending)
        self.assertEqual(str(self.app.single_button.cget("state")), "normal")
        self.assertTrue(self.app.connected)
        self.assertIn('仪器保持连接', self.app.status_var.get())
        self.app.accept_spectrum([8.0] * len(self.app.wavelengths), time.time(), context, request_id)
        self.assertNotIn(0, self.app.single_capture_candidates)

    def test_continuous_pause_freezes_plot_and_cannot_restart_until_matching_ack(self):
        context = self.prepare_fake_device_events()
        self.app.active_acquisition_request_id = 'stream-1'
        self.app.set_acquisition_state(True)
        original = list(self.app.raw_spectrum)
        self.app.stop_acquisition()
        self.assertTrue(self.app.connected)
        self.assertFalse(self.app.connection_pending)
        self.assertTrue(self.app.acquisition_pending)
        self.assertEqual(self.app.stop_button.cget('text'), '正在暂停')
        for button in (self.app.stop_button, self.app.start_button, self.app.single_button):
            self.assertEqual(str(button.cget('state')), 'disabled')
        self.app.start_acquisition()
        self.app.single_acquisition()
        self.app.stop_acquisition()
        self.app.worker.send.assert_called_once_with('stop', request_id='stream-1')
        self.app.accept_spectrum([8.0] * len(self.app.wavelengths), time.time(), context, 'stream-1')
        self.assertEqual(self.app.raw_spectrum, original)
        self.app.events.put(('acquisition_state', dict(running=False, request_id='stale')))
        self.app.events.put(('acquisition_state', dict(running=True, request_id='stream-1')))
        self.app.poll_events()
        self.assertTrue(self.app.cancel_requested)
        self.assertTrue(self.app.acquisition_pending)
        self.app.events.put(('acquisition_state', dict(running=False, request_id='stream-1', cancelled=True)))
        self.app.poll_events()
        self.assertFalse(self.app.acquiring)
        self.assertFalse(self.app.cancel_requested)
        self.assertFalse(self.app.acquisition_pending)
        self.assertTrue(self.app.connected)
        self.assertEqual(self.app.raw_spectrum, original)
        self.assertEqual(str(self.app.start_button.cget('state')), 'normal')
        self.app.start_acquisition()
        self.assertEqual(self.app.worker.send.call_args[0], ('configure_start',))
        self.app.accept_spectrum([8.0] * len(self.app.wavelengths), time.time(), context, 'stream-1')
        self.assertEqual(self.app.raw_spectrum, original)

    def test_cancel_during_configuration_keeps_waiting_after_readback_and_allows_next_single(self):
        context = self.prepare_fake_device_events()
        self.app.single_acquisition()
        request_id = self.app.active_acquisition_request_id
        self.app.stop_acquisition()
        self.app.events.put(('configuration_applied', dict(request_id=request_id, action='single',
                                                          context=context, settings=self.app.applied_settings)))
        self.app.poll_events()
        self.assertTrue(self.app.cancel_requested)
        self.assertTrue(self.app.acquisition_pending)
        self.assertEqual(str(self.app.single_button.cget('state')), 'disabled')
        self.assertIn('等待当前扫描或参数同步结束', self.app.status_var.get())
        self.app.events.put(('acquisition_state', dict(request_id=request_id, running=False, cancelled=True)))
        self.app.poll_events()
        self.assertTrue(self.app.connected)
        self.app.single_acquisition()
        new_request_id = self.app.active_acquisition_request_id
        self.assertNotEqual(new_request_id, request_id)
        self.app.events.put(('acquisition_state', dict(request_id=request_id, running=False, cancelled=True)))
        self.app.poll_events()
        self.assertTrue(self.app.single_in_progress)
        self.assertTrue(self.app.acquisition_pending)
        self.assertEqual(self.app.active_acquisition_request_id, new_request_id)

    def test_continuous_start_and_parameter_application_queue_one_operation(self):
        self.prepare_fake_device_events()
        self.app.apply_settings()
        self.assertEqual(self.app.worker.send.call_count, 1)
        self.assertEqual(self.app.worker.send.call_args[0], ("configure",))
        self.app.cancel_single_acquisition()
        self.app.start_acquisition()
        self.app.start_acquisition()
        self.app.single_acquisition()
        self.assertEqual(self.app.worker.send.call_count, 2)
        self.assertEqual(self.app.worker.send.call_args[0], ("configure_start",))
        self.assertTrue(self.app.acquisition_pending)

    def test_rendering_error_preserves_event_polling_and_reports_failed_single(self):
        context = self.prepare_fake_device_events()
        self.app.single_acquisition()
        request_id = self.app.active_acquisition_request_id
        for after_id in self.root.tk.call("after", "info"):
            self.root.after_cancel(after_id)
        self.app.events.put(("spectrum", dict(spectrum=[8.0] * len(self.app.wavelengths), timestamp=time.time(), context=context, request_id=request_id)))
        with mock.patch.object(self.app, "refresh_plot", side_effect=RuntimeError("controlled render failure")), mock.patch("tkinter.messagebox.showerror") as dialog:
            self.app.poll_events()
        self.assertTrue(dialog.called)
        self.assertFalse(self.app.single_in_progress)
        self.assertTrue(self.root.tk.call("after", "info"))
        self.app.events.put(("status", {"message": "polling resumed"}))
        self.app.poll_events()
        self.assertEqual(self.app.status_var.get(), "polling resumed")

    def test_failed_sdk_worker_can_be_replaced_without_waiting_forever(self):
        self.app.worker.join(1.0)
        self.assertFalse(self.app.worker.is_alive())
        replacement = SimpleNamespace(start=mock.Mock(), is_alive=mock.Mock(return_value=False))
        chosen = Path(self.temp_dir.name) / "valid-sdk"
        with mock.patch.object(self.app, "service_factory", return_value=replacement):
            self.app.activate_oceandirect_home(chosen)
        replacement.start.assert_called_once_with()
        self.assertEqual(self.app.sdk_home, chosen)
        self.assertIs(self.app.worker, replacement)

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
                "{integration}-{averages}",
            )
            reset_preview = self.root.getvar(variable_name)
            self.assertIn(f"KeepPrefix-{long_base}07-", reset_preview)
            self.assertIn("-1times_", reset_preview)
        finally:
            self.app.save_dialog.destroy()
            self.app.save_dialog = None

    def test_save_sequence_stepper_matches_acquisition_controls(self) -> None:
        self.app.save_suffix_var.set(SAVE_SUFFIX_SEQUENCE)
        self.app.open_save_settings()
        self.root.update_idletasks()
        try:
            self.assertEqual(int(self.app.save_sequence_digits_shell.cget("height")), 48)
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

    def test_save_suffix_none_uses_large_radio_and_plain_filename(self) -> None:
        self.app.save_format_var.set(SAVE_FORMAT_TXT)
        self.app.open_save_settings()
        self.root.update_idletasks()
        try:
            radios = (
                self.app.save_suffix_time_radio,
                self.app.save_suffix_sequence_radio,
                self.app.save_suffix_none_radio,
            )
            self.assertTrue(all(radio.box.winfo_reqwidth() >= 32 for radio in radios))
            self.assertTrue(all(radio.box.winfo_reqheight() >= 32 for radio in radios))
            self.app.save_suffix_none_radio.invoke()
            self.root.update_idletasks()
            variable_name = self.app.save_preview_label.cget("textvariable")
            preview = self.root.getvar(variable_name)
            self.assertEqual(preview, f"{self.app.compose_save_base_name()}.txt")
            self.assertEqual(str(self.app.save_sequence_digits_entry.cget("state")), "disabled")
            self.assertEqual(
                self.app.save_suffix_none_radio.box.itemcget(self.app.save_suffix_none_radio.box_dot, "state"),
                "normal",
            )
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
        legend_canvas = self.app.plot.legend_canvas
        self.assertIsNot(legend_canvas, self.app.plot.canvas)
        self.assertIsNone(self.app.plot.canvas.bbox("plot_legend"))
        legend_box = legend_canvas.bbox("plot_legend")
        self.assertIsNotNone(legend_box)
        self.assertEqual(self.app.plot._plot_rect[1], 24)
        plot_midpoint = self.app.plot._plot_rect
        self.assertIsNotNone(
            self.app.plot.data_at_pixel(
                (plot_midpoint[0] + plot_midpoint[2]) / 2,
                (plot_midpoint[1] + plot_midpoint[3]) / 2,
            )
        )
        first_legend_line = legend_canvas.find_withtag("legend_line_0")[0]
        first_legend_text = legend_canvas.find_withtag("legend_text_0")[0]
        first_legend_label = legend_canvas.itemcget(first_legend_text, "text")
        self.assertTrue(first_legend_label.startswith("spe"))
        self.assertTrue(first_legend_label.endswith("…"))
        self.assertLess(
            legend_canvas.coords(first_legend_line)[2],
            legend_canvas.bbox(first_legend_text)[0],
        )
        first_legend_box = legend_canvas.bbox("legend_text_0")
        second_legend_box = legend_canvas.bbox("legend_text_1")
        self.assertTrue(
            first_legend_box[2] <= second_legend_box[0]
            or second_legend_box[2] <= first_legend_box[0]
            or first_legend_box[3] <= second_legend_box[1]
            or second_legend_box[3] <= first_legend_box[1]
        )
        long_name = "最新已保存 ｜" + "very-long-sample-name-" * 40
        self.app.plot.set_series(
            [PlotSeries(long_name, (390.0, 420.0), (3.0, 4.0), "#0f6cbd", active=True)],
            "强度 (counts)",
        )
        self.app.plot.draw()
        truncated_legend = legend_canvas.itemcget(
            legend_canvas.find_withtag("legend_text_0")[0],
            "text",
        )
        self.assertTrue(truncated_legend.startswith("最"))
        self.assertTrue(truncated_legend.endswith("…"))
        self.assertNotIn("…sample-name", truncated_legend)
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

        self.app.device_info = replace(
            self.app.simulation_spectrometer.connect().info,
            saturation_intensity=63_000.0,
        )
        self.app.raw_spectrum = [63_000.0, 12_000.0]
        self.app.refresh_plot()
        self.assertEqual(self.app.saturation_var.get(), "饱和度: 100.0%")
        self.assertEqual(self.app.plot._current_y_bounds, (0.0, 63_050.0))
        self.assertFalse(self.app.plot.canvas.find_withtag("saturation_limit_line"))

    def test_fresh_acquisition_defaults_match_installed_oceanview(self) -> None:
        self.assertEqual(self.app.integration_var.get(), "100")
        self.assertEqual(self.app.integration_unit_var.get(), "ms")
        self.assertEqual(self.app.averages_var.get(), "1")
        self.assertEqual(self.app.boxcar_var.get(), "0")
        self.assertTrue(self.app.electric_dark_var.get())
        self.assertFalse(self.app.nonlinearity_var.get())
        self.assertEqual(self.app.x_axis_var.get(), "波长 (nm)")
        self.assertEqual(self.app.trigger_mode_var.get(), "按需 / 正常")

    def test_saturation_shows_numeric_ratio_without_plateau_judgement(self) -> None:
        self.app.device_info = replace(self.app.simulation_spectrometer.connect().info,
                                       saturation_intensity=65535.0)
        original = [-8032.8125] * 25 + [100.0, 1000.0] + [55037.2875] * 9
        self.app.wavelengths = list(range(200, 200 + len(original)))
        self.app.raw_spectrum = list(original)
        self.app.refresh_plot()
        self.assertEqual(self.app.saturation_var.get(), "饱和度: 84.0%")
        self.assertEqual(self.app.saturation_label.cget("style"), "MetricWarn.TLabel")
        self.assertEqual(self.app.raw_spectrum, original)
        for peak, text, style in ((10000.0, '饱和度: 15.3%', 'MetricSafe.TLabel'),
                                  (65535 * 0.799, '饱和度: 79.9%', 'MetricSafe.TLabel'),
                                  (65535 * 0.8, '饱和度: 80.0%', 'MetricWarn.TLabel'),
                                  (65535 * 0.949, '饱和度: 94.9%', 'MetricWarn.TLabel'),
                                  (65535 * 0.95, '饱和度: 95.0%', 'MetricDanger.TLabel'),
                                  (63047.0, '饱和度: 96.2%', 'MetricDanger.TLabel'),
                                  (65535.0, '饱和度: 100.0%', 'MetricDanger.TLabel'),
                                  (70000.0, '饱和度: 106.8%', 'MetricDanger.TLabel')):
            self.app.raw_spectrum = [peak] * len(original)
            self.app.refresh_plot()
            self.assertEqual(self.app.saturation_var.get(), text)
            self.assertEqual(self.app.saturation_label.cget('style'), style)
        self.app.raw_spectrum = []
        self.app.refresh_plot()
        self.assertEqual(self.app.saturation_var.get(), '饱和度: —')
        self.assertEqual(self.app.saturation_label.cget('style'), 'Metric.TLabel')

    def test_saturation_text_fits_with_chinese_fonts_and_dpi_scaling(self) -> None:
        label = self.app.saturation_label
        original_scaling = self.root.tk.call("tk", "scaling")
        fonts = []
        try:
            for dpi in (96, 120, 144):
                self.root.tk.call("tk", "scaling", dpi / 72)
                for family in ("Microsoft YaHei", "SimSun"):
                    font = tkfont.Font(root=self.root, family=family, size=9, weight="bold")
                    fonts.append(font)
                    for style, text in (("MetricSafe.TLabel", "饱和度: 25.8%"),
                                        ("MetricWarn.TLabel", "饱和度: 85.0%"),
                                        ("MetricDanger.TLabel", "饱和度: 100.0%"),
                                        ("MetricDanger.TLabel", "饱和度: 106.8%")):
                        with self.subTest(dpi=dpi, family=family, text=text):
                            self.app.ttk.Style().configure(style, font=font)
                            label.configure(style=style)
                            self.app.saturation_var.set(text)
                            self.root.update_idletasks()
                            required_width = font.measure(text) + 16
                            self.assertGreaterEqual(label.winfo_width(), required_width)
        finally:
            self.root.tk.call("tk", "scaling", original_scaling)

    def test_crosshair_tool_places_and_drags_free_xy_marker(self) -> None:
        self.assertEqual(self.app.current_workspace(), "采集")
        self.assertFalse(self.app.plot.crosshair_drag_preview_enabled)
        self.app.plot.set_data([400.0, 500.0, 600.0], [10.0, 50.0, 20.0], "强度 (counts)")
        self.app.plot.set_cursor_index(1)
        self.app.plot.draw()
        self.assertTrue(self.app.plot.canvas.find_withtag("data_cursor"))
        self.app.toggle_plot_mode("crosshair")
        self.assertEqual(self.app.plot.interaction_mode, "crosshair")
        self.assertFalse(self.app.plot.canvas.find_withtag("data_cursor"))
        self.assertEqual(self.app.cursor_tool_name_var.get(), "十字架")
        self.assertEqual(self.app.cursor_axis_name_var.get(), "X")
        self.assertEqual(self.app.cursor_unit_var.get(), "nm")
        left, top, right, bottom = self.app.plot._plot_rect
        press = SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2)
        self.app.plot.on_press(press)
        self.assertIsNotNone(self.app.plot.crosshair_position)
        self.assertEqual(len(self.app.plot.canvas.find_withtag("plot_crosshair")), 3)
        self.assertAlmostEqual(self.app.plot.canvas.coords("crosshair_vertical")[1], top)
        self.assertAlmostEqual(self.app.plot.canvas.coords("crosshair_vertical")[3], bottom)
        self.assertEqual(self.app.cursor_position_var.get(), "500.00")
        self.assertTrue(self.app.cursor_var.get().startswith("Y: "))

        drag = SimpleNamespace(x=left + (right - left) * 0.75, y=top + (bottom - top) * 0.25)
        with mock.patch.object(self.app.plot, "draw", wraps=self.app.plot.draw) as draw_mock:
            self.app.plot.on_drag(drag)
            self.app.plot.on_release(drag)
            draw_mock.assert_not_called()
        x_value, y_value = self.app.plot.crosshair_position
        self.assertAlmostEqual(x_value, 550.0, places=2)
        ylo, yhi = self.app.plot._current_y_bounds
        self.assertAlmostEqual(y_value, yhi - 0.25 * (yhi - ylo), places=2)
        self.assertEqual(self.app.cursor_position_var.get(), "550.00")

        self.app.toggle_plot_mode("crosshair")
        self.assertEqual(self.app.plot.interaction_mode, "normal")
        self.assertEqual(self.app.cursor_tool_name_var.get(), "数据游标")
        self.assertFalse(self.app.plot.canvas.find_withtag("plot_crosshair"))
        self.assertTrue(self.app.plot.canvas.find_withtag("data_cursor"))

        cursor_press = SimpleNamespace(x=left + (right - left) * 0.3, y=(top + bottom) / 2)
        cursor_drag = SimpleNamespace(x=left + (right - left) * 0.6, y=(top + bottom) / 2)
        with mock.patch.object(self.app.plot, "draw", wraps=self.app.plot.draw) as draw_mock:
            self.app.plot.on_press(cursor_press)
            self.app.plot.on_drag(cursor_drag)
            cursor_coords = self.app.plot.canvas.coords("data_cursor")
            self.assertAlmostEqual(cursor_coords[1], top)
            self.assertAlmostEqual(cursor_coords[3], bottom)
            self.app.plot.on_release(cursor_drag)
            draw_mock.assert_not_called()

        self.select_workspace("PL荧光")
        self.assertTrue(self.app.plot.crosshair_drag_preview_enabled)

    def test_plot_peak_downsampling_preserves_spikes_and_selection_drag_is_incremental(self) -> None:
        points = [(float(index), 1.0) for index in range(4000)]
        points[1777] = (1777.0, 9000.0)
        reduced = self.app.plot.peak_downsample(points, 600)
        self.assertLessEqual(len(reduced), 602)
        self.assertIn((1777.0, 9000.0), reduced)
        self.assertEqual(reduced[0], points[0])
        self.assertEqual(reduced[-1], points[-1])

        self.app.plot.set_data([float(index) for index in range(4000)], [1.0] * 4000, "强度 (counts)")
        self.app.plot.draw()
        self.app.plot.set_interaction_mode("select")
        left, top, right, bottom = self.app.plot._plot_rect
        press = SimpleNamespace(x=left + 20, y=top + 20)
        drag = SimpleNamespace(x=right - 20, y=bottom - 20)
        self.app.plot.on_press(press)
        with mock.patch.object(self.app.plot, "draw", wraps=self.app.plot.draw) as draw_mock:
            self.app.plot.on_drag(drag)
            draw_mock.assert_not_called()
        self.assertTrue(self.app.plot.canvas.find_withtag("zoom_selection"))

    def test_zoomed_axes_preserve_precision_pairs_edges_and_nan_gaps(self) -> None:
        wavelengths = [500.0 + index * 0.02 for index in range(21)]
        values = [float(index * index) for index in range(21)]
        self.app.plot.set_data(wavelengths, values, "强度 (counts)", "波长 (nm)")
        original_series = self.app.plot.series[0]
        self.app.plot.x_min, self.app.plot.x_max = 500.10, 500.22
        self.app.plot.draw()
        nm_labels = [
            self.app.plot.canvas.itemcget(item_id, "text")
            for item_id in self.app.plot.canvas.find_withtag("x_tick_label")
        ]
        self.assertEqual(len(nm_labels), len(set(nm_labels)))
        self.assertTrue(all("." in label for label in nm_labels))
        self.assertEqual(self.app.plot.series[0].wavelengths, tuple(wavelengths))
        self.assertEqual(self.app.plot.series[0].values, tuple(values))
        edge_points = self.app.plot.visible_series_points(
            original_series,
            500.10,
            500.22,
            include_neighbors=True,
            finite_only=False,
        )
        self.assertLess(edge_points[0][0], 500.10)
        self.assertGreater(edge_points[-1][0], 500.22)
        self.assertTrue(all(y == values[wavelengths.index(x)] for x, y in edge_points))

        energies = [1.80 + index * 0.01 for index in range(41)]
        energy_values = [float(index) for index in range(len(energies))]
        self.app.plot.set_data(energies, energy_values, "吸光度 (A)", "光子能量 (eV)")
        self.app.plot.x_min, self.app.plot.x_max = 1.92, 2.08
        self.app.plot.draw()
        ev_labels = [
            self.app.plot.canvas.itemcget(item_id, "text")
            for item_id in self.app.plot.canvas.find_withtag("x_tick_label")
        ]
        self.assertEqual(len(ev_labels), len(set(ev_labels)))
        self.assertTrue(all("." in label for label in ev_labels))

        nan = float("nan")
        self.app.plot.set_data([0, 1, 2, 3, 4], [0, 1, nan, 3, 4], "吸光度 (A)")
        self.app.plot.x_min = self.app.plot.x_max = None
        self.app.plot.draw()
        self.assertTrue(math.isnan(self.app.plot.series[0].values[2]))
        self.assertEqual(len(self.app.plot.canvas.find_withtag("spectrum_line")), 2)

    def test_baselines_are_invalidated_when_acquisition_context_changes(self) -> None:
        self.app.enter_simulation_mode()
        self.app.capture_reference()
        original_context = self.app.frame_context
        self.assertIsNotNone(original_context)
        self.assertIsNotNone(self.app.reference_context)

        self.app.simulation_settings = {**self.app.simulation_settings, "integration_us": 20_000}
        self.app.apply_simulation_configuration(self.app.simulation_settings)
        self.app.accept_spectrum(self.app.generate_simulated_spectrum(), time.time())

        self.assertNotEqual(self.app.frame_context, original_context)
        self.assertIsNone(self.app.spectrum_service.baselines.dark)
        self.assertIsNone(self.app.reference_spectrum)
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
        self.assertEqual(self.app.pl_background_button.cget("text"), "选择 BG…")
        self.assertEqual(self.app.ab_samples_button.cget("text"), "添加样品…")
        self.assertEqual(self.app.ab_reference_file_button.cget("text"), "选择 BG…")
        self.assertEqual(self.app.edc_check.cget("text"), "启用暗噪声校正")
        self.assertEqual(self.app.nl_check.cget("text"), "启用非线性校正")
        self.assertFalse(hasattr(self.app, "ab_dark_file_button"))
        self.assertFalse(hasattr(self.app, "dark_button"))
        self.assertFalse(hasattr(self.app, "clear_dark_button"))
        self.assertNotIn("暗谱扣除", self.app.mode_combo.cget("values"))
        self.assertEqual(self.app.processing_clear_baselines_button.cget("text"), "清除 AB 参考")
        self.assertIn(TAUC_DIRECT, self.app.ab_mode_combo.cget("values"))
        self.assertIn(TAUC_INDIRECT, self.app.ab_mode_combo.cget("values"))
        self.assertEqual(self.app.ab_wavelength_axis_button.cget("text"), "nm")
        self.assertEqual(self.app.ab_energy_axis_button.cget("text"), "eV")
        self.app.ab_reference_status_var.set("很长的文件名-" * 20)
        self.root.update_idletasks()
        self.assertGreaterEqual(
            self.app.ab_reference_clear_button.winfo_width(),
            self.app.ab_reference_clear_button.winfo_reqwidth(),
        )
        self.assertIn("吸光度 (A)", self.app.mode_combo.cget("values"))
        self.assertIn(TAUC_DIRECT, self.app.mode_combo.cget("values"))
        self.assertIn(TAUC_INDIRECT, self.app.mode_combo.cget("values"))
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

    def test_processing_uses_reference_only_for_transmittance_absorbance_and_tauc(self) -> None:
        self.app.enter_simulation_mode()
        self.select_workspace("处理")
        self.app.mode_var.set("峰值归一化")
        self.app.refresh_plot()
        self.assertTrue(self.app.plot.values)
        self.assertAlmostEqual(max(self.app.plot.values), 1.0)
        self.assertEqual(self.app.baseline_state_var.get(), "参考谱：未记录")
        self.assertIsNone(self.app.spectrum_service.baselines.dark)
        reference = [value + 1000.0 for value in self.app.raw_spectrum]
        self.app.accept_spectrum(reference, time.time())
        self.app.capture_reference()
        self.assertEqual(self.app.processing_reference_button.cget("style"), "Selected.TButton")
        self.assertEqual(self.app.baseline_state_label.cget("style"), "BaselineReady.TLabel")
        self.app.accept_spectrum([value * 0.5 for value in reference], time.time())
        self.app.mode_var.set("透过率 (%)")
        self.app.refresh_plot()
        self.assertAlmostEqual(self.app.plot.values[0], 50.0)
        self.app.mode_var.set("吸光度 (A)")
        self.app.refresh_plot()
        self.assertAlmostEqual(self.app.plot.values[0], 0.3010299957)
        self.assertEqual(self.app.baseline_state_var.get(), "参考谱：已记录")
        self.app.mode_var.set("Tauc 计算结果")
        self.app.refresh_plot()
        self.assertEqual(self.app.plot.y_label, "Tauc (A·eV)²")
        self.assertEqual(self.app.plot.x_label, "波长 (nm)")
        expected_tauc = (0.3010299957 * photon_energy_ev(self.app.wavelengths[0])) ** 2
        self.assertAlmostEqual(self.app.plot.values[0], expected_tauc)
        self.app.clear_baselines()
        self.assertEqual(self.app.baseline_state_var.get(), "参考谱：未记录")
        self.assertEqual(self.app.processing_reference_button.cget("style"), "TButton")
        self.assertEqual(self.app.baseline_state_label.cget("style"), "BaselineEmpty.TLabel")
        self.assertFalse(self.app.plot.values)

    def test_processing_indirect_tauc_export_preserves_negative_a_gap(self):
        self.app.enter_simulation_mode()
        self.select_workspace('处理')
        reference = [100.0] * len(self.app.raw_spectrum)
        self.app.accept_spectrum(reference, time.time())
        self.app.capture_reference()
        self.app.accept_spectrum([50.0, 100.0, 150.0] + [50.0] * (len(reference) - 3), time.time())
        self.app.mode_var.set(TAUC_INDIRECT)
        self.app.refresh_plot()
        self.assertEqual(self.app.plot.y_label, 'Tauc (A·eV)^(1/2)')
        expected = math.sqrt(math.log10(2) * photon_energy_ev(self.app.wavelengths[0]))
        self.assertAlmostEqual(self.app.plot.values[0], expected)
        self.assertEqual(self.app.plot.values[1], 0)
        self.assertTrue(math.isnan(self.app.plot.values[2]))
        output = Path(self.temp_dir.name) / 'indirect-processing.txt'
        with mock.patch('tkinter.filedialog.asksaveasfilename', return_value=str(output)) as dialog:
            self.app.save_plot_curves()
        self.assertIn('Tauc_Indirect_1-2_nm', dialog.call_args[1]['initialfile'])
        text, rows = self.read_curve_txt(output)
        self.assertIn('# Formula: (-log10(sample / reference) * E_eV)^(1/2)', text)
        self.assert_curve_matches_export_precision(rows, tuple(zip(self.app.plot.wavelengths, self.app.plot.values)))
        self.app.mode_var.set(TAUC_DIRECT)
        self.app.refresh_plot()
        self.assertTrue(math.isfinite(self.app.plot.values[2]))

    def test_ab_indirect_tauc_ev_export_retains_pairing_and_invalid_count(self):
        directory = Path(self.temp_dir.name)
        sample, bg = directory / 'sample.csv', directory / 'bg.csv'
        sample.write_text('x,y\n400,50\n410,100\n420,150\n', encoding='utf-8')
        bg.write_text('x,y\n400,100\n410,100\n420,100\n', encoding='utf-8')
        service = self.app.historical_absorbance_service
        service.load_samples((sample,))
        service.load_reference(bg)
        self.app.ab_mode_var.set(TAUC_INDIRECT)
        self.app.ab_x_axis_var.set('光子能量 (eV)')
        self.select_workspace('AB吸收')
        self.assertTrue(math.isnan(self.app.plot.values[0]))
        self.assertEqual(self.app.plot.values[1], 0)
        self.assertAlmostEqual(self.app.plot.values[2], math.sqrt(math.log10(2) * photon_energy_ev(400)))
        self.assertIn('无效点共 1', self.app.ab_result_status_var.get())
        self.assertIn('负吸光度 1 点', self.app.ab_result_status_var.get())
        output = directory / 'indirect-ab.txt'
        with mock.patch('tkinter.filedialog.asksaveasfilename', return_value=str(output)) as dialog:
            self.app.save_plot_curves()
        self.assertIn('Tauc_Indirect_1-2_eV', dialog.call_args[1]['initialfile'])
        text, rows = self.read_curve_txt(output)
        self.assertIn('E_eV)^(1/2)', text)
        self.assertIn('# Y: Tauc (A·eV)^(1/2)', text)
        self.assert_curve_matches_export_precision(rows, tuple(zip(self.app.plot.wavelengths, self.app.plot.values)))

    def test_indirect_offset_preview_matches_main_plot(self):
        self.load_offset_test_group(automatic=True)
        self.app.ab_mode_var.set(TAUC_INDIRECT)
        self.app.set_ab_x_axis('光子能量 (eV)')
        self.app.open_ab_offset_dialog()
        dialog = self.app.ab_offset_dialog
        try:
            self.assertEqual(dialog.mode.get(), TAUC_INDIRECT)
            self.assertEqual(dialog.plot.y_label, self.app.plot.y_label)
            self.assertEqual(dialog.plot.wavelengths, self.app.plot.wavelengths)
            self.assertTrue(self.app.plot.series_values_equal(dialog.plot.values, self.app.plot.values))
            dialog.mode.set(TAUC_DIRECT)
            dialog.preview()
            self.assertEqual(dialog.plot.y_label, 'Tauc (A·eV)²')
        finally:
            dialog.close()

    def test_tauc_preferences_migrate_legacy_and_preserve_indirect(self):
        repository = JsonPreferencesRepository(Path(self.temp_dir.name) / 'tauc-settings.json')
        for saved_mode, expected in (('Tauc 计算结果', TAUC_DIRECT), (TAUC_INDIRECT, TAUC_INDIRECT)):
            repository.save({'processing_mode': saved_mode, 'ab_mode': saved_mode})
            other_root = tk.Toplevel(self.root)
            other_root.withdraw()
            other = None
            try:
                other = OceanLabApp(
                    other_root, Path(self.temp_dir.name) / 'missing-sdk',
                    service_factory=service_factory, sdk_validator=is_oceandirect_home,
                    sdk_normalizer=normalize_sdk_home, preferences_repository=repository,
                    spectrum_exporter=DelimitedSpectrumExporter(),
                    spectrum_importer=DelimitedSpectrumImporter(), simulation_factory=SimulatedSpectrometer)
                self.assertEqual(other.mode_var.get(), expected)
                self.assertEqual(other.ab_mode_var.get(), expected)
                other.save_preferences()
                self.assertEqual(repository.load()['processing_mode'], expected)
                self.assertEqual(repository.load()['ab_mode'], expected)
            finally:
                if other is not None:
                    other.closing = True
                    if other.worker.is_alive():
                        other.worker.send('shutdown')
                        other.worker.join(timeout=1.0)
                other_root.destroy()

    def test_pl_overlay_is_not_replaced_by_live_frames(self) -> None:
        first = Path(self.temp_dir.name) / "pl_a.csv"
        second = Path(self.temp_dir.name) / "pl_b.txt"
        first.write_text("wavelength,intensity\n400,10\n410,20\n420,15\n", encoding="utf-8")
        second.write_text("wavelength\tintensity\n400\t5\n410\t25\n420\t10\n", encoding="utf-8")
        result = self.app.pl_result_service.load_many((first, second))
        self.assertEqual(result.added, 2)
        self.app.pl_active_var.set(self.app.pl_result_service.active_id or "")
        self.app.rebuild_pl_file_list()
        self.assertEqual(self.app.pl_open_button.cget("style"), "Selected.History.TButton")
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

    def test_ab_negative_signals_do_not_flatten_tauc_and_cursor_explains_gap(self) -> None:
        directory = Path(self.temp_dir.name)
        grid = (195.76960873, 199.78711, 384.790395179, 390.281376387, 899.896980901)
        paths = []
        for name, values in (("sample", (-17.7125, 130.2875, 1290.1375, 1549.6875, 63046.6375)),
                             ("bg", (-8027.5625, -7755.1125, 344.2875, 1573.5875, 55037.2875))):
            path = directory / f"{name}.csv"
            path.write_text("wavelength,intensity\n" + "\n".join(
                f"{x},{y}" for x, y in zip(grid, values)), encoding="utf-8")
            paths.append(path)
        self.app.historical_absorbance_service.load_samples((paths[0],))
        self.app.historical_absorbance_service.load_reference(paths[1])
        self.app.ab_mode_var.set("Tauc 计算结果")
        self.app.ab_x_axis_var.set("光子能量 (eV)")
        self.select_workspace("AB吸收")
        self.assertTrue(all(math.isnan(value) for value in self.app.plot.values[-2:]))
        self.assertLess(self.app.plot._current_y_bounds[1], 5)
        self.assertIn("BG 非正值 2 点", self.app.ab_result_status_var.get())
        self.app.plot.set_cursor_nearest_x(photon_energy_ev(grid[1]))
        self.assertEqual(self.app.cursor_var.get(), "强度: 无效")
        self.app.plot.set_cursor_nearest_x(photon_energy_ev(grid[2]))
        self.assertEqual(self.app.cursor_var.get(), "强度: 3.42")

    def load_offset_test_group(self, automatic=False):
        self.app.historical_absorbance_service.automatic_offsets = automatic
        paths = []
        for name, values in (('offset-bg', (-8000,) * 24 + (10000,) * 20 + (55037.2875,) * 20 + (20000,) * 36),
                             ('sample-platform', (250,) * 24 + (20000,) * 20 + (63047,) * 20 + (10000,) * 36)):
            path = Path(self.temp_dir.name) / f'{name}.txt'
            path.write_text(f'节点{name}的数据\nSpectrometer: QEPB1727\n积分时间 (秒): 0.1\n'
                            'Scans to average: 20\n启用暗噪声校正: true\n>>>>>Begin Spectral Data<<<<<\n' +
                            '\n'.join(f'{200+i}\t{value}' for i, value in enumerate(values)), encoding='utf-8')
            paths.append(path)
        self.app.historical_absorbance_service.load_reference(paths[0])
        self.app.historical_absorbance_service.load_samples((paths[1],))
        self.app.ab_mode_var.set('Tauc 计算结果')
        self.select_workspace('AB吸收')
        return paths

    def test_automatic_offset_main_plot_and_simple_preview_match(self):
        self.load_offset_test_group(automatic=True)
        self.app.set_ab_x_axis('光子能量 (eV)')
        self.assertTrue(all(math.isfinite(value) for value in self.app.plot.values))
        self.assertIn('自动估算', self.app.ab_offset_status_var.get())
        self.app.open_ab_offset_dialog()
        dialog = self.app.ab_offset_dialog
        self.root.update_idletasks()
        self.assertEqual(dialog.state.get(), '已自动应用到主图')
        self.assertFalse(dialog.show_original.get())
        self.assertEqual(len(dialog.plot.series), 1)
        self.assertEqual(dialog.plot.wavelengths, self.app.plot.wavelengths)
        self.assertTrue(self.app.plot.series_values_equal(dialog.plot.values, self.app.plot.values))
        # Let Windows deliver the transient's asynchronous map/configure events.
        self.root.after(100, self.root.quit)
        with mock.patch('tkinter.messagebox.showwarning'):
            self.root.mainloop()
        self.assertGreaterEqual(dialog.plot.canvas.winfo_height(), 280)
        self.assertGreaterEqual(dialog.plot.canvas.winfo_width(), 420)
        self.assertLess(dialog.target_entry.winfo_rooty() + dialog.target_entry.winfo_height(),
                        dialog.window.winfo_rooty() + dialog.window.winfo_height())
        dialog.show_original.set(True)
        dialog.preview()
        self.assertEqual(len(dialog.plot.series), 2)
        dialog.close()
        self.assertTrue(self.app.historical_absorbance_service.current_group.offsets['BG'].automatic)

    def test_dirty_preview_is_cleared_and_restore_is_not_automatically_undone(self):
        bg, sample = self.load_offset_test_group(automatic=True)
        self.app.open_ab_offset_dialog()
        dialog = self.app.ab_offset_dialog
        dialog.target.set('nan')
        self.assertFalse(dialog.plot.values)
        dialog.preview_button.invoke()
        self.assertEqual(dialog.state.get(), '无法预览校正')
        self.assertFalse(dialog.plot.values)
        self.assertIn('自动估算', self.app.ab_offset_status_var.get())
        dialog.target.set('63047')
        dialog.preview_button.invoke()
        dialog.restore_button.invoke()
        self.app.set_ab_x_axis('光子能量 (eV)')
        self.app.refresh_plot()
        service = self.app.historical_absorbance_service
        service.load_samples((sample,))
        self.assertNotIn('BG', service.current_group.offsets)
        self.assertTrue(any(math.isnan(value) for value in self.app.plot.values))
        with mock.patch('tkinter.filedialog.askopenfilename', return_value=str(bg)):
            self.app.select_ab_reference_file()
        self.assertTrue(service.current_group.offsets['BG'].automatic)
        self.assertTrue(all(math.isfinite(value) for value in self.app.plot.values))

    def test_offset_dialog_cancel_apply_export_and_restore(self):
        bg, sample = self.load_offset_test_group()
        service = self.app.historical_absorbance_service
        self.assertIn('疑似整体偏移：BG', self.app.ab_offset_status_var.get())
        self.app.open_ab_offset_dialog()
        dialog = self.app.ab_offset_dialog
        self.root.update_idletasks()
        self.assertEqual(dialog.target.get(), '63047')
        self.assertIn('8009.7125', dialog.summary.get())
        self.assertTrue(dialog.plots[0].canvas.find_withtag('spectrum_line'))
        self.assertLessEqual(dialog.apply_button.winfo_rooty() + dialog.apply_button.winfo_height(),
                             dialog.window.winfo_rooty() + dialog.window.winfo_height())
        self.assertEqual(service.current_group.offsets, {})
        dialog.close()
        self.assertTrue(math.isnan(self.app.plot.values[0]))
        self.app.open_ab_offset_dialog()
        self.app.ab_offset_dialog.apply()
        self.assertIsNone(self.app.ab_offset_dialog)
        self.assertTrue(all(math.isfinite(value) for value in self.app.plot.values))
        self.assertIn('BG +8009.7', self.app.ab_offset_status_var.get())
        self.assertEqual(self.app.ab_reference_status_var.get(), bg.name)
        self.assertEqual(service.reference.raw[0], -8000)
        destination = Path(self.temp_dir.name) / 'corrected.txt'
        with mock.patch('tkinter.filedialog.asksaveasfilename', return_value=str(destination)):
            self.app.save_plot_curves()
        text, rows = self.read_curve_txt(destination)
        self.assertIn('BG offset counts', text)
        self.assertIn('8009.7125', text)
        self.assertIn('BG plateau suspect points', text)
        self.assertTrue(all(math.isfinite(y) for _x, y in rows))
        self.app.open_ab_offset_dialog()
        self.app.ab_offset_dialog.restore()
        self.assertEqual(service.current_group.offsets, {})
        self.assertTrue(math.isnan(self.app.plot.values[0]))

    def test_offset_dialog_invalid_baseline_cannot_apply_and_noise_sample_cannot_shift(self):
        self.load_offset_test_group()
        self.app.open_ab_offset_dialog()
        dialog = self.app.ab_offset_dialog
        dialog.target.set('nan')
        self.assertEqual(str(dialog.apply_button.cget('state')), 'disabled')
        dialog.apply()
        self.assertEqual(self.app.historical_absorbance_service.current_group.offsets, {})
        dialog.combo.current(1)
        dialog.select_signal()
        dialog.target.set('65535')
        dialog.apply()
        self.assertIn('不建议', dialog.summary.get())
        self.assertEqual(self.app.historical_absorbance_service.current_group.offsets, {})
        dialog.close()

    def test_historical_ab_sample_and_bg_display_absorbance(self) -> None:
        paths = []
        for name, values in (
            ("sample", (5, 15, 30)),
            ("reference", (10, 20, 40)),
        ):
            path = Path(self.temp_dir.name) / f"{name}.csv"
            path.write_text(
                "wavelength,intensity\n" + "\n".join(f"{350 + index},{value}" for index, value in enumerate(values)),
                encoding="utf-8",
            )
            paths.append(path)
        self.app.historical_absorbance_service.load_samples((paths[0],))
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(paths[1])):
            self.app.select_ab_reference_file()
        self.assertEqual(self.app.ab_reference_status_var.get(), paths[1].name)
        self.app.ab_active_var.set(self.app.historical_absorbance_service.active_id or "")
        self.app.rebuild_ab_file_list()
        self.assertEqual(self.app.ab_samples_button.cget("style"), "Selected.History.TButton")
        self.assertEqual(self.app.ab_reference_file_button.cget("style"), "Selected.History.TButton")
        self.select_workspace("AB吸收")
        self.assertEqual(self.app.plot.y_label, "吸光度 (A)")
        self.assertAlmostEqual(self.app.plot.values[0], 0.3010299957)
        self.assertIn("无效点共 0", self.app.ab_result_status_var.get())
        wavelength_pairs = list(zip(self.app.plot.wavelengths, self.app.plot.values))
        self.app.set_ab_x_axis("光子能量 (eV)")
        self.assertEqual(self.app.plot.x_label, "光子能量 (eV)")
        self.assertLess(self.app.plot.wavelengths[0], self.app.plot.wavelengths[-1])
        expected_energy_pairs = sorted((photon_energy_ev(x), y) for x, y in wavelength_pairs)
        self.assertEqual(len(self.app.plot.wavelengths), len(expected_energy_pairs))
        for actual, expected in zip(zip(self.app.plot.wavelengths, self.app.plot.values), expected_energy_pairs):
            self.assertAlmostEqual(actual[0], expected[0])
            self.assertAlmostEqual(actual[1], expected[1])
        self.assertEqual(self.app.cursor_unit_var.get(), "eV")
        self.app.ab_mode_var.set("Tauc 计算结果")
        self.app.on_ab_display_changed()
        self.assertEqual(self.app.plot.y_label, "Tauc (A·eV)²")
        self.assertAlmostEqual(self.app.plot.values[-1], (0.3010299957 * self.app.plot.wavelengths[-1]) ** 2)

    def test_ab_groups_keep_old_results_and_controls_scoped(self) -> None:
        directory = Path(self.temp_dir.name)
        paths = []
        for name, values in (("sample-one", (50, 40, 30)), ("bg-one", (100, 100, 100)),
                             ("sample-two", (100, 80, 60)), ("bg-two", (200, 200, 200))):
            path = directory / f"{name}.csv"
            path.write_text("wavelength,intensity\n" + "\n".join(
                f"{400 + index * 10},{value}" for index, value in enumerate(values)
            ), encoding="utf-8")
            paths.append(path)
        self.select_workspace("AB吸收")
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(paths[1])):
            self.app.select_ab_reference_file()
        with mock.patch("tkinter.filedialog.askopenfilenames", return_value=(str(paths[0]),)):
            self.app.select_ab_sample_files()
        first_values = self.app.plot.series[0].values
        first_id = self.app.historical_absorbance_service.entries[0].entry_id
        self.app.create_ab_group()
        self.assertEqual(self.app.ab_group_var.get(), "分析组 2")
        self.assertEqual(self.app.ab_reference_status_var.get(), "未选择")
        self.assertEqual(self.app.plot.series[0].values, first_values)
        with mock.patch("tkinter.filedialog.askopenfilenames", return_value=(str(paths[2]),)):
            self.app.select_ab_sample_files()
        self.assertIn("等待 BG", self.app.ab_group_widgets["ab-2"]["title"].get())
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(paths[3])):
            self.app.select_ab_reference_file()
        self.assertIn("BG 已选择", self.app.ab_group_widgets["ab-2"]["title"].get())
        self.assertEqual(len(self.app.plot.series), 2)
        self.assertEqual(self.app.plot.series[0].values, first_values)
        self.assertTrue(self.app.plot.series[1].active)
        self.app.set_ab_active(first_id)
        self.assertEqual(self.app.ab_group_var.get(), "分析组 1")
        self.assertEqual(self.app.ab_reference_status_var.get(), paths[1].name)
        self.assertEqual(self.app.ab_active_var.get(), first_id)
        self.app.ab_group_var.set("分析组 2")
        self.app.on_ab_group_changed()
        self.app.clear_ab_group()
        self.assertEqual(self.app.plot.series[0].values, first_values)
        self.assertEqual(self.app.ab_active_var.get(), first_id)

    def test_ab_duplicate_preserves_zoom_hidden_state_and_can_reload(self) -> None:
        service = self.app.historical_absorbance_service
        directory = Path(self.temp_dir.name)
        sample = directory / "sample.csv"
        bg = directory / "bg.csv"
        sample.write_text("x,y\n400,50\n410,40\n420,30\n", encoding="utf-8")
        bg.write_text("x,y\n400,100\n410,100\n420,100\n", encoding="utf-8")
        self.select_workspace("AB吸收")
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(bg)):
            self.app.select_ab_reference_file()
        with mock.patch("tkinter.filedialog.askopenfilenames", return_value=(str(sample),)):
            self.app.select_ab_sample_files()
        plot = self.app.plot
        plot.x_min, plot.x_max, plot.y_min, plot.y_max = 405, 415, 0.2, 0.8
        plot.draw()
        lines_before = plot.canvas.find_withtag("spectrum_line")
        rows_before = tuple(self.app.ab_list_frame.winfo_children())
        with mock.patch("tkinter.filedialog.askopenfilenames", return_value=(str(sample),)):
            self.app.select_ab_sample_files()
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(bg)):
            self.app.select_ab_reference_file()
        self.assertEqual((plot.x_min, plot.x_max, plot.y_min, plot.y_max), (405, 415, 0.2, 0.8))
        self.assertEqual(plot.canvas.find_withtag("spectrum_line"), lines_before)
        self.assertEqual(tuple(self.app.ab_list_frame.winfo_children()), rows_before)
        sample.write_text("x,y\n400,20\n410,20\n420,20\n", encoding="utf-8")
        self.app.reload_ab_samples()
        self.assertAlmostEqual(plot.values[0], -math.log10(0.2))
        entry_id = service.entries[0].entry_id
        self.app.ab_row_variables[entry_id]["visible"].set(False)
        self.app.set_ab_visible(entry_id, self.app.ab_row_variables[entry_id]["visible"])
        with mock.patch("tkinter.filedialog.askopenfilenames", return_value=(str(sample),)):
            self.app.select_ab_sample_files()
        self.assertEqual(self.app.ab_active_var.get(), "")
        self.assertFalse(service.entries[0].visible)

    def test_ab_rejected_reference_preserves_filename_and_canvas(self) -> None:
        directory = Path(self.temp_dir.name)
        sample, bg, bad = (directory / name for name in ("sample.csv", "bg.csv", "bad.csv"))
        sample.write_text("x,y\n400,50\n410,40\n420,30\n", encoding="utf-8")
        bg.write_text("x,y\n400,100\n410,100\n420,100\n", encoding="utf-8")
        bad.write_text("x,y\n500,200\n510,200\n520,200\n", encoding="utf-8")
        service = self.app.historical_absorbance_service
        service.load_samples((sample,))
        service.load_reference(bg)
        self.app.rebuild_ab_file_list()
        self.select_workspace("AB吸收")
        values = tuple(self.app.plot.values)
        lines = self.app.plot.canvas.find_withtag("spectrum_line")
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=str(bad)), \
                mock.patch("tkinter.messagebox.showerror") as error:
            self.app.select_ab_reference_file()
        self.assertTrue(error.called)
        self.assertEqual(self.app.ab_reference_status_var.get(), bg.name)
        self.assertEqual(tuple(self.app.plot.values), values)
        self.assertEqual(self.app.plot.canvas.find_withtag("spectrum_line"), lines)

    def test_plot_redraws_same_data_when_view_bounds_change(self) -> None:
        plot = self.app.plot
        series = [PlotSeries("sample", (400, 410, 420), (0.3, 0.4, 0.5), "#d13438", active=True)]
        plot.set_series(series, "吸光度 (A)")
        plot.x_min, plot.x_max, plot.y_min, plot.y_max = 405, 415, 0.2, 0.8
        plot.draw()
        plot.x_min = plot.x_max = plot.y_min = plot.y_max = None
        with mock.patch.object(plot, "draw", wraps=plot.draw) as draw:
            plot.set_series(series, "吸光度 (A)")
            self.assertEqual(draw.call_count, 1)
        labels = [plot.canvas.itemcget(item, "text") for item in plot.canvas.find_withtag("x_tick_label")]
        self.assertIn("400", labels)
        self.assertIn("420", labels)
        self.assertEqual(plot.current_x_bounds(), (400, 420))

    def test_ab_color_change_with_nan_gaps_keeps_canvas_geometry(self) -> None:
        service = self.app.historical_absorbance_service
        directory = Path(self.temp_dir.name)
        sample, bg = directory / "sample.csv", directory / "bg.csv"
        sample.write_text("x,y\n400,50\n410,40\n420,30\n430,20\n", encoding="utf-8")
        bg.write_text("x,y\n400,100\n410,100\n420,0\n430,100\n", encoding="utf-8")
        service.load_reference(bg)
        service.load_samples((sample,))
        self.app.rebuild_ab_file_list()
        self.select_workspace("AB吸收")
        lines = self.app.plot.canvas.find_withtag("spectrum_line")
        self.assertTrue(lines)
        service.set_color(service.entries[0].entry_id, "#123456")
        self.app.refresh_plot()
        self.assertEqual(self.app.plot.canvas.find_withtag("spectrum_line"), lines)
        self.assertEqual(self.app.plot.canvas.itemcget(lines[0], "fill"), "#123456")

    def test_history_names_are_read_only_and_use_most_of_row_width(self):
        directory = Path(self.temp_dir.name)
        name = 'D260807-S2-30um-18ms-20times-完整样品名称-' + '0123456789' * 7
        paths = [directory / (name + suffix + '.csv') for suffix in ('-01', '-02')]
        for path in paths:
            path.write_text('x,y\n400,50\n410,40\n420,30\n', encoding='utf-8')
        self.app.pl_result_service.load_many(paths)
        self.app.rebuild_pl_file_list()
        service = self.app.historical_absorbance_service
        service.load_samples(paths)
        bg = directory / 'bg.csv'
        bg.write_text('x,y\n400,100\n410,100\n420,100\n', encoding='utf-8')
        service.load_reference(bg)
        self.app.rebuild_ab_file_list()
        for kind, workspace, entries in (('pl', 'PL荧光', self.app.pl_result_service.entries),
                                          ('ab', 'AB吸收', service.entries)):
            self.select_workspace(workspace)
            with mock.patch('tkinter.messagebox.showwarning'):
                self.root.update()
            self.root.update_idletasks()
            rows = getattr(self.app, kind + '_row_variables')
            label = rows[entries[1].entry_id]['name_label']
            self.assertEqual(label.winfo_class(), 'TLabel')
            self.assertFalse(any(child.winfo_class() == 'TEntry' for child in label.master.winfo_children()))
            self.assertGreater(label.winfo_width(), label.master.winfo_width() * 0.6)
            self.assertLess(rows[entries[1].entry_id]['remove_button'].winfo_width(), label.master.winfo_width() * 0.17)
            self.assertTrue(label.cget('text').endswith('…'))
            self.assertEqual(rows[entries[1].entry_id]['name'].get(), entries[1].display_name)
            label.event_generate('<Button-1>')
            self.assertEqual(getattr(self.app, kind + '_active_var').get(), entries[1].entry_id)
            self.assertTrue(self.app.plot.series[1].active)
            self.assertTrue(label.bind('<MouseWheel>'))

    def test_history_icons_toggle_visibility_and_active_state_without_rebuilding_rows(self):
        directory = Path(self.temp_dir.name)
        paths = [directory / ('icon-sample-' + str(index) + '.csv') for index in range(2)]
        for path in paths:
            path.write_text('x,y\n400,50\n410,40\n420,30\n', encoding='utf-8')
        bg = directory / 'icon-bg.csv'
        bg.write_text('x,y\n400,100\n410,100\n420,100\n', encoding='utf-8')
        self.app.pl_result_service.load_many(paths)
        self.app.rebuild_pl_file_list()
        self.app.historical_absorbance_service.load_samples(paths)
        self.app.historical_absorbance_service.load_reference(bg)
        self.app.rebuild_ab_file_list()
        for kind, workspace, service in (('pl', 'PL荧光', self.app.pl_result_service),
                                         ('ab', 'AB吸收', self.app.historical_absorbance_service)):
            self.select_workspace(workspace)
            rows = getattr(self.app, kind + '_row_variables')
            first, second = service.entries
            row = rows[first.entry_id]
            eye, target = row['visible_button'], row['active_button']
            image = str(eye.image)
            eye.invoke()
            self.assertFalse(service.entries[0].visible)
            self.assertFalse(row['visible'].get())
            self.assertNotEqual(str(eye.image), image)
            target.invoke()
            self.assertTrue(service.entries[0].visible)
            self.assertTrue(row['visible'].get())
            self.assertEqual(getattr(self.app, kind + '_active_var').get(), first.entry_id)
            selected_image = str(target.image)
            rows[second.entry_id]['active_button'].invoke()
            self.assertNotEqual(str(target.image), selected_image)
            self.assertIs(getattr(self.app, kind + '_row_variables')[first.entry_id]['active_button'], target)
            self.assertTrue(eye.bind('<MouseWheel>'))
            self.assertTrue(target.bind('<space>'))
            self.assertTrue(target.bind('<Return>'))

    def test_history_palette_updates_color_and_turbo_blocks_manual_action(self):
        path = Path(self.temp_dir.name) / 'palette-sample.csv'
        path.write_text('x,y\n400,50\n410,40\n420,30\n', encoding='utf-8')
        self.app.pl_result_service.load_many((path,))
        self.app.rebuild_pl_file_list()
        entry = self.app.pl_result_service.entries[0]
        palette = self.app.pl_row_variables[entry.entry_id]['color_button']
        original_image = str(palette.image)
        with mock.patch('tkinter.colorchooser.askcolor', return_value=((18, 52, 86), '#123456')) as choose:
            palette.invoke()
            self.assertEqual(self.app.pl_result_service.entries[0].color, '#123456')
            self.assertEqual(palette.cget('foreground'), '#123456')
            self.assertNotEqual(str(palette.image), original_image)
            self.assertEqual(choose.call_count, 1)
            self.app.pl_turbo_var.set(True)
            self.app.update_pl_row_colors()
            self.assertEqual(palette.cget('state'), 'disabled')
            palette.invoke()
            self.assertEqual(choose.call_count, 1)
            self.app.pl_turbo_var.set(False)
            self.app.update_pl_row_colors()
            self.assertEqual(palette.cget('foreground'), '#123456')
            self.assertEqual(palette.cget('state'), 'normal')
        self.app.pl_row_variables[entry.entry_id]['remove_button'].invoke()
        self.assertEqual(self.app.pl_result_service.entries, [])

    def test_history_name_hover_shows_full_text_and_cancels_on_leave_and_destroy(self):
        path = Path(self.temp_dir.name) / ('very-long-sample-name-' * 4 + '.csv')
        path.write_text('x,y\n400,50\n410,40\n420,30\n', encoding='utf-8')
        self.app.pl_result_service.load_many((path,))
        self.app.rebuild_pl_file_list()
        self.app.historical_absorbance_service.load_samples((path,))
        self.app.rebuild_ab_file_list()
        for kind, workspace in (('pl', 'PL荧光'), ('ab', 'AB吸收')):
            self.select_workspace(workspace)
            with mock.patch('tkinter.messagebox.showwarning'):
                self.root.update()
            label = next(iter(getattr(self.app, kind + '_row_variables').values()))['name_label']
            state = label._oceanlab_tooltip
            with mock.patch.object(self.root, 'after', wraps=self.root.after) as schedule:
                label.event_generate('<Enter>')
                self.assertTrue(any(call[0][0] == 600 for call in schedule.call_args_list))
            self.assertIsNone(state['window'])
            self.root.after(650, self.root.quit)
            with mock.patch('tkinter.messagebox.showwarning'):
                self.root.mainloop()
            self.assertIsNotNone(state['window'])
            tip_label = state['window'].winfo_children()[0]
            self.assertEqual(tip_label.cget('text'), path.name)
            self.assertNotIn('\n', tip_label.cget('text'))
            self.assertEqual(int(tip_label.cget('wraplength')), label.winfo_screenwidth() - 24)
            label.event_generate('<Leave>')
            self.assertIsNone(state['window'])
            self.assertIsNone(state['timer'])
            label.event_generate('<Enter>')
            self.assertIsNotNone(state['timer'])
            label.destroy()
            self.assertIsNone(state['timer'])
            self.assertIsNone(state['window'])

    def test_compact_history_controls_keep_bg_name_and_buttons_inside_panel(self):
        self.select_workspace('AB吸收')
        with mock.patch('tkinter.messagebox.showwarning'):
            self.root.update()
        filename = 'D260807-BG-30um-18ms-20times_QEPB17271_20-35-11-491.txt'
        self.app.ab_reference_status_var.set(filename)
        original_scale = float(self.root.tk.call('tk', 'scaling'))
        try:
            for scale in (1.0, 1.5, 2.0):
                self.root.tk.call('tk', 'scaling', scale)
                self.app.ab_reference_name_label.refresh()
                self.root.update_idletasks()
                label = self.app.ab_reference_name_label
                clear = self.app.ab_reference_clear_button
                self.assertEqual(self.app.ab_reference_status_var.get(), filename)
                self.assertGreater(label.winfo_width(), clear.winfo_width() * 2)
                for widget in (clear, self.app.ab_mode_combo, self.app.ab_reload_button,
                               self.app.ab_group_combo, self.app.ab_energy_axis_button):
                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                         widget.master.winfo_rootx() + widget.master.winfo_width())
        finally:
            self.root.tk.call('tk', 'scaling', original_scale)

    def test_workspace_display_preferences_persist_without_file_paths(self) -> None:
        self.select_workspace("PL荧光")
        self.app.mode_var.set("峰值归一化")
        self.app.pl_normalize_var.set(True)
        self.app.on_pl_display_changed()
        self.app.pl_turbo_var.set(True)
        self.app.ab_mode_var.set("透过率 (%)")
        self.app.ab_x_axis_var.set("光子能量 (eV)")
        self.app.save_preferences()
        saved = self.app.load_preferences()
        self.assertEqual(saved["workspace"], "PL荧光")
        self.assertEqual(saved["processing_mode"], "峰值归一化")
        self.assertTrue(saved["pl_normalize"])
        self.assertTrue(saved["pl_turbo"])
        self.assertEqual(saved["ab_mode"], "透过率 (%)")
        self.assertEqual(saved["ab_x_axis"], "光子能量 (eV)")
        self.assertNotIn("pl_files", saved)

    def test_old_dark_subtraction_preference_falls_back_to_raw(self) -> None:
        repository = JsonPreferencesRepository(Path(self.temp_dir.name) / "legacy-settings.json")
        repository.save({"processing_mode": "暗谱扣除", "workspace": "处理"})
        other_root = tk.Toplevel(self.root)
        other_root.withdraw()
        other = None
        try:
            other = OceanLabApp(
                other_root, Path(self.temp_dir.name) / "missing-sdk",
                service_factory=service_factory,
                sdk_validator=is_oceandirect_home,
                sdk_normalizer=normalize_sdk_home,
                preferences_repository=repository,
                spectrum_exporter=DelimitedSpectrumExporter(),
                spectrum_importer=DelimitedSpectrumImporter(),
                simulation_factory=SimulatedSpectrometer,
            )
            other_root.update_idletasks()
            self.assertEqual(other.mode_var.get(), "原始强度")
            self.assertNotIn("暗谱扣除", other.mode_combo.cget("values"))
            other.enter_simulation_mode()
            self.assertEqual(other.plot.values, other.raw_spectrum)
            self.assertIsNone(other.spectrum_service.baselines.dark)
        finally:
            if other is not None:
                other.closing = True
                if other.worker.is_alive():
                    other.worker.send("shutdown")
                    other.worker.join(timeout=1.0)
            other_root.destroy()


if __name__ == "__main__":
    unittest.main()
