from __future__ import annotations

import math
import time

from ..domain.models import (
    AcquisitionRequest,
    ConnectedDevice,
    DeviceInfo,
    SpectrumContext,
    SpectrumFrame,
)


class SimulatedSpectrometer:
    def __init__(self) -> None:
        self.frame_index = 0
        self.connected: ConnectedDevice | None = None
        self.request = AcquisitionRequest(10_000, 1, 0, 80)

    def connect(self) -> ConnectedDevice:
        if self.connected is None:
            info = DeviceInfo(
                device_id=-1,
                serial="SIMULATION",
                model="QE65000（模拟）",
                pixels=1044,
                wavelength_min=194.16,
                wavelength_max=989.11,
                integration_min_us=8_000,
                integration_max_us=65_000_000,
                integration_increment_us=1_000,
                max_intensity=65_535.0,
            )
            span = info.wavelength_max - info.wavelength_min
            wavelengths = tuple(info.wavelength_min + span * index / (info.pixels - 1) for index in range(info.pixels))
            self.connected = ConnectedDevice(info, wavelengths, self.request)
        return self.connected

    def disconnect(self) -> None:
        self.connected = None

    def apply_configuration(self, request: AcquisitionRequest) -> SpectrumContext:
        connected = self.connect()
        self.request = request
        return SpectrumContext(
            source="simulation",
            serial=connected.info.serial,
            integration_us=request.integration_us,
            averages=request.averages,
            boxcar=request.boxcar,
            trigger_mode=0,
            electric_dark=False,
            nonlinearity=False,
            light_source=False,
            pixel_count=len(connected.wavelengths),
            wavelength_min=connected.wavelengths[0],
            wavelength_max=connected.wavelengths[-1],
        )

    def acquire(self, context: SpectrumContext) -> SpectrumFrame:
        connected = self.connect()
        exposure_scale = min(max(context.integration_us / 100_000.0, 0.01), 2.2)
        average_scale = math.sqrt(max(1, context.averages))
        phase = self.frame_index * 0.17
        drift = 1.0 + 0.012 * math.sin(self.frame_index * 0.11)
        values: list[float] = []
        for index, wavelength in enumerate(connected.wavelengths):
            baseline = 115.0 + 0.055 * (wavelength - 194.16)
            excitation_tail = 1_600.0 * math.exp(-0.5 * ((wavelength - 365.0) / 8.5) ** 2)
            main_peak = 31_500.0 * math.exp(-0.5 * ((wavelength - 438.0) / 18.0) ** 2)
            shoulder = 8_200.0 * math.exp(-0.5 * ((wavelength - 515.0) / 34.0) ** 2)
            broad_tail = 2_600.0 * math.exp(-0.5 * ((wavelength - 630.0) / 82.0) ** 2)
            ripple = 36.0 * math.sin(index * 0.037 + phase)
            noise = (58.0 / average_scale) * (
                0.58 * math.sin(index * 0.71 + phase)
                + 0.29 * math.sin(index * 0.137 + phase * 1.9)
                + 0.13 * math.cos(index * 0.043 - phase * 0.7)
            )
            value = baseline + exposure_scale * drift * (excitation_tail + main_peak + shoulder + broad_tail)
            values.append(min(65_535.0, max(0.0, value + ripple + noise)))
        if context.boxcar > 0:
            values = self._boxcar(values, context.boxcar)
        self.frame_index += 1
        return SpectrumFrame(tuple(values), time.time(), context)

    @staticmethod
    def _boxcar(values: list[float], half_width: int) -> list[float]:
        prefix = [0.0]
        for value in values:
            prefix.append(prefix[-1] + value)
        result: list[float] = []
        for index in range(len(values)):
            start = max(0, index - half_width)
            end = min(len(values), index + half_width + 1)
            result.append((prefix[end] - prefix[start]) / (end - start))
        return result

    def shutdown(self) -> None:
        self.disconnect()
