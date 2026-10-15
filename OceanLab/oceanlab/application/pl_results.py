from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from ..domain.result_processing import normalize_peak, subtract_dark, wavelength_axes_match
from ..domain.results import ImportedSpectrum, PlotSeries, PlResultEntry
from ..ports.spectrum_importer import SpectrumImporter


@dataclass(frozen=True)
class ImportBatchResult:
    added: int
    duplicates: int
    errors: tuple[tuple[Path, str], ...]


@dataclass(frozen=True)
class PlotBuildResult:
    series: tuple[PlotSeries, ...]
    skipped: tuple[tuple[str, str], ...]


def turbo_color(position: float) -> str:
    """Return a color from Google's dependency-free Turbo approximation."""
    x = min(1.0, max(0.0, float(position)))
    coefficients = (
        (0.13572138, 4.61539260, -42.66032258, 132.13108234, -152.94239396, 59.28637943),
        (0.09140261, 2.19418839, 4.84296658, -14.18503333, 4.27729857, 2.82956604),
        (0.10667330, 12.64194608, -60.58204836, 110.36276771, -89.90310912, 27.34824973),
    )
    channels = []
    for channel in coefficients:
        value = sum(coefficient * (x ** power) for power, coefficient in enumerate(channel))
        channels.append(round(min(1.0, max(0.0, value)) * 255))
    return "#{:02x}{:02x}{:02x}".format(*channels)


class PlResultService:
    COLORS = (
        "#d13438",
        "#0f6cbd",
        "#107c10",
        "#8764b8",
        "#ca5010",
        "#038387",
        "#c239b3",
        "#498205",
    )

    def __init__(self, importer: SpectrumImporter):
        self.importer = importer
        self.entries: list[PlResultEntry] = []
        self.active_id: str | None = None
        self.background: ImportedSpectrum | None = None

    def load_many(self, paths: Iterable[Path]) -> ImportBatchResult:
        known = {entry.entry_id for entry in self.entries}
        added = 0
        duplicates = 0
        errors: list[tuple[Path, str]] = []
        for raw_path in paths:
            path = Path(raw_path).resolve()
            entry_id = str(path).casefold()
            if entry_id in known:
                duplicates += 1
                self.active_id = entry_id
                continue
            try:
                spectrum = self.importer.load(path)
            except (OSError, ValueError) as exc:
                errors.append((path, str(exc)))
                continue
            entry = PlResultEntry(
                entry_id=entry_id,
                spectrum=spectrum,
                display_name=spectrum.name,
                color=self.COLORS[len(self.entries) % len(self.COLORS)],
            )
            self.entries.append(entry)
            known.add(entry_id)
            self.active_id = entry_id
            added += 1
        return ImportBatchResult(added, duplicates, tuple(errors))

    def set_active(self, entry_id: str) -> None:
        if any(entry.entry_id == entry_id for entry in self.entries):
            self.active_id = entry_id
            self._replace(entry_id, visible=True)

    def set_visible(self, entry_id: str, visible: bool) -> None:
        self._replace(entry_id, visible=bool(visible))
        if not visible and self.active_id == entry_id:
            replacement = next((entry for entry in self.entries if entry.visible and entry.entry_id != entry_id), None)
            self.active_id = replacement.entry_id if replacement is not None else None

    def rename(self, entry_id: str, display_name: str) -> None:
        name = display_name.strip()
        if not name:
            raise ValueError("曲线名称不能为空。")
        self._replace(entry_id, display_name=name)

    def set_color(self, entry_id: str, color: str) -> None:
        self._replace(entry_id, color=color)

    def remove(self, entry_id: str) -> None:
        self.entries = [entry for entry in self.entries if entry.entry_id != entry_id]
        if self.active_id == entry_id:
            replacement = next((entry for entry in self.entries if entry.visible), self.entries[-1] if self.entries else None)
            self.active_id = replacement.entry_id if replacement is not None else None

    def clear(self) -> None:
        self.entries.clear()
        self.active_id = None
        self.background = None

    def load_background(self, path: Path) -> ImportedSpectrum:
        self.background = self.importer.load(Path(path))
        return self.background

    def clear_background(self) -> None:
        self.background = None

    def build_plot(self, normalize: bool, turbo: bool = False) -> PlotBuildResult:
        series: list[PlotSeries] = []
        skipped: list[tuple[str, str]] = []
        turbo_enabled = bool(turbo)
        turbo_denominator = max(1, len(self.entries) - 1)
        for index, entry in enumerate(self.entries):
            if not entry.visible:
                continue
            values = entry.spectrum.raw
            if self.background is not None:
                if not wavelength_axes_match(entry.spectrum.wavelengths, self.background.wavelengths):
                    skipped.append((entry.display_name, "与 BG 的波长网格不一致"))
                    continue
                values = subtract_dark(values, self.background.raw)
            if normalize:
                try:
                    values = normalize_peak(values)
                except ValueError as exc:
                    skipped.append((entry.display_name, str(exc)))
                    continue
            series.append(PlotSeries(
                name=entry.display_name,
                wavelengths=entry.spectrum.wavelengths,
                values=tuple(values),
                color=turbo_color(1.0 - index / turbo_denominator) if turbo_enabled else entry.color,
                active=entry.entry_id == self.active_id,
                metadata=entry.spectrum.metadata + (
                    ("Source file", str(entry.spectrum.source_path)),
                    ("BG", str(self.background.source_path) if self.background else "None"),
                    ("Peak normalization", str(normalize)),
                    ("Formula", "(sample - BG) / max(sample - BG)" if normalize and self.background else
                     "sample / max(sample)" if normalize else "sample - BG" if self.background else "sample"),
                ),
            ))
        return PlotBuildResult(tuple(series), tuple(skipped))

    def _replace(self, entry_id: str, **changes) -> None:
        self.entries = [replace(entry, **changes) if entry.entry_id == entry_id else entry for entry in self.entries]
