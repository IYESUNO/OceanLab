from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImportedSpectrum:
    name: str
    source_path: Path
    wavelengths: tuple[float, ...]
    raw: tuple[float, ...]
    dark: tuple[float, ...] | None = None
    reference: tuple[float, ...] | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def metadata_dict(self) -> dict[str, str]:
        return dict(self.metadata)


@dataclass(frozen=True)
class AbsorbanceDataset:
    name: str
    wavelengths: tuple[float, ...]
    sample: tuple[float, ...]
    dark: tuple[float, ...]
    reference: tuple[float, ...]
    source_paths: tuple[Path, ...]


@dataclass(frozen=True)
class PlotSeries:
    name: str
    wavelengths: tuple[float, ...]
    values: tuple[float, ...]
    color: str
    visible: bool = True
    active: bool = False
    dash: tuple[int, ...] | None = None
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PlResultEntry:
    entry_id: str
    spectrum: ImportedSpectrum
    display_name: str
    color: str
    visible: bool = True


@dataclass(frozen=True)
class AbsorbanceSampleEntry:
    entry_id: str
    group_id: str
    spectrum: ImportedSpectrum
    display_name: str
    color: str
    visible: bool = True
