from __future__ import annotations

import csv
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ports.exporter import ExportRequest, ExportResult


class JsonPreferencesRepository:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def save(self, preferences: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(preferences, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)


class DelimitedSpectrumExporter:
    OCEANVIEW_USER = "360"
    OCEANVIEW_MARKER = ">>>>>Begin Spectral Data<<<<<"

    def export(self, request: ExportRequest) -> ExportResult:
        request.path.parent.mkdir(parents=True, exist_ok=True)
        if request.path.suffix.lower() == ".txt":
            return self._export_oceanview_txt(request)
        delimiter = "\t" if request.path.suffix.lower() == ".txt" else ","
        length = len(request.frame.values)
        dark_values = (
            request.baselines.dark.values
            if request.baselines.dark is not None
            else (float("nan"),) * length
        )
        reference_values = (
            request.baselines.reference.values
            if request.baselines.reference is not None
            else (float("nan"),) * length
        )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                newline="",
                encoding="utf-8-sig",
                dir=request.path.parent,
                prefix=f".{request.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                writer = csv.writer(handle, delimiter=delimiter)
                writer.writerow(["# OceanLab spectrum export"])
                writer.writerow([
                    "# timestamp",
                    datetime.fromtimestamp(request.frame.timestamp).isoformat(timespec="milliseconds"),
                ])
                for name, value in request.metadata.items():
                    writer.writerow([f"# {name}", value])
                writer.writerow([
                    "wavelength_nm",
                    "raw_counts",
                    "dark_counts",
                    "reference_counts",
                    request.processed_label,
                ])
                writer.writerows(zip(
                    request.wavelengths,
                    request.frame.values,
                    dark_values,
                    reference_values,
                    request.processed,
                ))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, request.path)
            result = ExportResult(request.path, request.path.stat().st_size)
            self.verify(result, length)
            return result
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def _export_oceanview_txt(self, request: ExportRequest) -> ExportResult:
        frame = request.frame
        context = frame.context
        timestamp = datetime.fromtimestamp(frame.timestamp)
        serial = request.metadata.get("serial", "").strip() or context.serial
        weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        date_text = (
            f"{weekdays[timestamp.weekday()]} {months[timestamp.month - 1]} {timestamp.day:02d} "
            f"{timestamp:%H:%M:%S} CST {timestamp.year}"
        )
        integration_seconds = context.integration_us / 1_000_000
        integration_text = f"{integration_seconds:.6E}"
        mantissa, exponent = integration_text.split("E")
        integration_text = f"{mantissa}E{int(exponent)}"
        lines = [
            f"节点{request.path.name}的数据",
            "",
            f"Date: {date_text}",
            f"User: {self.OCEANVIEW_USER}",
            f"Spectrometer: {serial}",
            f"触发模式: {context.trigger_mode}",
            f"积分时间 (秒): {integration_text}",
            f"Scans to average: {context.averages}",
            f"启用暗噪声校正: {str(context.electric_dark).lower()}",
            f"启用非线性校正: {str(context.nonlinearity).lower()}",
            f"滑动平均宽度: {context.boxcar}",
            "X轴模式: Wavelengths",
            f"Number of Pixels in Spectrum: {len(frame.values)}",
            self.OCEANVIEW_MARKER,
        ]
        lines.extend(
            f"{float(wavelength):.12g}\t{float(intensity):.12g}"
            for wavelength, intensity in zip(request.wavelengths, frame.values)
        )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                newline="",
                encoding="gb18030",
                dir=request.path.parent,
                prefix=f".{request.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write("\r\n".join(lines))
                handle.write("\r\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, request.path)
            result = ExportResult(request.path, request.path.stat().st_size)
            self.verify(result, len(frame.values))
            return result
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def verify(self, result: ExportResult, expected_rows: int) -> None:
        if not result.path.is_file() or result.size <= 0:
            raise OSError("导出文件不存在或为空。")
        if result.path.suffix.lower() == ".txt":
            self._verify_oceanview_txt(result.path, expected_rows)
            return
        delimiter = "\t" if result.path.suffix.lower() == ".txt" else ","
        data_rows = 0
        with result.path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            header_seen = False
            for row in reader:
                if not row:
                    continue
                if row[0] == "wavelength_nm":
                    header_seen = True
                    continue
                if header_seen:
                    if len(row) != 5:
                        raise ValueError("导出数据列数不正确。")
                    float(row[0])
                    for value in row[1:]:
                        parsed = float(value)
                        if not (math.isfinite(parsed) or math.isnan(parsed)):
                            raise ValueError("导出文件包含无效数值。")
                    data_rows += 1
        if not header_seen or data_rows != expected_rows:
            raise ValueError(f"导出文件数据行数错误：期望 {expected_rows}，实际 {data_rows}。")

    def _verify_oceanview_txt(self, path: Path, expected_rows: int) -> None:
        text = path.read_text(encoding="gb18030")
        lines = text.splitlines()
        try:
            marker_index = lines.index(self.OCEANVIEW_MARKER)
        except ValueError as exc:
            raise ValueError("导出文件缺少 OceanView 光谱数据开始标记。") from exc
        data_rows = 0
        for line in lines[marker_index + 1 :]:
            if not line.strip():
                continue
            cells = line.split()
            if len(cells) != 2:
                raise ValueError("OceanView 导出数据必须包含波长和强度两列。")
            float(cells[0])
            float(cells[1])
            data_rows += 1
        if data_rows != expected_rows:
            raise ValueError(f"导出文件数据行数错误：期望 {expected_rows}，实际 {data_rows}。")
