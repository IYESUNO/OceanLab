from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oceanlab.adapters.filesystem import DelimitedSpectrumExporter, JsonPreferencesRepository
from oceanlab.adapters.spectrum_files import DelimitedSpectrumImporter
from oceanlab.application.spectrum_service import SpectrumService
from oceanlab.domain.models import BaselineSet, SpectrumContext, SpectrumFrame
from oceanlab.domain.processing import ProcessingMode
from oceanlab.ports.exporter import ExportRequest


def context(integration_us: int = 10_000) -> SpectrumContext:
    return SpectrumContext("test", "S1", integration_us, 1, 0, 0, False, False, False, 3, 350.0, 352.0)


class ProcessingAndStorageTest(unittest.TestCase):
    def test_dark_transmittance_and_absorbance(self) -> None:
        service = SpectrumService()
        service.accept(SpectrumFrame((1.0, 1.0, 1.0), 1.0, context()))
        service.capture_dark()
        service.accept(SpectrumFrame((11.0, 21.0, 41.0), 2.0, context()))
        service.capture_reference()
        service.accept(SpectrumFrame((6.0, 11.0, 21.0), 3.0, context()))
        transmission, _ = service.processed(ProcessingMode.TRANSMITTANCE)
        absorbance, _ = service.processed(ProcessingMode.ABSORBANCE)
        self.assertEqual(transmission, [50.0, 50.0, 50.0])
        self.assertAlmostEqual(absorbance[0], 0.3010299957)

    def test_context_change_invalidates_baselines(self) -> None:
        service = SpectrumService()
        service.accept(SpectrumFrame((1.0, 1.0, 1.0), 1.0, context()))
        service.capture_dark()
        cleared = service.accept(SpectrumFrame((2.0, 2.0, 2.0), 2.0, context(20_000)))
        self.assertEqual(cleared, ("暗谱",))
        self.assertIsNone(service.baselines.dark)

    def test_preferences_round_trip_is_atomic_and_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            repository = JsonPreferencesRepository(path)
            expected = {"base_name": "样品", "averages": "3"}
            repository.save(expected)
            self.assertEqual(repository.load(), expected)
            self.assertFalse(any(path.parent.glob("*.tmp")))

    def test_export_preserves_header_and_data_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spectrum.csv"
            frame = SpectrumFrame((10.0, 20.0, 30.0), 1.0, context())
            exporter = DelimitedSpectrumExporter()
            result = exporter.export(ExportRequest(
                path=path,
                frame=frame,
                wavelengths=(350.0, 351.0, 352.0),
                processed=frame.values,
                processed_label="强度 (counts)",
                baselines=BaselineSet(),
                metadata={"model": "FAKE", "serial": "S1"},
            ))
            self.assertGreater(result.size, 0)
            text = path.read_text(encoding="utf-8-sig")
            self.assertTrue(text.startswith("# OceanLab spectrum export"))
            self.assertIn("wavelength_nm,raw_counts,dark_counts,reference_counts,强度 (counts)", text)

    def test_txt_export_matches_oceanview_header_and_two_column_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "样品基本名_TESTSERIAL1_00-00-01-000.txt"
            frame = SpectrumFrame((10.5, 20.5, 30.5), 1.0, context(1_000_000))
            exporter = DelimitedSpectrumExporter()
            result = exporter.export(ExportRequest(
                path=path,
                frame=frame,
                wavelengths=(350.0, 351.0, 352.0),
                processed=(0.0, 0.0, 0.0),
                processed_label="不应写入",
                baselines=BaselineSet(),
                metadata={"base_name": "样品基本名", "serial": "TESTSERIAL", "user": "不应导出"},
            ))
            self.assertGreater(result.size, 0)
            text = path.read_text(encoding="gb18030")
            self.assertTrue(text.startswith(f"节点{path.name}的数据"))
            self.assertIn("User: 360", text)
            self.assertNotIn("不应导出", text)
            self.assertIn("积分时间 (秒): 1.000000E0", text)
            self.assertIn(">>>>>Begin Spectral Data<<<<<\n350\t10.5\n351\t20.5", text.replace("\r\n", "\n"))
            self.assertNotIn("raw_counts", text)
            imported = DelimitedSpectrumImporter().load(path)
            self.assertEqual(imported.name, "样品基本名")
            self.assertEqual(imported.raw, frame.values)


if __name__ == "__main__":
    unittest.main()
