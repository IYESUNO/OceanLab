from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Sequence

from .results import ImportedSpectrum


@dataclass(frozen=True)
class SignalAssessment:
    plateau: float | None
    plateau_points: int
    negative_points: int
    negative_run: int
    suspected_offset: bool


@dataclass(frozen=True)
class SignalOffset:
    delta: float
    target: float
    source: str
    estimated: bool
    automatic: bool = False


def assess_signal(values: Sequence[float]) -> SignalAssessment:
    """Conservative trial heuristics, not an instrument saturation flag.

    Require at least eight adjacent near-maximum points and twenty adjacent
    points below -100 counts. Small negative edge/noise points do not trigger.
    """
    peak = max(values, default=0.0)
    tolerance = max(2.0, abs(peak) * 1e-5)
    plateau_values = []
    plateau_run = negative_run = longest_plateau = longest_negative = 0
    for value in values:
        near_peak = peak > 1000 and math.isfinite(value) and peak - value <= tolerance
        plateau_run = plateau_run + 1 if near_peak else 0
        negative_run = negative_run + 1 if value < -100 else 0
        longest_plateau = max(longest_plateau, plateau_run)
        longest_negative = max(longest_negative, negative_run)
        if near_peak:
            plateau_values.append(value)
    plateau = median(plateau_values) if longest_plateau >= 8 else None
    return SignalAssessment(plateau, len(plateau_values) if plateau is not None else 0,
                            sum(value < 0 for value in values), longest_negative,
                            plateau is not None and longest_negative >= 20)


def same_acquisition(left: ImportedSpectrum, right: ImportedSpectrum) -> bool:
    """Only estimate across identified devices with matching capture settings."""
    def signature(spectrum):
        data = {key.casefold(): value.strip() for key, value in spectrum.metadata}
        instrument = data.get('spectrometer') or data.get('serial')
        integration = data.get('积分时间 (秒)') or data.get('integration time (sec)')
        try:
            time = float(integration) if integration else float(data['integration_us']) / 1e6
        except (KeyError, ValueError):
            return None
        if not instrument or not math.isfinite(time) or time <= 0:
            return None
        return (instrument, time,
                data.get('scans to average') or data.get('averages'),
                data.get('启用暗噪声校正') or data.get('electric_dark'),
                data.get('启用非线性校正') or data.get('nonlinearity'),
                data.get('滑动平均宽度') or data.get('boxcar'),
                data.get('触发模式') or data.get('trigger_mode'))
    first, second = signature(left), signature(right)
    return first is not None and first == second
