from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from oceanlab.adapters.filesystem import DelimitedSpectrumExporter
from oceanlab.adapters.spectrum_files import DelimitedSpectrumImporter
from oceanlab.application.absorbance import HistoricalAbsorbanceService
from oceanlab.application.pl_results import PlResultService, turbo_color
from oceanlab.domain.models import BaselineSet, SpectrumContext, SpectrumFrame
from oceanlab.domain.result_processing import PHOTON_ENERGY_EV_NM
from oceanlab.ports.exporter import ExportRequest


def context() -> SpectrumContext:
    return SpectrumContext("test", "S1", 10_000, 1, 0, 0, False, False, False, 3, 350.0, 352.0)


class SpectrumImportAndResultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        self.importer = DelimitedSpectrumImporter()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_two_column(
        self,
        name: str,
        values: tuple[float, ...] = (10.0, 20.0, 30.0),
        separator: str = ",",
        encoding: str = "utf-8",
    ) -> Path:
        path = self.directory / name
        lines = [separator.join(("wavelength_nm", "intensity"))]
        lines.extend(separator.join((str(350 + index), str(value))) for index, value in enumerate(values))
        path.write_bytes(("\n".join(lines) + "\n").encode(encoding))
        return path

    def write_oceanlab(self, name: str = "complete.csv", base_name: str | None = "样品基本名") -> Path:
        path = self.directory / name
        frame = SpectrumFrame((6.0, 11.0, 21.0), 1.0, context())
        dark = SpectrumFrame((1.0, 1.0, 1.0), 1.0, context())
        reference = SpectrumFrame((11.0, 21.0, 41.0), 1.0, context())
        metadata = {"model": "FAKE", "sample": "样品"}
        if base_name is not None:
            metadata["base_name"] = base_name
        DelimitedSpectrumExporter().export(ExportRequest(
            path=path,
            frame=frame,
            wavelengths=(350.0, 351.0, 352.0),
            processed=frame.values,
            processed_label="强度 (counts)",
            baselines=BaselineSet(dark, reference),
            metadata=metadata,
        ))
        return path

    def test_imports_oceanlab_with_baselines_and_metadata(self) -> None:
        spectrum = self.importer.load(self.write_oceanlab())
        self.assertEqual(spectrum.raw, (6.0, 11.0, 21.0))
        self.assertEqual(spectrum.dark, (1.0, 1.0, 1.0))
        self.assertEqual(spectrum.reference, (11.0, 21.0, 41.0))
        self.assertEqual(spectrum.metadata_dict()["sample"], "样品")
        self.assertEqual(spectrum.name, "样品基本名")

    def test_imports_oceanview_gb18030_two_column_format_and_node_name(self) -> None:
        path = self.directory / "renamed_file.txt"
        text = "\r\n".join((
            "节点KCaCl3Eu-d0000GPa-L360nm-1s-1_TESTSERIAL1_21-41-02-058.txt的数据",
            "",
            "Date: Sun Jul 12 21:41:02 CST 2026",
            "Spectrometer: TESTSERIAL",
            "Number of Pixels in Spectrum: 3",
            ">>>>>Begin Spectral Data<<<<<",
            "350\t10.5",
            "351\t20.5",
            "352\t30.5",
        )) + "\r\n"
        path.write_bytes(text.encode("gb18030"))
        spectrum = self.importer.load(path)
        self.assertEqual(spectrum.name, "KCaCl3Eu-d0000GPa-L360nm-1s-1")
        self.assertEqual(spectrum.wavelengths, (350.0, 351.0, 352.0))
        self.assertEqual(spectrum.raw, (10.5, 20.5, 30.5))
        self.assertEqual(spectrum.metadata_dict()["Spectrometer"], "TESTSERIAL")

    def test_legacy_oceanlab_filename_recovers_basic_name(self) -> None:
        path = self.write_oceanlab("sample_name_20260712_214102_058.csv", base_name=None)
        self.assertEqual(self.importer.load(path).name, "sample_name")

    def test_imports_generic_delimiters_and_gb18030(self) -> None:
        tabbed = self.importer.load(self.write_two_column("tab.txt", separator="\t"))
        semicolon = self.importer.load(self.write_two_column("semicolon.txt", separator=";"))
        chinese = self.importer.load(self.write_two_column("中文.csv", encoding="gb18030"))
        spaced = self.directory / "space.txt"
        spaced.write_text("# comment\nwavelength intensity\n350 1\n351 2\n", encoding="utf-8")
        self.assertEqual(tabbed.wavelengths, (350.0, 351.0, 352.0))
        self.assertEqual(semicolon.raw[1], 20.0)
        self.assertEqual(chinese.raw[-1], 30.0)
        self.assertEqual(self.importer.load(spaced).raw, (1.0, 2.0))

    def test_rejects_unknown_columns_and_non_increasing_axis(self) -> None:
        many = self.directory / "many.csv"
        many.write_text("x,y,z\n1,2,3\n2,3,4\n", encoding="utf-8")
        repeated = self.directory / "repeat.csv"
        repeated.write_text("x,y\n350,1\n350,2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "恰好"):
            self.importer.load(many)
        with self.assertRaisesRegex(ValueError, "严格递增"):
            self.importer.load(repeated)

    def test_pl_service_overlays_deduplicates_and_uses_optional_background(self) -> None:
        complete = self.write_oceanlab()
        generic = self.write_two_column("generic.csv")
        background = self.write_two_column("background.csv", (1.0, 2.0, 3.0))
        service = PlResultService(self.importer)
        result = service.load_many((complete, generic, complete))
        self.assertEqual((result.added, result.duplicates), (2, 1))
        raw = service.build_plot(normalize=False)
        self.assertEqual(len(raw.series), 2)
        self.assertEqual(raw.series[0].values, (6.0, 11.0, 21.0))
        service.load_background(background)
        subtracted = service.build_plot(normalize=False)
        self.assertEqual(len(subtracted.series), 2)
        self.assertEqual(subtracted.series[0].values, (5.0, 9.0, 18.0))
        self.assertEqual(subtracted.series[1].values, (9.0, 18.0, 27.0))
        normalized = service.build_plot(normalize=True)
        self.assertAlmostEqual(normalized.series[0].values[-1], 1.0)
        gradient = service.build_plot(normalize=True, turbo=True)
        self.assertEqual(gradient.series[0].color, turbo_color(1.0))
        self.assertEqual(gradient.series[-1].color, turbo_color(0.0))
        self.assertNotEqual(gradient.series[0].color, gradient.series[-1].color)
        raw_gradient = service.build_plot(normalize=False, turbo=True)
        self.assertEqual(raw_gradient.series[0].color, turbo_color(1.0))
        self.assertEqual(raw_gradient.series[-1].color, turbo_color(0.0))
        self.assertEqual(
            [series.color for series in service.build_plot(normalize=False, turbo=False).series],
            [entry.color for entry in service.entries],
        )
        active = service.active_id
        service.rename(active, "活动样品")
        service.set_color(active, "#123456")
        service.set_visible(active, False)
        self.assertNotEqual(service.active_id, active)
        self.assertEqual(len(service.build_plot(False).series), 1)
        service.set_active(active)
        active_series = next(series for series in service.build_plot(False).series if series.active)
        self.assertEqual((active_series.name, active_series.color, active_series.active), ("活动样品", "#123456", True))

        mismatched = self.directory / "mismatched-bg.csv"
        mismatched.write_text("x,y\n350,1\n351.5,2\n352,3\n", encoding="utf-8")
        service.load_background(mismatched)
        skipped = service.build_plot(False)
        self.assertFalse(skipped.series)
        self.assertEqual(len(skipped.skipped), 2)
        service.clear_background()
        self.assertEqual(len(service.build_plot(False).series), 2)

    def test_pl_service_skips_zero_peak_normalization(self) -> None:
        service = PlResultService(self.importer)
        service.load_many((self.write_two_column("zero.csv", (0.0, 0.0, 0.0)),))
        result = service.build_plot(normalize=True)
        self.assertFalse(result.series)
        self.assertIn("正峰值", result.skipped[0][1])

    def test_absorbance_multiple_samples_with_optional_dark(self) -> None:
        service = HistoricalAbsorbanceService(self.importer)
        sample = self.write_two_column("sample.csv", (6.0, 11.0, 21.0))
        sample_two = self.write_two_column("sample-two.csv", (21.0, 41.0, 81.0))
        dark = self.write_two_column("dark.csv", (1.0, 1.0, 1.0))
        reference = self.write_two_column("reference.csv", (11.0, 21.0, 41.0))
        service.load_samples((sample, sample_two))
        service.load_reference(reference)
        without_dark = service.build_plot(absorbance=False)
        self.assertEqual(len(without_dark.series), 2)
        self.assertAlmostEqual(without_dark.series[0].values[0], 6.0 / 11.0 * 100.0)
        service.load_dark(dark)
        transmission = service.build_plot(absorbance=False)
        absorbance = service.build_plot(absorbance=True)
        self.assertEqual(transmission.series[0].values, (50.0, 50.0, 50.0))
        self.assertEqual(transmission.invalid_counts[0][1], 0)
        self.assertAlmostEqual(absorbance.series[0].values[0], 0.3010299957)
        tauc = service.build_plot(absorbance=True, tauc=True)
        expected_energy = PHOTON_ENERGY_EV_NM / 350.0
        self.assertAlmostEqual(tauc.series[0].values[0], (0.3010299957 * expected_energy) ** 2)
        self.assertEqual(tauc.invalid_counts[0][1], 0)
        negative_absorbance = -math.log10(2.0)
        self.assertAlmostEqual(tauc.series[1].values[0], (negative_absorbance * expected_energy) ** 2)
        self.assertEqual(tauc.invalid_counts[1][1], 0)
        self.assertTrue(all(math.isfinite(value) for value in tauc.series[1].values))
        service.set_active(service.entries[0].entry_id)
        self.assertTrue(service.build_plot(True).series[0].active)
        service.clear_dark()
        self.assertAlmostEqual(service.build_plot(False).series[0].values[0], 6.0 / 11.0 * 100.0)

    def test_absorbance_rejects_grid_mismatch_and_counts_invalid_points(self) -> None:
        sample = self.write_two_column("sample.csv", (1.0, 2.0, 3.0))
        dark = self.write_two_column("dark.csv", (1.0, 1.0, 1.0))
        reference = self.directory / "reference.csv"
        reference.write_text("x,y\n350,2\n351.5,2\n352,2\n", encoding="utf-8")
        service = HistoricalAbsorbanceService(self.importer)
        service.load_samples((sample,))
        service.load_reference(reference)
        mismatch = service.build_plot(absorbance=False)
        self.assertFalse(mismatch.series)
        self.assertIn("波长网格", mismatch.skipped[0][1])

        zero_sample = self.write_two_column("zero-sample.csv", (1.0, 1.0, 1.0))
        zero_dark = self.write_two_column("zero-dark.csv", (1.0, 1.0, 1.0))
        valid_reference = self.write_two_column("valid-reference.csv", (1.0, 2.0, 3.0))
        service.clear()
        service.load_samples((zero_sample,))
        service.load_reference(valid_reference)
        service.load_dark(zero_dark)
        result = service.build_plot(absorbance=True)
        self.assertEqual(result.invalid_counts[0][1], 3)
        self.assertTrue(all(math.isnan(value) for value in result.series[0].values))


if __name__ == "__main__":
    unittest.main()
