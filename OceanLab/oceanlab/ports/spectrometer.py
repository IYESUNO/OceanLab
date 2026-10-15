from __future__ import annotations

from ._typing import Protocol

from ..domain.models import AcquisitionRequest, ConnectedDevice, SpectrumContext, SpectrumFrame


class SpectrometerInterrupted(RuntimeError):
    """A pending operation was explicitly cancelled by the user."""


class SpectrometerPort(Protocol):
    def connect(self) -> ConnectedDevice: ...

    def disconnect(self) -> None: ...

    def check_connection(self) -> bool: ...

    def apply_configuration(self, request: AcquisitionRequest) -> SpectrumContext: ...

    def acquire(self, context: SpectrumContext) -> SpectrumFrame | None: ...

    def shutdown(self) -> None: ...
