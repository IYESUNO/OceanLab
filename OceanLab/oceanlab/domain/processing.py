from __future__ import annotations

import math
from enum import Enum

from .models import BaselineSet, SpectrumFrame
from .result_processing import transmission_ratio


class ProcessingMode(str, Enum):
    RAW = "原始强度"
    DARK_SUBTRACTED = "暗谱扣除"
    TRANSMITTANCE = "透过率 (%)"
    ABSORBANCE = "吸光度 (A)"


def process_spectrum(
    frame: SpectrumFrame,
    baselines: BaselineSet,
    mode: ProcessingMode,
) -> tuple[list[float], str]:
    raw = frame.values
    dark_frame = baselines.dark
    reference_frame = baselines.reference
    dark_valid = dark_frame is not None and dark_frame.context.compatible_with(frame.context)
    reference_valid = reference_frame is not None and reference_frame.context.compatible_with(frame.context)
    dark = dark_frame.values if dark_valid else (0.0,) * len(raw)

    if mode is ProcessingMode.DARK_SUBTRACTED:
        return [sample - offset for sample, offset in zip(raw, dark)], "暗谱扣除 (counts)"
    if mode in (ProcessingMode.TRANSMITTANCE, ProcessingMode.ABSORBANCE):
        if not reference_valid:
            return [], "需要参考谱"
        values: list[float] = []
        for sample, reference, offset in zip(raw, reference_frame.values, dark):
            ratio = transmission_ratio(sample, reference, offset)
            if mode is ProcessingMode.TRANSMITTANCE:
                values.append(ratio * 100.0)
            else:
                values.append(-math.log10(ratio) if ratio > 0 else float("nan"))
        return values, mode.value
    return list(raw), "强度 (counts)"
