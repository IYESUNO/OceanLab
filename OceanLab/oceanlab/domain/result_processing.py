from __future__ import annotations

import math
from typing import Sequence

from .results import AbsorbanceDataset

PHOTON_ENERGY_EV_NM = 1239.841984


def subtract_dark(raw: Sequence[float], dark: Sequence[float]) -> tuple[float, ...]:
    if len(raw) != len(dark):
        raise ValueError("原始光谱与暗谱的数据点数量不一致。")
    return tuple(sample - offset for sample, offset in zip(raw, dark))


def wavelength_axes_match(left: Sequence[float], right: Sequence[float]) -> bool:
    return len(left) == len(right) and all(
        math.isclose(first, second, rel_tol=0.0, abs_tol=1e-9)
        for first, second in zip(left, right)
    )


def normalize_peak(values: Sequence[float]) -> tuple[float, ...]:
    finite = [value for value in values if math.isfinite(value)]
    peak = max(finite, default=float("nan"))
    if not math.isfinite(peak) or peak <= 0:
        raise ValueError("光谱没有可用于归一化的正峰值。")
    return tuple(value / peak if math.isfinite(value) else float("nan") for value in values)


def calculate_absorbance(
    dataset: AbsorbanceDataset,
    absorbance: bool,
) -> tuple[tuple[float, ...], int]:
    values: list[float] = []
    invalid = 0
    for sample, dark, reference in zip(dataset.sample, dataset.dark, dataset.reference):
        denominator = reference - dark
        ratio = (sample - dark) / denominator if abs(denominator) > 1e-12 else float("nan")
        if absorbance:
            value = -math.log10(ratio) if ratio > 0 and math.isfinite(ratio) else float("nan")
        else:
            value = ratio * 100.0 if math.isfinite(ratio) else float("nan")
        if not math.isfinite(value):
            invalid += 1
        values.append(value)
    return tuple(values), invalid


def photon_energy_ev(wavelength_nm: float) -> float:
    """Convert a positive wavelength in nanometres to photon energy in eV."""
    if not math.isfinite(wavelength_nm) or wavelength_nm <= 0:
        return float("nan")
    return PHOTON_ENERGY_EV_NM / wavelength_nm


def calculate_tauc_direct(dataset: AbsorbanceDataset) -> tuple[tuple[float, ...], int]:
    """Return the direct-transition Tauc ordinate ``(A * hν)²``.

    Absorbance is proportional to the absorption coefficient when the sample
    thickness is constant, so its scale does not change the Tauc intercept.
    """
    absorbance_values, _ = calculate_absorbance(dataset, absorbance=True)
    return calculate_tauc_direct_values(dataset.wavelengths, absorbance_values)


def calculate_tauc_direct_values(
    wavelengths: Sequence[float],
    absorbance_values: Sequence[float],
) -> tuple[tuple[float, ...], int]:
    """Apply the direct-transition Tauc ordinate to paired wavelength/A data."""
    if len(wavelengths) != len(absorbance_values):
        raise ValueError("波长与吸光度的数据点数量不一致。")
    values: list[float] = []
    invalid = 0
    for wavelength, absorbance in zip(wavelengths, absorbance_values):
        energy = photon_energy_ev(wavelength)
        if not math.isfinite(energy) or not math.isfinite(absorbance):
            value = float("nan")
            invalid += 1
        else:
            value = (absorbance * energy) ** 2
        values.append(value)
    return tuple(values), invalid
