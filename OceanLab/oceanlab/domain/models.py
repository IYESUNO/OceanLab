from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum, IntEnum, auto
from typing import Any


class TriggerMode(IntEnum):
    NORMAL = 0
    SOFTWARE = 1
    SYNCHRONIZATION = 2
    EXTERNAL_HARDWARE = 3


class AcquisitionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    IDLE = auto()
    CONFIGURING = auto()
    ACQUIRING = auto()
    ERROR = auto()
    SHUTTING_DOWN = auto()


@dataclass(frozen=True)
class DeviceCapabilities:
    electric_dark: bool = False
    nonlinearity: bool = False
    trigger: bool = False
    light_source: bool = False


@dataclass(frozen=True)
class DeviceInfo:
    device_id: int
    serial: str
    model: str
    pixels: int
    wavelength_min: float
    wavelength_max: float
    integration_min_us: int
    integration_max_us: int
    integration_increment_us: int
    max_intensity: float
    capabilities: DeviceCapabilities = DeviceCapabilities()
    # Device-reported saturation threshold. QE65000 uses the ADC maximum,
    # matching OceanView; a corrected plateau is not an ADC calibration.
    saturation_intensity: float | None = None


@dataclass(frozen=True)
class AcquisitionRequest:
    integration_us: int
    averages: int
    boxcar: int
    interval_ms: int
    trigger_mode: int = TriggerMode.NORMAL
    electric_dark: bool = False
    nonlinearity: bool = False
    light_source: bool = False
    apply_electric_dark: bool = False
    apply_nonlinearity: bool = False
    apply_trigger: bool = False
    apply_light_source: bool = False


@dataclass(frozen=True)
class SpectrumContext:
    source: str
    serial: str
    integration_us: int
    averages: int
    boxcar: int
    trigger_mode: int
    electric_dark: bool
    nonlinearity: bool
    light_source: bool
    pixel_count: int
    wavelength_min: float
    wavelength_max: float

    def compatible_with(self, other: SpectrumContext | None) -> bool:
        return other is not None and self == other

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConnectedDevice:
    info: DeviceInfo
    wavelengths: tuple[float, ...]
    settings: AcquisitionRequest


@dataclass(frozen=True)
class SpectrumFrame:
    values: tuple[float, ...]
    timestamp: float
    context: SpectrumContext


@dataclass(frozen=True)
class BaselineSet:
    dark: SpectrumFrame | None = None
    reference: SpectrumFrame | None = None

    def compatible_with(self, context: SpectrumContext) -> bool:
        return all(
            frame is None or frame.context.compatible_with(context)
            for frame in (self.dark, self.reference)
        )
