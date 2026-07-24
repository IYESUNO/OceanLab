from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ._typing import Protocol

from ..domain.models import BaselineSet, SpectrumFrame


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


class SpectrumExporter(Protocol):
    def export(self, request: ExportRequest) -> ExportResult: ...

    def verify(self, result: ExportResult, expected_rows: int) -> None: ...
