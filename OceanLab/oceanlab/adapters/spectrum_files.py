from __future__ import annotations

import csv
import math
import re
from pathlib import Path

from ..domain.results import AbsorbanceDataset, ImportedSpectrum


class DelimitedSpectrumImporter:
    ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")
    OCEANVIEW_MARKER = ">>>>>Begin Spectral Data<<<<<"
    OCEANVIEW_NODE = re.compile(
        r"^节点(?P<name>.+)_[^_]+_\d{2}-\d{2}-\d{2}-\d{3}\.txt的数据$"
    )
    TIMESTAMP_SUFFIX = re.compile(r"_\d{8}_\d{6}_\d{3}$")
    SEQUENCE_SUFFIX = re.compile(r"_\d{1,9}$")

    def load(self, path: Path) -> ImportedSpectrum:
        path = Path(path).resolve()
        text = self._read_text(path)
        if self.OCEANVIEW_MARKER in text:
            result = self._load_oceanview(path, text)
        elif any(line.strip().startswith("# OceanLab spectrum export") for line in text.splitlines()):
            result = self._load_oceanlab(path, text)
        else:
            result = self._load_two_column(path, text)
        self._validate_axis(result.wavelengths)
        if len(result.raw) != len(result.wavelengths):
            raise ValueError("波长和强度的数据点数量不一致。")
        return result

    def load_absorbance(self, path: Path) -> AbsorbanceDataset:
        spectrum = self.load(path)
        if spectrum.dark is None or spectrum.reference is None:
            raise ValueError("完整 AB 文件必须同时包含暗谱和参考谱。")
        return AbsorbanceDataset(
            name=spectrum.name,
            wavelengths=spectrum.wavelengths,
            sample=spectrum.raw,
            dark=spectrum.dark,
            reference=spectrum.reference,
            source_paths=(spectrum.source_path,),
        )

    def load_absorbance_triplet(
        self,
        sample_path: Path,
        dark_path: Path,
        reference_path: Path,
    ) -> AbsorbanceDataset:
        sample = self.load(sample_path)
        dark = self.load(dark_path)
        reference = self.load(reference_path)
        self._validate_matching_axes(sample.wavelengths, dark.wavelengths, "暗谱")
        self._validate_matching_axes(sample.wavelengths, reference.wavelengths, "参考谱")
        return AbsorbanceDataset(
            name=sample.name,
            wavelengths=sample.wavelengths,
            sample=sample.raw,
            dark=dark.raw,
            reference=reference.raw,
            source_paths=(sample.source_path, dark.source_path, reference.source_path),
        )

    def _read_text(self, path: Path) -> str:
        if path.suffix.lower() not in {".csv", ".txt"}:
            raise ValueError("仅支持 CSV 和 TXT 光谱文件。")
        if not path.is_file():
            raise FileNotFoundError(f"光谱文件不存在：{path}")
        data = path.read_bytes()
        for encoding in self.ENCODINGS:
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("文件编码无法识别，请使用 UTF-8 或 GB18030。")

    def _load_oceanlab(self, path: Path, text: str) -> ImportedSpectrum:
        delimiter = "\t" if path.suffix.lower() == ".txt" else ","
        try:
            rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
        except csv.Error as exc:
            raise ValueError(f"OceanLab 文件结构无效：{exc}") from exc
        header_index = next(
            (index for index, row in enumerate(rows) if row and row[0].strip() == "wavelength_nm"),
            None,
        )
        if header_index is None:
            raise ValueError("OceanLab 文件缺少数据表头。")
        header = [cell.strip() for cell in rows[header_index]]
        required = {"wavelength_nm", "raw_counts", "dark_counts", "reference_counts"}
        if not required.issubset(header):
            raise ValueError("OceanLab 文件缺少波长、原始、暗谱或参考谱列。")
        indexes = {name: header.index(name) for name in required}
        wavelengths: list[float] = []
        raw: list[float] = []
        dark: list[float] = []
        reference: list[float] = []
        for line_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
            if not row or all(not cell.strip() for cell in row):
                continue
            try:
                wavelengths.append(float(row[indexes["wavelength_nm"]]))
                raw.append(float(row[indexes["raw_counts"]]))
                dark.append(float(row[indexes["dark_counts"]]))
                reference.append(float(row[indexes["reference_counts"]]))
            except (IndexError, ValueError) as exc:
                raise ValueError(f"OceanLab 文件第 {line_number} 行包含无效数据。") from exc
        metadata: list[tuple[str, str]] = []
        for row in rows[1:header_index]:
            if row and row[0].strip().startswith("#"):
                key = row[0].strip()[1:].strip()
                value = row[1].strip() if len(row) > 1 else ""
                metadata.append((key, value))
        self._validate_values(raw, "原始强度")
        metadata_dict = dict(metadata)
        display_name = metadata_dict.get("base_name", "").strip() or self._legacy_base_name(path.stem)
        return ImportedSpectrum(
            name=display_name,
            source_path=path,
            wavelengths=tuple(wavelengths),
            raw=tuple(raw),
            dark=self._optional_finite_series(dark),
            reference=self._optional_finite_series(reference),
            metadata=tuple(metadata),
        )

    def _load_two_column(self, path: Path, text: str) -> ImportedSpectrum:
        source_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if not source_lines:
            raise ValueError("光谱文件没有数据。")
        delimiter = self._detect_delimiter(source_lines[0])
        parsed_rows = [self._split(line, delimiter) for line in source_lines]
        header_skipped = False
        wavelengths: list[float] = []
        raw: list[float] = []
        for line_number, row in enumerate(parsed_rows, start=1):
            if len(row) != 2:
                raise ValueError("通用光谱文件必须恰好包含波长和强度两列。")
            try:
                wavelength, intensity = float(row[0]), float(row[1])
            except ValueError as exc:
                if line_number == 1 and not header_skipped:
                    header_skipped = True
                    continue
                raise ValueError(f"通用光谱文件第 {line_number} 个数据行不是数值。") from exc
            wavelengths.append(wavelength)
            raw.append(intensity)
        self._validate_values(raw, "强度")
        return ImportedSpectrum(path.stem, path, tuple(wavelengths), tuple(raw))

    def _load_oceanview(self, path: Path, text: str) -> ImportedSpectrum:
        lines = text.splitlines()
        try:
            marker_index = next(index for index, line in enumerate(lines) if line.strip() == self.OCEANVIEW_MARKER)
        except StopIteration as exc:
            raise ValueError("OceanView 文件缺少光谱数据开始标记。") from exc

        header_lines = [line.strip() for line in lines[:marker_index] if line.strip()]
        metadata: list[tuple[str, str]] = []
        for line in header_lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                metadata.append((key.strip(), value.strip()))

        node_name = ""
        if header_lines:
            match = self.OCEANVIEW_NODE.match(header_lines[0])
            if match:
                node_name = match.group("name").strip()

        wavelengths: list[float] = []
        raw: list[float] = []
        for line_number, line in enumerate(lines[marker_index + 1 :], start=marker_index + 2):
            if not line.strip():
                continue
            cells = line.split()
            if len(cells) != 2:
                raise ValueError(f"OceanView 文件第 {line_number} 行必须包含波长和强度两列。")
            try:
                wavelength, intensity = float(cells[0]), float(cells[1])
            except ValueError as exc:
                raise ValueError(f"OceanView 文件第 {line_number} 行包含无效数据。") from exc
            wavelengths.append(wavelength)
            raw.append(intensity)

        declared_count = dict(metadata).get("Number of Pixels in Spectrum")
        if declared_count:
            try:
                expected_count = int(declared_count)
            except ValueError as exc:
                raise ValueError("OceanView 文件的像素数量无效。") from exc
            if expected_count != len(wavelengths):
                raise ValueError(
                    f"OceanView 文件声明 {expected_count} 个像素，实际读取 {len(wavelengths)} 个。"
                )
        self._validate_values(raw, "OceanView 强度")
        return ImportedSpectrum(
            name=node_name or self._legacy_base_name(path.stem),
            source_path=path,
            wavelengths=tuple(wavelengths),
            raw=tuple(raw),
            metadata=tuple(metadata),
        )

    @classmethod
    def _legacy_base_name(cls, stem: str) -> str:
        """Recover the saved basic name from files exported before it became metadata."""
        without_timestamp = cls.TIMESTAMP_SUFFIX.sub("", stem)
        if without_timestamp != stem:
            return without_timestamp
        without_sequence = cls.SEQUENCE_SUFFIX.sub("", stem)
        return without_sequence or stem

    @staticmethod
    def _detect_delimiter(line: str) -> str | None:
        for delimiter in ("\t", ",", ";"):
            if delimiter in line:
                return delimiter
        return None

    @staticmethod
    def _split(line: str, delimiter: str | None) -> list[str]:
        return [cell.strip() for cell in (line.split(delimiter) if delimiter else line.split())]

    @staticmethod
    def _optional_finite_series(values: list[float]) -> tuple[float, ...] | None:
        return tuple(values) if values and all(math.isfinite(value) for value in values) else None

    @staticmethod
    def _validate_values(values: list[float], label: str) -> None:
        if len(values) < 2:
            raise ValueError(f"{label}至少需要两个数据点。")
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{label}包含非有限数值。")

    @staticmethod
    def _validate_axis(wavelengths: tuple[float, ...]) -> None:
        if len(wavelengths) < 2:
            raise ValueError("波长轴至少需要两个数据点。")
        if not all(math.isfinite(value) for value in wavelengths):
            raise ValueError("波长轴包含非有限数值。")
        if any(current <= previous for previous, current in zip(wavelengths, wavelengths[1:])):
            raise ValueError("波长必须严格递增且不能重复。")

    @staticmethod
    def _validate_matching_axes(expected: tuple[float, ...], actual: tuple[float, ...], label: str) -> None:
        if len(expected) != len(actual) or any(
            not math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)
            for left, right in zip(expected, actual)
        ):
            raise ValueError(f"{label}与样品的波长网格不一致，无法计算 AB。")
