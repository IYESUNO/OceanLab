from __future__ import annotations

from pathlib import Path
from ._typing import Protocol

from ..domain.results import AbsorbanceDataset, ImportedSpectrum


class SpectrumImporter(Protocol):
    def load(self, path: Path) -> ImportedSpectrum: ...

    def load_absorbance(self, path: Path) -> AbsorbanceDataset: ...

    def load_absorbance_triplet(
        self,
        sample_path: Path,
        dark_path: Path,
        reference_path: Path,
    ) -> AbsorbanceDataset: ...
