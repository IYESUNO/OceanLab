from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..domain.result_processing import calculate_absorbance, calculate_tauc_direct, wavelength_axes_match
from ..domain.results import AbsorbanceDataset, ImportedSpectrum, PlotSeries
from ..ports.spectrum_importer import SpectrumImporter
from .pl_results import ImportBatchResult, PlResultService


@dataclass(frozen=True)
class AbsorbancePlotResult:
    series: tuple[PlotSeries, ...]
    skipped: tuple[tuple[str, str], ...]
    invalid_counts: tuple[tuple[str, int], ...]


class HistoricalAbsorbanceService:
    def __init__(self, importer: SpectrumImporter):
        self.importer = importer
        self.sample_results = PlResultService(importer)
        self.reference: ImportedSpectrum | None = None
        self.dark: ImportedSpectrum | None = None

    @property
    def entries(self):
        return self.sample_results.entries

    @property
    def active_id(self) -> str | None:
        return self.sample_results.active_id

    def load_samples(self, paths: Iterable[Path]) -> ImportBatchResult:
        return self.sample_results.load_many(paths)

    def load_reference(self, path: Path) -> ImportedSpectrum:
        self.reference = self.importer.load(Path(path))
        return self.reference

    def load_dark(self, path: Path) -> ImportedSpectrum:
        self.dark = self.importer.load(Path(path))
        return self.dark

    def clear_reference(self) -> None:
        self.reference = None

    def clear_dark(self) -> None:
        self.dark = None

    def set_active(self, entry_id: str) -> None:
        self.sample_results.set_active(entry_id)

    def set_visible(self, entry_id: str, visible: bool) -> None:
        self.sample_results.set_visible(entry_id, visible)

    def rename(self, entry_id: str, display_name: str) -> None:
        self.sample_results.rename(entry_id, display_name)

    def set_color(self, entry_id: str, color: str) -> None:
        self.sample_results.set_color(entry_id, color)

    def remove(self, entry_id: str) -> None:
        self.sample_results.remove(entry_id)

    def clear(self) -> None:
        self.sample_results.clear()
        self.reference = None
        self.dark = None

    def build_plot(self, absorbance: bool, tauc: bool = False) -> AbsorbancePlotResult:
        series: list[PlotSeries] = []
        skipped: list[tuple[str, str]] = []
        invalid_counts: list[tuple[str, int]] = []
        if self.reference is None:
            return AbsorbancePlotResult((), (("AB", "尚未选择 BG 参考"),), ())
        for entry in self.entries:
            if not entry.visible:
                continue
            sample = entry.spectrum
            if not wavelength_axes_match(sample.wavelengths, self.reference.wavelengths):
                skipped.append((entry.display_name, "与 BG 参考的波长网格不一致"))
                continue
            if self.dark is not None and not wavelength_axes_match(sample.wavelengths, self.dark.wavelengths):
                skipped.append((entry.display_name, "与可选暗谱的波长网格不一致"))
                continue
            dark_values = self.dark.raw if self.dark is not None else (0.0,) * len(sample.raw)
            dataset = AbsorbanceDataset(
                name=entry.display_name,
                wavelengths=sample.wavelengths,
                sample=sample.raw,
                dark=dark_values,
                reference=self.reference.raw,
                source_paths=tuple(
                    path
                    for path in (sample.source_path, self.reference.source_path, self.dark.source_path if self.dark else None)
                    if path is not None
                ),
            )
            values, invalid = (
                calculate_tauc_direct(dataset)
                if tauc
                else calculate_absorbance(dataset, absorbance)
            )
            invalid_counts.append((entry.display_name, invalid))
            series.append(PlotSeries(
                name=entry.display_name,
                wavelengths=sample.wavelengths,
                values=values,
                color=entry.color,
                active=entry.entry_id == self.active_id,
            ))
        return AbsorbancePlotResult(tuple(series), tuple(skipped), tuple(invalid_counts))
