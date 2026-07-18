from __future__ import annotations

import math
from typing import Sequence

from .results import AbsorbanceDataset


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
