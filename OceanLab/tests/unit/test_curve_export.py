import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oceanlab.adapters.curve_txt import CURVE_DATA_MARKER, CurveTxtExporter
from oceanlab.adapters.spectrum_files import DelimitedSpectrumImporter
from oceanlab.adapters.filesystem import DelimitedSpectrumExporter
from oceanlab.domain.models import BaselineSet, SpectrumContext, SpectrumFrame
from oceanlab.domain.results import PlotSeries
from oceanlab.ports.exporter import CurveExportRequest, ExportRequest


class CurveExportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "结果.txt"
        self.exporter = CurveTxtExporter()

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, values=(0.012345678901234567, float("nan"), -0.9876543210987654)):
        curve = PlotSeries("样品\n名称", (2.100123456789012, 2.2, 2.3), values, "#ff0000", metadata=(("BG", "本组BG.txt"),))
        return CurveExportRequest(self.path, curve, "光子能量 (eV)", "Tauc (A·eV)²", (("BG", "其他组BG.txt"),))

    def test_transformed_values_use_bounded_rounding_with_nan_and_units(self):
        request = self.request()
        result = self.exporter.export(request)
        self.assertGreater(result.size, 0)
        text = self.path.read_text(encoding="gb18030")
        self.assertIn("# X: 光子能量 (eV)", text)
        self.assertIn("# Y: Tauc (A·eV)²", text)
        self.assertIn("# Curve: 样品 名称", text)
        self.assertIn("# BG: 本组BG.txt", text)
        self.assertIn("# Invalid points: 1", text)
        lines = text.splitlines()
        rows = [line.split("\t") for line in lines[lines.index(CURVE_DATA_MARKER) + 1:]]
        self.assertNotIn("x\ty", lines)
        self.assertEqual(rows, [["2.10012345679", "0.012345679"], ["2.2", "NaN"], ["2.3", "-0.98765432"]])
        self.assertEqual(len(rows), 3)
        for row, x, y in zip(rows, request.curve.wavelengths, request.curve.values):
            self.assertEqual(len(row), 2)
            self.assertTrue(math.isclose(float(row[0]), x, rel_tol=5.1e-12))
            if math.isnan(y):
                self.assertEqual(row[1], "NaN")
            else:
                self.assertTrue(math.isclose(float(row[1]), y, rel_tol=5.1e-8))
        with self.assertRaisesRegex(ValueError, "曲线结果"):
            DelimitedSpectrumImporter().load(self.path)

    def test_formats_preserve_weak_nonzero_results_without_fixed_decimal_cutoffs(self):
        cases = (
            ("强度 (counts)", 12345.6789, "12345.679"),
            ("归一化强度", 0.123456789, "0.12345679"),
            ("透过率 (%)", 65.4321987, "65.432199"),
            ("吸光度 (A)", 0.123456789, "0.12345679"),
            ("吸光度 (A)", 0.0000149, "1.49e-05"),
            ("吸光度 (A)", -1e-8, "-1e-08"),
            ("Tauc (A·eV)²", 1.23456789e-12, "1.2345679e-12"),
        )
        for label, value, expected in cases:
            with self.subTest(label=label, value=value):
                curve = PlotSeries("sample", (400.123456, 401.234567), (value, 0.0), "#ff0000")
                self.exporter.export(CurveExportRequest(self.path, curve, "波长 (nm)", label))
                text = self.path.read_text(encoding="gb18030")
                rows = text.split(CURVE_DATA_MARKER + "\n")[1].splitlines()
                self.assertEqual(rows, ["400.123456\t" + expected, "401.234567\t0"])

    def test_result_rounding_error_is_bounded_across_sign_and_magnitude(self):
        values = tuple(sign * mantissa * 10.0 ** exponent
                       for exponent in range(-300, 301, 10)
                       for mantissa in (1.000000049, 1.000000051, 1.23456789, 9.99999995)
                       for sign in (-1, 1))
        curve = PlotSeries("range", tuple(range(len(values))), values, "#ff0000")
        self.exporter.export(CurveExportRequest(self.path, curve, "像素索引", "Tauc (A·eV)²"))
        lines = self.path.read_text(encoding="gb18030").splitlines()
        rows = lines[lines.index(CURVE_DATA_MARKER) + 1:]
        for index, (line, value) in enumerate(zip(rows, values)):
            x, y = map(float, line.split("\t"))
            self.assertEqual(x, index)
            self.assertNotEqual(y, 0)
            self.assertTrue(math.isclose(y, value, rel_tol=5.1e-8), (value, y))

    def test_small_absorbance_export_does_not_distort_subsequent_tauc(self):
        absorbance = (0.00001491234567, -0.00001491234567)
        energy = (2.100123456789012, 2.2)
        curve = PlotSeries("weak absorption", energy, absorbance, "#ff0000")
        self.exporter.export(CurveExportRequest(self.path, curve, "光子能量 (eV)", "吸光度 (A)"))
        lines = self.path.read_text(encoding="gb18030").splitlines()
        rows = lines[lines.index(CURVE_DATA_MARKER) + 1:]
        for line, x, a in zip(rows, energy, absorbance):
            saved_x, saved_a = map(float, line.split("\t"))
            self.assertTrue(math.isclose((saved_x * saved_a) ** 2, (x * a) ** 2, rel_tol=1.1e-7))

    def test_axis_precision_increases_only_when_rounding_would_merge_points(self):
        axis = (400.0000001, 400.0000002, 400.0000003)
        curve = PlotSeries("sample", axis, (1.0, 2.0, 3.0), "#ff0000")
        self.exporter.export(CurveExportRequest(self.path, curve, "波长 (nm)", "强度 (counts)"))
        text = self.path.read_text(encoding="gb18030")
        self.assertIn("# X precision: 12 significant digits", text)
        rows = text.split(CURVE_DATA_MARKER + "\n")[1].splitlines()
        self.assertEqual(tuple(float(row.split("\t")[0]) for row in rows), axis)

    def test_close_axis_rounding_respects_spacing_not_only_unique_points(self):
        axis = (400.123456789012, 400.123456799012, 400.123456809012)
        curve = PlotSeries("close grid", axis, (1, 2, 3), "#ff0000")
        self.exporter.export(CurveExportRequest(self.path, curve, "波长 (nm)", "Counts"))
        lines = self.path.read_text(encoding="gb18030").splitlines()
        rows = lines[lines.index(CURVE_DATA_MARKER) + 1:]
        rounded = tuple(float(row.split("\t")[0]) for row in rows)
        step = min(b - a for a, b in zip(axis, axis[1:]))
        self.assertTrue(all(abs(a - b) <= step * 1e-6 for a, b in zip(axis, rounded)))
        self.assertTrue(all(b > a for a, b in zip(rounded, rounded[1:])))

    def test_raw_acquisition_txt_retains_original_precision_for_reprocessing(self):
        context = SpectrumContext("test", "S1", 1000, 3, 0, 0, False, False, False, 2, 400.0, 402.0)
        frame = SpectrumFrame((123.456789, 2.3456789), 1.0, context)
        DelimitedSpectrumExporter().export(ExportRequest(
            self.path, frame, (400.123456, 401.234567), frame.values,
            "强度 (counts)", BaselineSet(), {},
        ))
        text = self.path.read_text(encoding="gb18030")
        rows = text.split(CURVE_DATA_MARKER + "\n")[1].splitlines()
        self.assertEqual(rows, ["400.123456\t123.456789", "401.234567\t2.3456789"])
        imported = DelimitedSpectrumImporter().load(self.path)
        self.assertEqual(imported.wavelengths, (400.123456, 401.234567))
        self.assertEqual(imported.raw, (123.456789, 2.3456789))

    def test_validation_failure_preserves_existing_file(self):
        self.path.write_bytes(b"existing data")
        with self.assertRaises(ValueError):
            self.exporter.export(self.request((1.0, 2.0)))
        self.assertEqual(self.path.read_bytes(), b"existing data")
        with mock.patch.object(self.exporter, "_verify", side_effect=ValueError("verification failed")):
            with self.assertRaises(ValueError):
                self.exporter.export(self.request())
        self.assertEqual(self.path.read_bytes(), b"existing data")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
