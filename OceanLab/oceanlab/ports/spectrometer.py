from __future__ import annotations

from typing import Protocol

from ..domain.models import AcquisitionRequest, ConnectedDevice, SpectrumContext, SpectrumFrame


class SpectrometerPort(Protocol):
    def connect(self) -> ConnectedDevice: ...

    def disconnect(self) -> None: ...

    def apply_configuration(self, request: AcquisitionRequest) -> SpectrumContext: ...

    def acquire(self, context: SpectrumContext) -> SpectrumFrame: ...

    def shutdown(self) -> None: ...
