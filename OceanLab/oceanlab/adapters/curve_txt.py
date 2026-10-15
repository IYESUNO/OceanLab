"""Two-column display results, kept distinct from raw instrument spectra."""
import math
import os
import tempfile
from pathlib import Path

from ..ports.exporter import CurveExportRequest, ExportResult

CURVE_EXPORT_MARKER = "# OceanLab curve export v1"
CURVE_DATA_MARKER = ">>>>>Begin Spectral Data<<<<<"


class CurveTxtExporter:
    # These are serialization limits, not claims about instrument accuracy.
    # Eight significant digits bound Y rounding to ~5e-8 relative error.
    Y_FORMAT = ".8g"
    X_DIGITS = 12
    X_STEP_ERROR = 1e-6

    @staticmethod
    def _number(value, spec):
        if math.isnan(value):
            return "NaN"
        text = format(value, spec)
        return "0" if float(text) == 0 else text

    @staticmethod
    def _precision_description(spec):
        digits = int(spec[1:-1])
        if spec == ".0f":
            return "Integer pixel indices (exact)"
        return str(digits) + " significant digits; numerical rounding, not measurement uncertainty"

    def _x_format(self, values, label):
        if label == "像素索引" and all(float(x).is_integer() for x in values):
            return ".0f"
        steps = tuple(b - a for a, b in zip(values, values[1:]))
        local_steps = tuple(min(steps[max(0, i - 1):i + 1]) for i in range(len(values)))
        for digits in range(self.X_DIGITS, 18):
            candidate = ".{}g".format(digits)
            rounded = tuple(float(self._number(x, candidate)) for x in values)
            if (all(b > a for a, b in zip(rounded, rounded[1:])) and
                    all(abs(x - r) <= step * self.X_STEP_ERROR
                        for x, r, step in zip(values, rounded, local_steps))):
                return candidate
        raise ValueError("导出横轴精度不足，无法保留不同数据点。")

    def format_columns(self, x_values, y_values, x_label, y_label):
        if len(x_values) != len(y_values):
            raise ValueError("导出 X/Y 数据点数量不一致。")
        x_spec = self._x_format(x_values, x_label)
        y_spec = self.Y_FORMAT
        rows = tuple((self._number(x, x_spec), self._number(y, y_spec))
                     for x, y in zip(x_values, y_values))
        return rows, x_spec, y_spec

    @staticmethod
    def _text(value):
        return str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")

    @staticmethod
    def _verify(path, expected):
        lines = path.read_text(encoding="gb18030").splitlines()
        if lines.count(CURVE_DATA_MARKER) != 1:
            raise ValueError("曲线导出文件缺少数据开始标记。")
        rows = lines[lines.index(CURVE_DATA_MARKER) + 1:]
        if len(rows) != len(expected):
            raise ValueError("曲线导出文件的列数或点数不正确。")
        for line, (expected_x, expected_y) in zip(rows, expected):
            cells = line.split("\t")
            if len(cells) != 2:
                raise ValueError("曲线导出文件必须为两列。")
            if cells != [expected_x, expected_y]:
                raise ValueError("曲线导出数值与设定的输出精度不一致。")

    def export(self, request: CurveExportRequest) -> ExportResult:
        curve = request.curve
        if len(curve.wavelengths) != len(curve.values) or len(curve.values) < 2:
            raise ValueError("曲线的 X/Y 数据点必须配对且至少有两个点。")
        if not all(math.isfinite(x) for x in curve.wavelengths):
            raise ValueError("曲线横轴包含无效值。")
        if any(b <= a for a, b in zip(curve.wavelengths, curve.wavelengths[1:])):
            raise ValueError("曲线横轴必须严格递增。")
        if any(math.isinf(y) for y in curve.values) or not any(math.isfinite(y) for y in curve.values):
            raise ValueError("曲线没有可导出的有效数值。")
        path = Path(request.path)
        expected, x_spec, y_spec = self.format_columns(curve.wavelengths, curve.values, request.x_label, request.y_label)
        metadata = dict(request.metadata)
        metadata.update(curve.metadata)
        headers = {
            **metadata,
            "Curve": curve.name,
            "X": request.x_label,
            "Y": request.y_label,
            "X precision": self._precision_description(x_spec),
            "Y precision": self._precision_description(y_spec),
            "Precision scope": "Export rounding only; calculations use unrounded data",
            "User": "360",
            "Points": str(len(expected)),
            "Invalid points": str(sum(math.isnan(y) for y in curve.values)),
            "Data scope": "Full curve; display zoom does not trim data",
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="gb18030", newline="", dir=str(path.parent),
                prefix=".oceanlab-curve-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(CURVE_EXPORT_MARKER + "\r\n")
                for key, value in headers.items():
                    handle.write("# {}: {}\r\n".format(self._text(key), self._text(value)))
                handle.write(CURVE_DATA_MARKER + "\r\n")
                for x, y in expected:
                    handle.write("{}\t{}\r\n".format(x, y))
                handle.flush()
                os.fsync(handle.fileno())
            self._verify(temporary, expected)
            os.replace(str(temporary), str(path))
            self._verify(path, expected)
            return ExportResult(path.resolve(), path.stat().st_size)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
