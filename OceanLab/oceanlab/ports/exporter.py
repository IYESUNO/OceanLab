from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ._typing import Protocol

from ..domain.models import BaselineSet, SpectrumFrame
from ..domain.results import PlotSeries


@dataclass(frozen=True)
class ExportRequest:
    path: Path
    frame: SpectrumFrame
    wavelengths: Sequence[float]
    processed: Sequence[float]
    processed_label: str
    baselines: BaselineSet
    metadata: dict[str, str]


@dataclass(frozen=True)
class ExportResult:
    path: Path
    size: int


@dataclass(frozen=True)
class CurveExportRequest:
    path: Path
    curve: PlotSeries
    x_label: str
    y_label: str
    metadata: tuple[tuple[str, str], ...] = ()


class SpectrumExporter(Protocol):
    def export(self, request: ExportRequest) -> ExportResult: ...

    def verify(self, result: ExportResult, expected_rows: int) -> None: ...

    def export_curve(self, request: CurveExportRequest) -> ExportResult: ...
