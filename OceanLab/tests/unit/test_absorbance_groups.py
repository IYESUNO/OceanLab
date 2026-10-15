from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from oceanlab.adapters.spectrum_files import DelimitedSpectrumImporter
from oceanlab.application.absorbance import HistoricalAbsorbanceService
from oceanlab.domain.signal_offset import assess_signal


class AbsorbanceGroupsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.service = HistoricalAbsorbanceService(DelimitedSpectrumImporter(), automatic_offsets=False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def spectrum(self, name, values, grid=(400, 410, 420)):
        path = self.directory / f"{name}.csv"
        path.write_text("wavelength,intensity\n" + "\n".join(
            f"{x},{y}" for x, y in zip(grid, values)
        ), encoding="utf-8")
        return path

    def first_group(self):
        sample = self.spectrum("sample", (50, 40, 30))
        reference = self.spectrum("bg", (100, 100, 100))
        self.service.load_reference(reference)
        self.service.load_samples((sample,))
        return sample, reference

    def test_group_visibility_preserves_individual_choices_and_other_group_data(self):
        self.first_group()
        service = self.service
        first = service.current_group
        service.load_samples((self.spectrum('hidden', (20, 30, 40)),))
        service.set_visible(first.entries[1].entry_id, False)
        service.create_group()
        service.load_reference(self.spectrum('bg2', (200, 200, 200)))
        service.load_samples((self.spectrum('sample2', (10, 20, 30)),))
        second_curve = service.build_plot(True).series[1]
        service.set_active(first.entries[0].entry_id)
        service.set_group_visible(first.group_id, False)
        result = service.build_plot(True)
        self.assertEqual(len(result.series), 1)
        self.assertEqual(result.series[0].values, second_curve.values)
        self.assertNotEqual(service.active_id, first.entries[0].entry_id)
        self.assertEqual([entry.visible for entry in first.entries], [True, False])
        service.select_group(first.group_id)
        active_before_preview = service.active_id
        self.assertEqual(len(service.build_plot(True, correction_overrides={}).series), 2)
        self.assertEqual(service.active_id, active_before_preview)
        self.assertFalse(first.visible)
        service.set_group_visible(first.group_id, True)
        self.assertEqual(len(service.build_plot(True).series), 2)
        self.assertEqual([entry.visible for entry in first.entries], [True, False])
        service.set_group_visible(first.group_id, False)
        service.set_active(first.entries[0].entry_id)
        self.assertTrue(first.visible)
        self.assertEqual(service.active_id, first.entries[0].entry_id)

    def test_remove_group_keeps_other_reference_and_replaces_last_with_empty_group(self):
        paths = self.first_group()
        before = {path: path.read_bytes() for path in paths}
        service = self.service
        first = service.current_group
        second = service.create_group()
        service.load_reference(self.spectrum('bg2', (200, 200, 200)))
        service.load_samples((self.spectrum('sample2', (50, 40, 30)),))
        second_values = service.build_plot(True).series[1].values
        service.select_group(first.group_id)
        service.remove_group(first.group_id)
        self.assertIs(service.current_group, second)
        self.assertEqual(service.build_plot(True).series[0].values, second_values)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        service.remove_group(second.group_id)
        self.assertEqual(len(service.groups), 1)
        self.assertIsNone(service.active_id)
        self.assertIsNone(service.reference)
        self.assertEqual(service.entries, [])
        self.assertEqual(service.current_group.name, "分析组 1")
        blank = service.current_group
        for _ in range(8):
            service.remove_group(blank.group_id)
            self.assertIs(service.current_group, blank)
        self.assertFalse(service.can_remove_group(blank.group_id))
        self.assertEqual(service.create_group().name, "分析组 2")

    def test_indirect_tauc_rejects_negative_absorbance_and_selects_renderable_curve(self):
        self.service.load_reference(self.spectrum('bg', (100, 100, 100)))
        self.service.load_samples((self.spectrum('above-bg', (200, 200, 200)),
                                   self.spectrum('mixed', (50, 100, 150))))
        first, second = self.service.entries
        self.service.active_id = first.entry_id
        result = self.service.build_plot(True, tauc=True, tauc_exponent=0.5)
        self.assertEqual([count for _name, count in result.invalid_counts], [3, 1])
        self.assertEqual(self.service.active_id, second.entry_id)
        self.assertTrue(result.series[1].active)
        self.assertEqual(result.series[1].values[1], 0)
        self.assertIn('1/2 次方无实数结果', result.signal_warnings[0][1])
        self.assertTrue(dict(result.series[1].metadata)['Formula'].endswith('^(1/2)'))
        direct = self.service.build_plot(True, tauc=True)
        self.assertEqual([count for _name, count in direct.invalid_counts], [0, 0])

    def offset_fixture(self, name, values, integration='0.1'):
        path = self.directory / f'{name}.txt'
        path.write_text(f'节点{name}的数据\nSpectrometer: QEPB1727\n积分时间 (秒): {integration}\n'
                        'Scans to average: 20\n启用暗噪声校正: true\n'
                        '>>>>>Begin Spectral Data<<<<<\n' + '\n'.join(
                            f'{200+i},{value}'.replace(',', '\t') for i, value in enumerate(values)), encoding='utf-8')
        return path

    def offset_group(self):
        bg = self.offset_fixture('shifted-bg', (-8000,) * 24 + (10000,) * 20 + (55037.2875,) * 20 + (20000,) * 36)
        sample = self.offset_fixture('sample-platform', (250,) * 24 + (20000,) * 20 + (63047,) * 20 + (10000,) * 36)
        self.service.load_reference(bg)
        self.service.load_samples((sample,))
        return bg, sample

    def test_automatic_offset_applies_for_either_import_order(self):
        self.service = HistoricalAbsorbanceService(DelimitedSpectrumImporter())
        bg, sample = self.offset_group()
        for reference_first in (False, True):
            with self.subTest(reference_first=reference_first):
                self.service.clear()
                if reference_first:
                    self.service.load_reference(bg)
                    self.assertFalse(self.service.current_group.offsets)
                    self.service.load_samples((sample,))
                else:
                    self.service.load_samples((sample,))
                    self.service.load_reference(bg)
                correction = self.service.current_group.offsets['BG']
                self.assertTrue(correction.automatic)
                self.assertTrue(correction.estimated)
                self.assertAlmostEqual(correction.delta, 8009.7125)
                self.assertEqual(self.service.build_plot(True, True).invalid_counts[0][1], 0)
                self.assertEqual(self.service.reference.raw[0], -8000)

    def test_automatic_restore_stays_restored_until_signal_reload(self):
        self.service.automatic_offsets = True
        bg, sample = self.offset_group()
        self.assertIn('BG', self.service.current_group.offsets)
        self.service.set_offset('BG', None)
        self.service.build_plot(True)
        self.service.apply_automatic_offsets()
        self.service.load_samples((sample,))
        self.assertNotIn('BG', self.service.current_group.offsets)
        self.assertEqual(self.service.build_plot(True).invalid_counts[0][1], 24)
        self.service.load_reference(bg)
        self.assertTrue(self.service.current_group.offsets['BG'].automatic)

    def test_automatic_offset_waits_for_matching_baseline_and_keeps_other_group(self):
        self.service.automatic_offsets = True
        bg, sample = self.offset_group()
        first_id = self.service.current_group_id
        first_result = self.service.build_plot(True).series[0].values
        first_correction = self.service.current_group.offsets['BG']
        self.service.create_group()
        self.service.load_reference(bg)
        other = self.offset_fixture('different-integration', (250,) * 24 + (20000,) * 20 + (63047,) * 20 + (10000,) * 36, '0.015')
        self.service.load_samples((other,))
        self.assertNotIn('BG', self.service.current_group.offsets)
        self.service.load_samples((sample,))
        self.assertTrue(self.service.current_group.offsets['BG'].automatic)
        self.assertEqual(self.service.build_plot(True).series[0].values, first_result)
        self.service.select_group(first_id)
        self.assertEqual(self.service.current_group.offsets['BG'], first_correction)

    def test_offset_detection_ignores_edge_noise_and_requires_plateau(self):
        self.assertFalse(assess_signal((-5,) * 4 + (3000,) * 80).suspected_offset)
        self.assertFalse(assess_signal((-8000,) * 40 + tuple(range(1000, 3000))).suspected_offset)
        self.offset_group()
        info, target, source = self.service.offset_suggestion('BG')
        self.assertTrue(info.suspected_offset)
        self.assertEqual(target, 63047)
        self.assertIn('估算', source)

    def test_offset_preview_apply_restore_preserve_original_and_other_groups(self):
        self.offset_group()
        original = self.service.reference.raw
        old = self.service.build_plot(True, tauc=True)
        active = self.service.active_id
        first_group = self.service.current_group_id
        info, target, source = self.service.offset_suggestion('BG')
        correction = self.service.make_offset('BG', target, source, True)
        self.assertAlmostEqual(correction.delta, 8009.7125)
        preview = self.service.build_plot(True, True, {'BG': correction})
        self.assertEqual(preview.invalid_counts[0][1], 0)
        self.assertEqual(old.invalid_counts[0][1], 24)
        self.assertEqual(self.service.current_group.offsets, {})
        self.assertEqual(self.service.active_id, active)
        self.service.set_offset('BG', correction)
        self.assertEqual(self.service.reference.raw, original)
        metadata = dict(self.service.build_plot(True).series[0].metadata)
        self.assertAlmostEqual(float(metadata['BG offset counts']), correction.delta)
        self.assertEqual(metadata['BG offset estimated'], 'True')
        self.service.create_group()
        self.first_group()
        second_before = self.service.build_plot(True).series[-1].values
        self.service.select_group(first_group)
        self.service.set_offset('BG', None)
        self.assertEqual(self.service.build_plot(True).series[-1].values, second_before)
        self.assertEqual(self.service.build_plot(True).invalid_counts[0][1], 24)

    def test_offset_baseline_requires_matching_metadata_and_rejects_invalid_targets(self):
        self.offset_group()
        replacement = self.offset_fixture('different-time', (250,) * 24 + (20000,) * 20 + (63047,) * 20 + (10000,) * 36, '0.015')
        self.service.remove(self.service.entries[0].entry_id)
        self.service.load_samples((replacement,))
        self.assertIsNone(self.service.offset_suggestion('BG')[1])
        for target in (float('nan'), float('inf'), 0, 55037):
            with self.assertRaises(ValueError):
                self.service.make_offset('BG', target, 'manual', False)
        with self.assertRaises(ValueError):
            self.service.make_offset(self.service.entries[0].entry_id, 64000, 'manual', False)

    def test_reload_resets_only_changed_signal_offset_and_failure_preserves_it(self):
        bg, sample = self.offset_group()
        correction = self.service.make_offset('BG', 63047, 'manual', False)
        self.service.set_offset('BG', correction)
        self.service.load_samples((sample,), reload=True)
        self.assertEqual(self.service.current_group.offsets['BG'], correction)
        with self.assertRaises(OSError):
            self.service.load_reference(self.directory / 'missing.txt')
        self.assertEqual(self.service.current_group.offsets['BG'], correction)
        self.service.load_reference(bg)
        self.assertNotIn('BG', self.service.current_group.offsets)

    def test_sample_offset_is_independent_and_duplicate_import_keeps_it(self):
        bg, sample = self.offset_group()
        # Use the shifted signal as sample, and the clean plateau as reference.
        self.service.clear()
        self.service.load_reference(sample)
        self.service.load_samples((bg,))
        entry = self.service.entries[0]
        correction = self.service.make_offset(entry.entry_id, 63047, 'manual', False)
        self.service.set_offset(entry.entry_id, correction)
        self.service.load_samples((bg,))
        self.assertEqual(self.service.current_group.offsets[entry.entry_id], correction)
        metadata = dict(self.service.build_plot(True).series[0].metadata)
        self.assertEqual(metadata['BG offset counts'], '0')
        self.assertAlmostEqual(float(metadata['Sample offset counts']), correction.delta)
        self.service.load_samples((bg,), reload=True)
        self.assertNotIn(entry.entry_id, self.service.current_group.offsets)

    def test_second_group_and_reference_replacement_do_not_change_first(self):
        self.first_group()
        first = self.service.build_plot(True).series[0].values
        group_two = self.service.create_group()
        self.assertIsNone(self.service.reference)
        self.service.load_samples((self.spectrum("second", (100, 80, 60)),))
        waiting = self.service.build_plot(True)
        self.assertEqual(waiting.series[0].values, first)
        self.assertIn("尚未选择 BG", waiting.skipped[0][1])
        self.service.load_reference(self.spectrum("bg-two", (200, 200, 200)))
        result = self.service.build_plot(True)
        self.assertEqual(result.series[0].values, first)
        self.assertAlmostEqual(result.series[1].values[0], -math.log10(100 / 200))
        self.service.load_reference(self.spectrum("bg-three", (400, 400, 400)))
        updated = self.service.build_plot(True)
        self.assertEqual(updated.series[0].values, first)
        self.assertAlmostEqual(updated.series[1].values[0], -math.log10(100 / 400))
        self.service.clear_current_group()
        self.assertEqual(self.service.current_group_id, group_two.group_id)
        self.assertEqual(self.service.build_plot(True).series[0].values, first)

    def test_same_path_can_belong_to_two_groups_with_different_references(self):
        sample, _ = self.first_group()
        first_id = self.service.entries[0].entry_id
        first = self.service.build_plot(True).series[0].values
        self.service.create_group()
        self.service.load_reference(self.spectrum("other-bg", (200, 200, 200),))
        self.assertEqual(self.service.load_samples((sample,)).added, 1)
        self.assertNotEqual(self.service.entries[1].entry_id, first_id)
        self.assertEqual(self.service.build_plot(True).series[0].values, first)
        self.assertAlmostEqual(self.service.build_plot(True).series[1].values[0], -math.log10(0.25))

    def test_failed_reference_and_reload_preserve_previous_data(self):
        sample, _ = self.first_group()
        reference_before = self.service.reference
        values_before = self.service.build_plot(True).series[0].values
        bad = self.spectrum("bad", (200, 200, 200), (500, 510, 520))
        with self.assertRaisesRegex(ValueError, "波长网格"):
            self.service.load_reference(bad)
        with self.assertRaises(OSError):
            self.service.load_reference(self.directory / "missing.csv")
        self.assertIs(self.service.reference, reference_before)
        self.spectrum("sample", (10, 10, 10), (500, 510, 520))
        failed = self.service.load_samples((sample,), reload=True)
        self.assertEqual(len(failed.errors), 1)
        self.assertEqual(self.service.build_plot(True).series[0].values, values_before)
        added = self.service.load_samples((bad,))
        self.assertEqual((added.added, len(added.errors)), (0, 1))

    def test_duplicate_and_reload_preserve_visibility_identity_and_styles(self):
        sample, _ = self.first_group()
        entry_id = self.service.entries[0].entry_id
        self.service.rename(entry_id, "renamed")
        self.service.set_color(entry_id, "#123456")
        self.service.set_visible(entry_id, False)
        self.service.build_plot(True)
        self.spectrum("sample", (20, 20, 20))
        duplicate = self.service.load_samples((sample,))
        self.assertEqual((duplicate.added, duplicate.duplicates), (0, 1))
        self.assertIsNone(self.service.active_id)
        self.assertEqual(self.service.entries[0].spectrum.raw, (50, 40, 30))
        self.service.load_samples((sample,), reload=True)
        entry = self.service.entries[0]
        self.assertEqual((entry.entry_id, entry.display_name, entry.color, entry.visible),
                         (entry_id, "renamed", "#123456", False))
        self.assertEqual(entry.spectrum.raw, (20, 20, 20))
        self.service.set_active(entry_id)
        self.assertAlmostEqual(self.service.build_plot(True).series[0].values[0], -math.log10(0.2))

    def test_active_falls_back_to_valid_curve_when_group_loses_reference(self):
        self.first_group()
        first_id = self.service.entries[0].entry_id
        self.service.create_group()
        self.service.load_reference(self.spectrum("bg-two", (200, 200, 200)))
        self.service.load_samples((self.spectrum("second", (100, 100, 100)),))
        self.service.build_plot(True)
        self.service.clear_reference()
        result = self.service.build_plot(True)
        self.assertEqual(self.service.active_id, first_id)
        self.assertTrue(result.series[0].active)
        self.service.set_visible(first_id, False)
        self.assertFalse(self.service.build_plot(True).series)
        self.assertIsNone(self.service.active_id)

    def test_different_groups_accept_different_grids_and_keep_nan_counts(self):
        self.first_group()
        self.service.create_group()
        self.service.load_reference(self.spectrum("other-bg", (0, 0, 0), (500, 510, 520)))
        self.service.load_samples((self.spectrum("other-sample", (1, 2, 3), (500, 510, 520)),))
        result = self.service.build_plot(True)
        self.assertEqual(result.invalid_counts[-1][1], 3)
        self.assertTrue(all(math.isnan(value) for value in result.series[-1].values))
        self.assertTrue(result.series[0].active)
        self.assertFalse(result.series[-1].active)
        self.assertIn("全部计算点无效", result.skipped[-1][1])

    def test_negative_bg_and_sample_cannot_create_false_tauc_peak(self):
        # Representative points from the reported measurement, plus a valid
        # sample/BG > 1 point whose negative absorbance must remain usable.
        grid = (195.76960873, 199.78711, 384.790395179, 390.281376387, 899.896980901)
        self.service.load_samples((self.spectrum("sample", (-17.7125, 130.2875, 1290.1375, 1549.6875, 63046.6375), grid),))
        self.service.load_reference(self.spectrum("bg", (-8027.5625, -7755.1125, 344.2875, 1573.5875, 55037.2875), grid))
        for absorbance, tauc in ((False, False), (True, False), (True, True)):
            with self.subTest(absorbance=absorbance, tauc=tauc):
                result = self.service.build_plot(absorbance, tauc=tauc)
                values = result.series[0].values
                self.assertTrue(all(math.isnan(value) for value in values[:2]))
                self.assertTrue(all(math.isfinite(value) for value in values[2:]))
                self.assertEqual(result.invalid_counts[0][1], 2)
                self.assertIn("BG 非正值 2 点", result.signal_warnings[0][1])
                if tauc:
                    self.assertLess(max(value for value in values if math.isfinite(value)), 4)
                elif absorbance:
                    self.assertLess(values[-1], 0)
                else:
                    self.assertGreater(values[-1], 100)


if __name__ == "__main__":
    unittest.main()
