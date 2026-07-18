from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import BaselineSet, SpectrumFrame
from ..domain.processing import ProcessingMode, process_spectrum


@dataclass
class SpectrumService:
    current: SpectrumFrame | None = None
    baselines: BaselineSet = BaselineSet()

    def accept(self, frame: SpectrumFrame) -> tuple[str, ...]:
        cleared: list[str] = []
        dark = self.baselines.dark
        reference = self.baselines.reference
        if dark is not None and not dark.context.compatible_with(frame.context):
            dark = None
            cleared.append("暗谱")
        if reference is not None and not reference.context.compatible_with(frame.context):
            reference = None
            cleared.append("参考谱")
        self.current = frame
        self.baselines = BaselineSet(dark, reference)
        return tuple(cleared)

    def capture_dark(self) -> None:
        if self.current is None:
            raise RuntimeError("请先采集一帧光谱。")
        self.baselines = BaselineSet(self.current, self.baselines.reference)

    def capture_reference(self) -> None:
        if self.current is None:
            raise RuntimeError("请先采集一帧光谱。")
        self.baselines = BaselineSet(self.baselines.dark, self.current)

    def clear_baselines(self) -> None:
        self.baselines = BaselineSet()

    def clear_dark(self) -> None:
        self.baselines = BaselineSet(None, self.baselines.reference)

    def processed(self, mode: ProcessingMode) -> tuple[list[float], str]:
        if self.current is None:
            return [], "强度 (counts)"
        return process_spectrum(self.current, self.baselines, mode)
