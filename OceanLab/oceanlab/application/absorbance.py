from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from ..domain.result_processing import calculate_absorbance, calculate_tauc, wavelength_axes_match
from ..domain.results import AbsorbanceDataset, AbsorbanceSampleEntry, ImportedSpectrum, PlotSeries
from ..domain.signal_offset import SignalOffset, assess_signal, same_acquisition
from statistics import median
from ..ports.spectrum_importer import SpectrumImporter
from .pl_results import ImportBatchResult


@dataclass
class AbsorbanceAnalysisGroup:
    group_id: str
    name: str
    entries: list[AbsorbanceSampleEntry] = field(default_factory=list)
    reference: ImportedSpectrum | None = None
    offsets: dict[str, SignalOffset] = field(default_factory=dict)
    offset_suppressed: set[str] = field(default_factory=set)
    visible: bool = True


@dataclass(frozen=True)
class AbsorbancePlotResult:
    series: tuple[PlotSeries, ...]
    skipped: tuple[tuple[str, str], ...]
    invalid_counts: tuple[tuple[str, int], ...]
    signal_warnings: tuple[tuple[str, str], ...] = ()


class HistoricalAbsorbanceService:
    COLORS = ("#d13438", "#0f6cbd", "#107c10", "#8764b8", "#ca5010", "#038387", "#c239b3", "#498205")

    def __init__(self, importer: SpectrumImporter, automatic_offsets: bool = True):
        self.importer = importer
        self.automatic_offsets = automatic_offsets
        self.groups: list[AbsorbanceAnalysisGroup] = []
        self.current_group_id = ""
        self.active_id: str | None = None
        self._next_group_number = 1
        self.create_group()

    @property
    def current_group(self) -> AbsorbanceAnalysisGroup:
        return next(group for group in self.groups if group.group_id == self.current_group_id)

    @property
    def entries(self) -> list[AbsorbanceSampleEntry]:
        return [entry for group in self.groups for entry in group.entries]

    @property
    def reference(self) -> ImportedSpectrum | None:
        return self.current_group.reference

    def create_group(self) -> AbsorbanceAnalysisGroup:
        number = self._next_group_number
        self._next_group_number += 1
        group = AbsorbanceAnalysisGroup(f"ab-{number}", f"分析组 {number}")
        self.groups.append(group)
        self.current_group_id = group.group_id
        return group

    def select_group(self, group_id: str) -> None:
        if any(group.group_id == group_id for group in self.groups):
            self.current_group_id = group_id

    def set_group_visible(self, group_id: str, visible: bool) -> None:
        group = next((group for group in self.groups if group.group_id == group_id), None)
        if group is None:
            return
        group.visible = bool(visible)
        if not group.visible and any(entry.entry_id == self.active_id for entry in group.entries):
            self.active_id = None

    def remove_group(self, group_id: str) -> None:
        group = next((group for group in self.groups if group.group_id == group_id), None)
        if group is None:
            return
        if not self.can_remove_group(group_id):
            return
        position = self.groups.index(group)
        if any(entry.entry_id == self.active_id for entry in group.entries):
            self.active_id = None
        self.groups.remove(group)
        if not self.groups:
            self._next_group_number = 1
            self.create_group()
        elif self.current_group_id == group_id:
            self.current_group_id = self.groups[min(position, len(self.groups) - 1)].group_id

    def can_remove_group(self, group_id: str) -> bool:
        group = next((group for group in self.groups if group.group_id == group_id), None)
        return group is not None and (len(self.groups) > 1 or bool(group.entries) or group.reference is not None)

    def _validate_spectrum(self, spectrum: ImportedSpectrum, excluding_id: str | None = None, excluding_role: str = "") -> None:
        group = self.current_group
        comparisons = [(entry.display_name, entry.spectrum) for entry in group.entries if entry.entry_id != excluding_id]
        if group.reference is not None and excluding_role != "reference":
            comparisons.append(("本组 BG 参考", group.reference))
        for name, other in comparisons:
            if not wavelength_axes_match(spectrum.wavelengths, other.wavelengths):
                raise ValueError(f"与{name}的波长网格不一致；请使用同一波长网格，或新建分析组。")

    def load_samples(self, paths: Iterable[Path], reload: bool = False) -> ImportBatchResult:
        group = self.current_group
        added = duplicates = 0
        errors: list[tuple[Path, str]] = []
        for raw_path in paths:
            path = Path(raw_path).resolve()
            entry_id = f"{group.group_id}|{str(path).casefold()}"
            existing = next((entry for entry in group.entries if entry.entry_id == entry_id), None)
            if existing is not None and not reload:
                duplicates += 1
                continue
            try:
                spectrum = self.importer.load(path)
                self._validate_spectrum(spectrum, excluding_id=entry_id)
            except (OSError, ValueError) as exc:
                errors.append((path, str(exc)))
                continue
            if existing is not None:
                self._replace(entry_id, spectrum=spectrum)
                group.offsets.pop(entry_id, None)
                group.offset_suppressed.discard(entry_id)
                duplicates += 1
            else:
                group.entries.append(AbsorbanceSampleEntry(
                    entry_id, group.group_id, spectrum, spectrum.name,
                    self.COLORS[len(self.entries) % len(self.COLORS)],
                ))
                if group.visible:
                    self.active_id = entry_id
                added += 1
        self.apply_automatic_offsets()
        return ImportBatchResult(added, duplicates, tuple(errors))

    def load_reference(self, path: Path) -> ImportedSpectrum:
        spectrum = self.importer.load(Path(path))
        self._validate_spectrum(spectrum, excluding_role="reference")
        first_reference = self.current_group.reference is None
        self.current_group.reference = spectrum
        self.current_group.offsets.pop("BG", None)
        self.current_group.offset_suppressed.discard("BG")
        if first_reference and self.current_group.visible:
            visible = next((entry for entry in reversed(self.current_group.entries) if entry.visible), None)
            if visible is not None:
                self.active_id = visible.entry_id
        self.apply_automatic_offsets()
        return spectrum

    def clear_reference(self) -> None:
        self.current_group.reference = None
        self.current_group.offsets.pop("BG", None)
        self.current_group.offset_suppressed.discard("BG")

    def group_signals(self):
        group = self.current_group
        return ([('BG', 'BG', group.reference)] if group.reference else []) + [
            (entry.entry_id, entry.display_name, entry.spectrum) for entry in group.entries]

    def offset_suggestion(self, signal_id: str):
        signals = self.group_signals()
        spectrum = next((item for key, _name, item in signals if key == signal_id), None)
        if spectrum is None:
            raise ValueError("本组信号已移除。")
        assessment = assess_signal(spectrum.raw)
        candidates = []
        for key, name, other in signals:
            if key == signal_id or not same_acquisition(spectrum, other):
                continue
            info = assess_signal(other.raw)
            if info.plateau is not None and info.negative_run < 20:
                candidates.append((info.plateau, name))
        if not assessment.suspected_offset or not candidates:
            return assessment, None, "无可靠的同组平台基准，请填写已确认的数值"
        target = median(value for value, _name in candidates)
        # Inconsistent peer plateaus cannot serve as a single baseline.
        if max(value for value, _ in candidates) - min(value for value, _ in candidates) > max(20, target * .001):
            return assessment, None, "同组平台不一致，请填写已确认的数值"
        if target <= assessment.plateau:
            return assessment, None, "同组平台不能解释向下偏移，请检查测量"
        return assessment, target, "同组匹配采集参数的平台估算：" + "、".join(name for _value, name in candidates)

    def make_offset(self, signal_id: str, target: float, source: str, estimated: bool, automatic: bool = False) -> SignalOffset:
        assessment, _suggested, _source = self.offset_suggestion(signal_id)
        if not assessment.suspected_offset:
            raise ValueError("未同时检测到饱和平台和连续明显负值，不建议整体偏移校正。")
        if not math.isfinite(target) or target <= assessment.plateau:
            raise ValueError("基准必须为有限数值，且大于原平台。")
        correction = SignalOffset(target - assessment.plateau, target, source, estimated, automatic)
        spectrum = next(s for key, _name, s in self.group_signals() if key == signal_id)
        if not all(math.isfinite(value + correction.delta) for value in spectrum.raw):
            raise ValueError("补偿量过大。")
        return correction

    def set_offset(self, signal_id: str, correction: SignalOffset | None) -> None:
        if correction is None:
            self.current_group.offsets.pop(signal_id, None)
            self.current_group.offset_suppressed.add(signal_id)
        else:
            validated = self.make_offset(signal_id, correction.target, correction.source, correction.estimated, correction.automatic)
            self.current_group.offsets[signal_id] = validated
            self.current_group.offset_suppressed.discard(signal_id)

    def apply_automatic_offsets(self) -> None:
        """Apply only well-supported group estimates after a successful import.

        Rendering/previewing cannot reapply a correction explicitly restored
        by the user. Reloading that signal starts a fresh assessment.
        """
        if not self.automatic_offsets:
            return
        group = self.current_group
        for key, _name, _spectrum in self.group_signals():
            if key in group.offsets or key in group.offset_suppressed:
                continue
            info, target, source = self.offset_suggestion(key)
            if info.suspected_offset and target is not None:
                group.offsets[key] = self.make_offset(key, target, source, True, automatic=True)

    def set_active(self, entry_id: str) -> None:
        entry = next((entry for entry in self.entries if entry.entry_id == entry_id), None)
        if entry is not None:
            self.current_group_id = entry.group_id
            self.current_group.visible = True
            self.active_id = entry_id
            self._replace(entry_id, visible=True)

    def set_visible(self, entry_id: str, visible: bool) -> None:
        self._replace(entry_id, visible=bool(visible))
        if not visible and self.active_id == entry_id:
            self.active_id = None

    def rename(self, entry_id: str, display_name: str) -> None:
        name = display_name.strip()
        if not name:
            raise ValueError("曲线名称不能为空。")
        self._replace(entry_id, display_name=name)

    def set_color(self, entry_id: str, color: str) -> None:
        self._replace(entry_id, color=color)

    def _replace(self, entry_id: str, **changes) -> None:
        for group in self.groups:
            group.entries = [replace(entry, **changes) if entry.entry_id == entry_id else entry for entry in group.entries]

    def remove(self, entry_id: str) -> None:
        for group in self.groups:
            group.entries = [entry for entry in group.entries if entry.entry_id != entry_id]
            group.offsets.pop(entry_id, None)
            group.offset_suppressed.discard(entry_id)
        if self.active_id == entry_id:
            self.active_id = None

    def clear_current_group(self) -> None:
        group = self.current_group
        if any(entry.entry_id == self.active_id for entry in group.entries):
            self.active_id = None
        group.entries.clear()
        group.reference = None
        group.offsets.clear()
        group.offset_suppressed.clear()
        group.visible = True

    def clear(self) -> None:
        self.groups.clear()
        self.active_id = None
        self._next_group_number = 1
        self.create_group()

    def build_plot(self, absorbance: bool, tauc: bool = False, correction_overrides=None,
                   tauc_exponent: float = 2.0) -> AbsorbancePlotResult:
        series: list[PlotSeries] = []
        series_ids: list[str] = []
        rendered_ids: list[str] = []
        skipped: list[tuple[str, str]] = []
        invalid_counts: list[tuple[str, int]] = []
        signal_warnings: list[tuple[str, str]] = []
        populated_groups = sum(bool(group.entries) for group in self.groups)
        for group in self.groups:
            if not group.visible and not (correction_overrides is not None and group is self.current_group):
                continue
            offsets = correction_overrides if correction_overrides is not None and group is self.current_group else group.offsets
            bg_offset = offsets.get('BG')
            bg_info = assess_signal(group.reference.raw) if group.reference else None
            for entry in group.entries:
                if not entry.visible:
                    continue
                name = f"{group.name} / {entry.display_name}" if populated_groups > 1 else entry.display_name
                if group.reference is None:
                    skipped.append((name, "本组尚未选择 BG 参考"))
                    continue
                sample = entry.spectrum
                sample_offset = offsets.get(entry.entry_id)
                sample_info = assess_signal(sample.raw)
                dataset = AbsorbanceDataset(
                    name=name,
                    wavelengths=sample.wavelengths,
                    sample=tuple(value + sample_offset.delta for value in sample.raw) if sample_offset else sample.raw,
                    dark=(0.0,) * len(sample.raw),
                    reference=tuple(value + bg_offset.delta for value in group.reference.raw) if bg_offset else group.reference.raw,
                    source_paths=tuple(s.source_path for s in (sample, group.reference) if s is not None),
                )
                values, invalid = calculate_tauc(dataset, tauc_exponent) if tauc else calculate_absorbance(dataset, absorbance)
                invalid_counts.append((name, invalid))
                bad_bg = sum(value <= 1e-12 for value in dataset.reference)
                bad_sample = sum(value <= 0 if absorbance or tauc else value < 0 for value in dataset.sample)
                reasons = []
                if bad_bg:
                    reasons.append(f"BG 非正值 {bad_bg} 点")
                if bad_sample:
                    reasons.append(f"样品{'非正' if absorbance or tauc else '负'}值 {bad_sample} 点")
                if tauc and tauc_exponent == 0.5:
                    negative_a = sum(s > r > 1e-12 for s, r in zip(dataset.sample, dataset.reference))
                    if negative_a:
                        reasons.append(f"负吸光度 {negative_a} 点，1/2 次方无实数结果")
                if reasons:
                    signal_warnings.append((name, "、".join(reasons)))
                if any(math.isfinite(value) for value in values):
                    rendered_ids.append(entry.entry_id)
                else:
                    skipped.append((name, "全部计算点无效，请检查本组 BG 和样品"))
                series_ids.append(entry.entry_id)
                series.append(PlotSeries(name, sample.wavelengths, values, entry.color, metadata=sample.metadata + (
                    ("Source file", str(sample.source_path)),
                    ("Group", group.name),
                    ("BG", str(group.reference.source_path)),
                    ("BG offset counts", str(bg_offset.delta if bg_offset else 0)),
                    ("Sample offset counts", str(sample_offset.delta if sample_offset else 0)),
                    ("BG offset target", str(bg_offset.target) if bg_offset else "None"),
                    ("BG offset source", bg_offset.source if bg_offset else "None"),
                    ("BG offset estimated", str(bg_offset.estimated) if bg_offset else "False"),
                    ("BG offset automatic", str(bg_offset.automatic) if bg_offset else "False"),
                    ("Sample offset target", str(sample_offset.target) if sample_offset else "None"),
                    ("Sample offset source", sample_offset.source if sample_offset else "None"),
                    ("Sample offset estimated", str(sample_offset.estimated) if sample_offset else "False"),
                    ("Sample offset automatic", str(sample_offset.automatic) if sample_offset else "False"),
                    ("BG plateau suspect points", str(bg_info.plateau_points)),
                    ("Sample plateau suspect points", str(sample_info.plateau_points)),
                    ("Formula", "(-log10((sample + sample_offset) / (BG + BG_offset)) * E_eV)" +
                     ("^2" if tauc_exponent == 2.0 else "^(1/2)") if tauc else
                     "-log10((sample + sample_offset) / (BG + BG_offset))" if absorbance else
                     "(sample + sample_offset) / (BG + BG_offset) * 100"),
                )))
        active_id = self.active_id if self.active_id in rendered_ids else rendered_ids[0] if rendered_ids else None
        if correction_overrides is None:
            self.active_id = active_id
        series = [replace(item, active=entry_id == active_id) for item, entry_id in zip(series, series_ids)]
        return AbsorbancePlotResult(tuple(series), tuple(skipped), tuple(invalid_counts), tuple(signal_warnings))
