from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from tkinter import font as tkfont

from ..domain.results import PlotSeries

class SpectrumPlot:
    BG = "#ffffff"
    GRID = "#d7dee8"
    AXIS = "#475569"
    LINE = "#d13438"
    CURSOR = "#e600a9"
    BORDER = "#94a3b8"
    LINE_WIDTH = 1.6
    ACTIVE_LINE_WIDTH = LINE_WIDTH * 2
    PLOT_LEFT = 108
    Y_TITLE_X = 24

    def __init__(self, canvas, legend_canvas=None):
        self.canvas = canvas
        self.legend_canvas = legend_canvas
        self.wavelengths: list[float] = []
        self.values: list[float] = []
        self.series: list[PlotSeries] = []
        self.x_min: float | None = None
        self.x_max: float | None = None
        self.y_min: float | None = None
        self.y_max: float | None = None
        self.x_label = "波长 (nm)"
        self.y_label = "Counts"
        self.preferred_y_bounds: tuple[float, float] | None = None
        self.empty_title = "等待光谱数据"
        self.empty_hint = "连接仪器或进入模拟模式，然后开始连续采集或执行单次采集"
        self.cursor_callback = None
        self.cursor_index: int | None = None
        self._cursor_dragging = False
        self.crosshair_callback = None
        self.crosshair_position: tuple[float, float] | None = None
        self._crosshair_dragging = False
        self._overlay_drag_preview = False
        self.crosshair_drag_preview_enabled = True
        self.limit_callback = None
        self._plot_rect: tuple[float, float, float, float] | None = None
        self._legend_rects: list[tuple[float, float, float, float]] = []
        self._current_y_bounds: tuple[float, float] | None = None
        self._drag_start_x: int | None = None
        self._drag_start_bounds: tuple[float, float] | None = None
        self._drag_start_y: int | None = None
        self._drag_start_y_bounds: tuple[float, float] | None = None
        self._selection_start: tuple[int, int] | None = None
        self._dragging = False
        self.interaction_mode = "normal"
        self.mode_callback = None
        self._drawn_canvas_size: tuple[int, int] | None = None
        self._drawn_view_bounds: tuple[float | None, ...] | None = None
        self._pending_canvas_size: tuple[int, int] | None = None
        self._resize_draw_after_id: str | None = None
        self._interaction_draw_after_id: str | None = None
        self._legend_resize_after_id: str | None = None
        self._axis_tick_font = tkfont.Font(root=canvas, font=("Segoe UI", 9))
        self._x_title_font = tkfont.Font(root=canvas, font=("Microsoft YaHei UI", 10))
        self._y_title_font = tkfont.Font(root=canvas, font=("Microsoft YaHei UI", 10))
        self._legend_font = tkfont.Font(root=canvas, font=("Microsoft YaHei UI", 9))
        self._legend_active_font = tkfont.Font(root=canvas, font=("Microsoft YaHei UI", 9, "bold"))
        canvas.configure(cursor="arrow")
        canvas.bind("<Configure>", self.on_canvas_configure)
        canvas.bind("<Motion>", self.on_motion)
        canvas.bind("<Leave>", self.on_leave)
        canvas.bind("<MouseWheel>", self.on_mousewheel)
        canvas.bind("<ButtonPress-1>", self.on_press)
        canvas.bind("<B1-Motion>", self.on_drag)
        canvas.bind("<ButtonRelease-1>", self.on_release)
        if self.legend_canvas is not None:
            self.legend_canvas.bind("<Configure>", self.on_legend_canvas_configure)

    def on_canvas_configure(self, event) -> None:
        """Coalesce real canvas resizes and ignore position-only configure events."""
        size = (max(1, int(event.width)), max(1, int(event.height)))
        if size == self._drawn_canvas_size:
            self._pending_canvas_size = None
            return
        if size == self._pending_canvas_size:
            return
        self._pending_canvas_size = size
        if self._resize_draw_after_id is None:
            self._resize_draw_after_id = self.canvas.after(28, self.flush_canvas_resize)

    def flush_canvas_resize(self) -> None:
        self._resize_draw_after_id = None
        pending_size = self._pending_canvas_size
        self._pending_canvas_size = None
        if pending_size is None or pending_size == self._drawn_canvas_size:
            return
        self.draw()

    def on_legend_canvas_configure(self, _event) -> None:
        if self._legend_resize_after_id is None:
            self._legend_resize_after_id = self.legend_canvas.after(28, self.flush_legend_resize)

    def flush_legend_resize(self) -> None:
        self._legend_resize_after_id = None
        self.draw_legend_canvas()

    @staticmethod
    def nice_ticks(vmin: float, vmax: float, count: int = 5) -> list[float]:
        if not math.isfinite(vmin) or not math.isfinite(vmax) or vmax <= vmin:
            return [vmin]
        raw = (vmax - vmin) / max(1, count)
        magnitude = 10 ** math.floor(math.log10(abs(raw)))
        residual = raw / magnitude
        step = (1 if residual < 1.5 else 2 if residual < 3 else 5 if residual < 7 else 10) * magnitude
        start = math.ceil(vmin / step) * step
        ticks = []
        value = start
        while value <= vmax + step * 0.01:
            ticks.append(value)
            value += step
        return ticks

    @staticmethod
    def format_axis_tick(value: float, step: float) -> str:
        """Format a tick without collapsing distinct zoomed values to one label."""
        if not math.isfinite(value):
            return "—"
        if not math.isfinite(step) or step <= 0:
            return f"{value:.6g}"
        if math.isclose(value, 0.0, rel_tol=0.0, abs_tol=step * 1e-9):
            value = 0.0
        if abs(value) >= 1e7 or (value != 0.0 and abs(value) < 1e-6):
            return f"{value:.6g}"
        decimals = 0 if step >= 1.0 else min(9, max(1, int(math.ceil(-math.log10(step)))))
        return f"{value:.{decimals}f}"

    @staticmethod
    def clip_segment_to_rect(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> tuple[float, float, float, float] | None:
        """Clip a line segment to the plot rectangle without flattening off-screen data."""
        dx, dy = x2 - x1, y2 - y1
        lower, upper = 0.0, 1.0
        for p, q in ((-dx, x1 - left), (dx, right - x1), (-dy, y1 - top), (dy, bottom - y1)):
            if abs(p) < 1e-15:
                if q < 0:
                    return None
                continue
            ratio = q / p
            if p < 0:
                lower = max(lower, ratio)
            else:
                upper = min(upper, ratio)
            if lower > upper:
                return None
        return (
            x1 + lower * dx,
            y1 + lower * dy,
            x1 + upper * dx,
            y1 + upper * dy,
        )

    def set_data(
        self,
        wavelengths: list[float],
        values: list[float],
        y_label: str,
        x_label: str = "波长 (nm)",
        preferred_y_bounds: tuple[float, float] | None = None,
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.set_series(
            [PlotSeries("", tuple(wavelengths), tuple(values), self.LINE, active=True, metadata=metadata)],
            y_label,
            x_label,
            preferred_y_bounds,
        )

    def set_empty_message(self, title: str, hint: str) -> None:
        self.empty_title = title
        self.empty_hint = hint

    def set_series(
        self,
        series: list[PlotSeries] | tuple[PlotSeries, ...],
        y_label: str,
        x_label: str = "波长 (nm)",
        preferred_y_bounds: tuple[float, float] | None = None,
    ) -> None:
        preferred_bounds_changed = preferred_y_bounds != self.preferred_y_bounds
        next_series = [item for item in series if item.visible]
        style_only = (
            len(next_series) == len(self.series)
            and self._drawn_view_bounds == (self.x_min, self.x_max, self.y_min, self.y_max)
            and y_label == self.y_label
            and x_label == self.x_label
            and preferred_y_bounds == self.preferred_y_bounds
            and all(
                before.name == after.name
                and before.wavelengths == after.wavelengths
                and self.series_values_equal(before.values, after.values)
                for before, after in zip(self.series, next_series)
            )
            and bool(self.canvas.find_withtag("spectrum_line"))
        )
        self.series = next_series
        active = next((item for item in self.series if item.active), self.series[0] if self.series else None)
        self.wavelengths = list(active.wavelengths) if active is not None else []
        self.values = list(active.values) if active is not None else []
        self.x_label = x_label
        self.y_label = y_label
        self.preferred_y_bounds = preferred_y_bounds
        if preferred_bounds_changed:
            if preferred_y_bounds is None:
                self.y_min = None
                self.y_max = None
            else:
                self.y_min, self.y_max = preferred_y_bounds
        point_count = min(len(self.wavelengths), len(self.values))
        if point_count == 0:
            self.cursor_index = None
        elif self.cursor_index is not None:
            self.cursor_index = min(self.cursor_index, point_count - 1)
        if style_only:
            self.update_series_styles()
        else:
            self.draw()

    @staticmethod
    def series_values_equal(before, after) -> bool:
        return before == after or (len(before) == len(after) and all(
            first == second or (math.isnan(first) and math.isnan(second))
            for first, second in zip(before, after)
        ))

    def update_series_styles(self) -> None:
        """Update colors and active emphasis without rebuilding plot geometry."""
        legend_surface = self.legend_canvas or self.canvas
        for index, item in enumerate(self.series):
            width = self.ACTIVE_LINE_WIDTH if item.active else self.LINE_WIDTH
            self.canvas.itemconfigure(f"series_line_{index}", fill=item.color, width=width)
            self.canvas.itemconfigure(f"series_line_{index}", dash=item.dash or ())
            legend_surface.itemconfigure(
                f"legend_line_{index}",
                fill=item.color,
                width=width,
                dash=item.dash or (),
            )
            legend_surface.itemconfigure(
                f"legend_text_{index}",
                font=("Microsoft YaHei UI", 9, "bold") if item.active else ("Microsoft YaHei UI", 9),
            )
        if self.cursor_callback and self.cursor_index is not None:
            point_count = min(len(self.wavelengths), len(self.values))
            if 0 <= self.cursor_index < point_count:
                self.cursor_callback(self.wavelengths[self.cursor_index], self.values[self.cursor_index])

    def request_interaction_draw(self) -> None:
        """Coalesce dense wheel and drag events to roughly one redraw per frame."""
        if self._interaction_draw_after_id is None:
            self._interaction_draw_after_id = self.canvas.after(16, self.flush_interaction_draw)

    def flush_interaction_draw(self) -> None:
        self._interaction_draw_after_id = None
        self.draw()

    def full_x_bounds(self) -> tuple[float, float] | None:
        populated = [item for item in self.series if item.wavelengths]
        if not populated:
            return None
        return min(item.wavelengths[0] for item in populated), max(item.wavelengths[-1] for item in populated)

    def current_x_bounds(self) -> tuple[float, float] | None:
        full = self.full_x_bounds()
        if full is None:
            return None
        lo = self.x_min if self.x_min is not None else full[0]
        hi = self.x_max if self.x_max is not None else full[1]
        if hi <= lo:
            return full
        return self.clamp_x_bounds(lo, hi)

    def clamp_x_bounds(self, lo: float, hi: float) -> tuple[float, float]:
        full = self.full_x_bounds()
        if full is None:
            return lo, hi
        full_lo, full_hi = full
        full_span = full_hi - full_lo
        span = min(max(hi - lo, full_span / 1000.0), full_span)
        if lo < full_lo:
            lo = full_lo
            hi = lo + span
        if hi > full_hi:
            hi = full_hi
            lo = hi - span
        return lo, hi

    def visible_data(self) -> tuple[list[float], list[float]]:
        bounds = self.current_x_bounds()
        if bounds is None:
            return [], []
        lo, hi = bounds
        pairs = [
            point
            for item in self.series
            for point in self.visible_series_points(item, lo, hi, include_neighbors=True)
        ]
        if not pairs:
            return [], []
        xs, ys = zip(*pairs)
        return list(xs), list(ys)

    @staticmethod
    def visible_series_points(
        item: PlotSeries,
        xmin: float,
        xmax: float,
        *,
        include_neighbors: bool = False,
        finite_only: bool = True,
    ) -> list[tuple[float, float]]:
        """Slice a monotonic spectrum without changing any X/Y pairings.

        The optional adjacent points let the renderer clip the original line
        precisely at both viewport edges after zooming.
        """
        start = bisect_left(item.wavelengths, xmin)
        end = bisect_right(item.wavelengths, xmax)
        if include_neighbors:
            start = max(0, start - 1)
            end = min(len(item.wavelengths), end + 1)
        return [
            (x, y)
            for x, y in zip(item.wavelengths[start:end], item.values[start:end])
            if not finite_only or math.isfinite(y)
        ]

    def set_cursor_index(self, index: int, redraw: bool = True) -> bool:
        point_count = min(len(self.wavelengths), len(self.values))
        if point_count == 0:
            self.cursor_index = None
            return False
        self.cursor_index = min(point_count - 1, max(0, int(index)))
        if redraw:
            self.draw()
        return True

    def set_cursor_nearest_x(self, value: float, redraw: bool = True) -> bool:
        point_count = min(len(self.wavelengths), len(self.values))
        if point_count == 0 or not math.isfinite(value):
            return False
        index = min(range(point_count), key=lambda i: abs(self.wavelengths[i] - value))
        return self.set_cursor_index(index, redraw=redraw)

    def set_cursor_from_pixel(self, pixel_x: float, redraw: bool = True) -> bool:
        if self._plot_rect is None:
            return False
        bounds = self.current_x_bounds()
        if bounds is None:
            return False
        left, _top, right, _bottom = self._plot_rect
        pixel_x = min(right, max(left, pixel_x))
        lo, hi = bounds
        value = lo + (pixel_x - left) / max(1.0, right - left) * (hi - lo)
        return self.set_cursor_nearest_x(value, redraw=redraw)

    def set_crosshair_from_pixel(self, pixel_x: float, pixel_y: float, redraw: bool = True) -> bool:
        if self._plot_rect is None:
            return False
        left, top, right, bottom = self._plot_rect
        pixel_x = min(right, max(left, pixel_x))
        pixel_y = min(bottom, max(top, pixel_y))
        point = self.data_at_pixel(pixel_x, pixel_y)
        if point is None:
            return False
        self.crosshair_position = point
        if redraw:
            self.draw()
        return True

    def update_cursor_overlay(self) -> None:
        """Move only the data-cursor item; do not rebuild curves, axes, or legend."""
        point_count = min(len(self.wavelengths), len(self.values))
        if (
            self.interaction_mode == "crosshair"
            or self.cursor_index is None
            or not 0 <= self.cursor_index < point_count
            or self._plot_rect is None
        ):
            self.canvas.delete("data_cursor")
            return
        bounds = self.current_x_bounds()
        if bounds is None:
            self.canvas.delete("data_cursor")
            return
        left, top, right, bottom = self._plot_rect
        xmin, xmax = bounds
        cursor_x_value = self.wavelengths[self.cursor_index]
        cursor_y_value = self.values[self.cursor_index]
        if xmin <= cursor_x_value <= xmax:
            x = left + (cursor_x_value - xmin) / (xmax - xmin or 1.0) * (right - left)
            if self.canvas.find_withtag("data_cursor"):
                self.canvas.coords("data_cursor", x, top, x, bottom)
            else:
                self.canvas.create_line(
                    x, top, x, bottom, fill=self.CURSOR, width=1.5, tags="data_cursor"
                )
        else:
            self.canvas.delete("data_cursor")
        if self.cursor_callback:
            self.cursor_callback(cursor_x_value, cursor_y_value)

    def update_crosshair_overlay(self) -> None:
        """Move only the three crosshair items for low-latency PL interaction."""
        if (
            self.interaction_mode != "crosshair"
            or self.crosshair_position is None
            or self._plot_rect is None
            or self._current_y_bounds is None
        ):
            self.canvas.delete("plot_crosshair")
            return
        bounds = self.current_x_bounds()
        if bounds is None:
            self.canvas.delete("plot_crosshair")
            return
        left, top, right, bottom = self._plot_rect
        xmin, xmax = bounds
        ymin, ymax = self._current_y_bounds
        x_value, y_value = self.crosshair_position
        if xmin <= x_value <= xmax and ymin <= y_value <= ymax:
            x = left + (x_value - xmin) / (xmax - xmin or 1.0) * (right - left)
            y = bottom - (y_value - ymin) / (ymax - ymin or 1.0) * (bottom - top)
            vertical = (x, top, x, bottom)
            horizontal = (left, y, right, y)
            if self._overlay_drag_preview:
                vertical = (x, max(top, y - 22), x, min(bottom, y + 22))
                horizontal = (max(left, x - 22), y, min(right, x + 22), y)
            if self.canvas.find_withtag("crosshair_vertical"):
                self.canvas.coords("crosshair_vertical", *vertical)
                self.canvas.coords("crosshair_horizontal", *horizontal)
                self.canvas.coords("crosshair_center", x - 4, y - 4, x + 4, y + 4)
            else:
                self.canvas.create_line(
                    *vertical, fill=self.CURSOR, width=1.5,
                    tags=("plot_crosshair", "crosshair_vertical"),
                )
                self.canvas.create_line(
                    *horizontal, fill=self.CURSOR, width=1.5,
                    tags=("plot_crosshair", "crosshair_horizontal"),
                )
                self.canvas.create_oval(
                    x - 4, y - 4, x + 4, y + 4, outline=self.CURSOR, width=1.5,
                    tags=("plot_crosshair", "crosshair_center"),
                )
        else:
            self.canvas.delete("plot_crosshair")
        if self.crosshair_callback:
            self.crosshair_callback(x_value, y_value)

    def step_cursor(self, direction: int) -> bool:
        point_count = min(len(self.wavelengths), len(self.values))
        if point_count == 0:
            return False
        if self.cursor_index is None:
            bounds = self.current_x_bounds()
            value = (bounds[0] + bounds[1]) / 2.0 if bounds else self.wavelengths[0]
            return self.set_cursor_nearest_x(value)
        target = min(point_count - 1, max(0, self.cursor_index + (1 if direction > 0 else -1)))
        changed = target != self.cursor_index
        self.set_cursor_index(target)
        return changed

    @staticmethod
    def peak_downsample(
        points: list[tuple[float, float]], max_points: int
    ) -> list[tuple[float, float]]:
        """Reduce dense lines while retaining each bucket's minimum and maximum."""
        if len(points) <= max_points or max_points < 4:
            return points
        interior = points[1:-1]
        bucket_count = max(1, (max_points - 2) // 2)
        bucket_size = max(1, math.ceil(len(interior) / bucket_count))
        reduced = [points[0]]
        for start in range(0, len(interior), bucket_size):
            bucket = interior[start : start + bucket_size]
            low_index = min(range(len(bucket)), key=lambda index: bucket[index][1])
            high_index = max(range(len(bucket)), key=lambda index: bucket[index][1])
            for index in sorted({low_index, high_index}):
                reduced.append(bucket[index])
        reduced.append(points[-1])
        return reduced

    def reset_view(self) -> None:
        """Restore the maximum meaningful view, equivalent to OceanView ZoomOutMax."""
        self.x_min = None
        self.x_max = None
        if self.preferred_y_bounds is None:
            self.y_min = None
            self.y_max = None
        else:
            self.y_min, self.y_max = self.preferred_y_bounds
        self.draw()

    def reset_y(self) -> None:
        self.y_min = None
        self.y_max = None
        self.draw()

    def fit_both(self) -> None:
        """Fit both axes to the complete spectrum currently loaded."""
        full = self.full_x_bounds()
        if full is not None:
            self.x_min, self.x_max = full
        self.y_min = None
        self.y_max = None
        self.draw()

    def set_interaction_mode(self, mode: str) -> None:
        if mode not in {"normal", "zoom_in", "zoom_out", "select", "drag", "crosshair"}:
            raise ValueError(f"Unsupported plot interaction mode: {mode}")
        self.interaction_mode = mode
        self._drag_start_x = None
        self._drag_start_y = None
        self._drag_start_bounds = None
        self._drag_start_y_bounds = None
        self._selection_start = None
        self._cursor_dragging = False
        self._crosshair_dragging = False
        self._overlay_drag_preview = False
        self._dragging = False
        cursor = "fleur" if mode == "drag" else "crosshair" if mode != "normal" else "arrow"
        self.canvas.configure(cursor=cursor)
        if self.mode_callback:
            self.mode_callback(mode)

    def zoom_x(self, factor: float, center: float | None = None, redraw: bool = True) -> None:
        bounds = self.current_x_bounds()
        if bounds is None:
            return
        lo, hi = bounds
        center = center if center is not None else (lo + hi) / 2.0
        new_lo = center - (center - lo) * factor
        new_hi = center + (hi - center) * factor
        self.x_min, self.x_max = self.clamp_x_bounds(new_lo, new_hi)
        if redraw:
            self.draw()

    def zoom_y(self, factor: float, center: float | None = None) -> None:
        if self._current_y_bounds is None:
            self.draw()
        if self._current_y_bounds is None:
            return
        lo, hi = self._current_y_bounds
        center = center if center is not None else (lo + hi) / 2.0
        half = max((hi - lo) * factor / 2.0, 1e-12)
        self.y_min = center - half
        self.y_max = center + half
        self.draw()

    def maximum_y_bounds(self) -> tuple[float, float] | None:
        if self.preferred_y_bounds is not None:
            return self.preferred_y_bounds
        finite = [value for item in self.series for value in item.values if math.isfinite(value)]
        if not finite:
            return None
        lo, hi = min(finite), max(finite)
        if hi <= lo:
            hi = lo + 1.0
        span = hi - lo
        return lo - span * 0.1, hi + span * 0.1

    @staticmethod
    def clamp_bounds(lo: float, hi: float, limit_lo: float, limit_hi: float) -> tuple[float, float]:
        limit_span = limit_hi - limit_lo
        span = hi - lo
        if span >= limit_span:
            return limit_lo, limit_hi
        if lo < limit_lo:
            lo = limit_lo
            hi = lo + span
        if hi > limit_hi:
            hi = limit_hi
            lo = hi - span
        return lo, hi

    def zoom_both(self, factor: float, center_x: float, center_y: float) -> bool:
        x_bounds = self.current_x_bounds()
        if x_bounds is None:
            return False
        if self._current_y_bounds is None:
            self.draw()
        if self._current_y_bounds is None:
            return False
        xlo, xhi = x_bounds
        ylo, yhi = self._current_y_bounds
        new_x = self.clamp_x_bounds(
            center_x - (center_x - xlo) * factor,
            center_x + (xhi - center_x) * factor,
        )
        new_y = (
            center_y - (center_y - ylo) * factor,
            center_y + (yhi - center_y) * factor,
        )
        if factor > 1.0:
            maximum_y = self.maximum_y_bounds()
            if maximum_y is not None:
                new_y = self.clamp_bounds(*new_y, *maximum_y)
        changed = any(
            not math.isclose(before, after, rel_tol=1e-10, abs_tol=1e-10)
            for before, after in zip((xlo, xhi, ylo, yhi), (*new_x, *new_y))
        )
        self.x_min, self.x_max = new_x
        self.y_min, self.y_max = new_y
        self.draw()
        return changed

    def pan_x(self, fraction: float) -> None:
        bounds = self.current_x_bounds()
        if bounds is None:
            return
        lo, hi = bounds
        shift = (hi - lo) * fraction
        self.x_min, self.x_max = self.clamp_x_bounds(lo + shift, hi + shift)
        self.draw()

    def _build_legend_layout(self, available_width: int, available_height: int):
        legend_items = [(index, item) for index, item in enumerate(self.series) if item.name]
        if not legend_items:
            return None
        normal_font = self._legend_font
        active_font = self._legend_active_font
        row_height = max(
            24,
            normal_font.metrics("linespace") + 8,
            active_font.metrics("linespace") + 8,
        )
        available_width = max(1, int(available_width))
        available_height = max(1, int(available_height))
        # A normal legend floats outside the plot canvas in the application.
        # Keep the common two/three-series history on one row and wrap only
        # larger lists when the legend host is short or narrow.
        row_hint = 1 if len(legend_items) <= 3 else max(
            1,
            min(3, int(max(1, (available_height - 8) // row_height))),
        )
        column_gap = 8
        desired_columns = max(1, math.ceil(len(legend_items) / row_hint))
        columns_that_fit = max(1, int((available_width + column_gap) // (96 + column_gap)))
        column_count = min(desired_columns, columns_that_fit)
        max_rows = max(1, math.ceil(len(legend_items) / column_count))
        column_budget = max(
            64,
            int((available_width - column_gap * (column_count - 1)) / column_count),
        )
        # Leave a few pixels for Canvas text bounds and the right padding on
        # older Tk builds used by the Win7 package.
        max_text_width = max(32, column_budget - 62)

        def display_name(item: PlotSeries) -> str:
            font = active_font if item.active else normal_font
            if font.measure(item.name) <= max_text_width:
                return item.name
            low, high = 1, len(item.name)
            best = "…"
            while low <= high:
                keep = (low + high) // 2
                candidate = item.name[:keep] + "…"
                if font.measure(candidate) <= max_text_width:
                    best = candidate
                    low = keep + 1
                else:
                    high = keep - 1
            return best

        columns = []
        for start in range(0, len(legend_items), max_rows):
            column_entries = []
            for series_index, item in legend_items[start : start + max_rows]:
                label = display_name(item)
                font = active_font if item.active else normal_font
                column_entries.append((series_index, item, label, font.measure(label) + 58))
            columns.append(column_entries)
        legend_height = max(len(column) for column in columns) * row_height + 8
        legend_width = min(
            available_width + 16,
            sum(max((entry[3] for entry in column), default=0) for column in columns)
            + column_gap * max(0, len(columns) - 1)
            + 16,
        )
        return columns, row_height, column_gap, legend_width, legend_height

    def _render_legend(self, surface, layout, left: float, top: float, right: float, bottom: float) -> None:
        if layout is None:
            return
        columns, row_height, column_gap, legend_width, legend_height = layout
        legend_right = min(right, left + legend_width)
        legend_bottom = min(bottom, top + legend_height)
        surface.create_rectangle(
            left,
            top,
            legend_right,
            legend_bottom,
            fill="#f8fbff",
            outline="#cbd5e1",
            tags="plot_legend",
        )
        if surface is self.canvas:
            self._legend_rects.append((left, top, legend_right, legend_bottom))
        legend_x = left + 8
        normal_font = self._legend_font
        active_font = self._legend_active_font
        for column in columns:
            column_width = max((entry[3] for entry in column), default=0)
            for row, (series_index, item, label, _item_width) in enumerate(column):
                y = top + 4 + row * row_height + row_height / 2
                surface.create_line(
                    legend_x + 2,
                    y,
                    legend_x + 26,
                    y,
                    fill=item.color,
                    width=self.ACTIVE_LINE_WIDTH if item.active else self.LINE_WIDTH,
                    dash=item.dash or (),
                    tags=("plot_legend", "plot_legend_line", f"legend_line_{series_index}"),
                )
                surface.create_text(
                    legend_x + 34,
                    y,
                    text=label,
                    fill=self.AXIS,
                    anchor="w",
                    font=active_font if item.active else normal_font,
                    tags=("plot_legend", "plot_legend_text", f"legend_text_{series_index}"),
                )
            legend_x += column_width + column_gap

    def draw_legend_canvas(self) -> None:
        if self.legend_canvas is None:
            return
        surface = self.legend_canvas
        surface.delete("all")
        self._legend_rects = []
        width = max(1, surface.winfo_width())
        height = max(1, surface.winfo_height())
        layout = self._build_legend_layout(width - 16, height - 8)
        if layout is None:
            return
        legend_height = layout[4]
        legend_top = max(2, (height - legend_height) / 2)
        self._render_legend(surface, layout, 8, legend_top, width - 8, height - 2)

    def draw(self) -> None:
        if self._interaction_draw_after_id is not None:
            self.canvas.after_cancel(self._interaction_draw_after_id)
            self._interaction_draw_after_id = None
        c = self.canvas
        self._drawn_view_bounds = (self.x_min, self.x_max, self.y_min, self.y_max)
        c.delete("all")
        self._legend_rects = []
        width = max(240, c.winfo_width())
        height = max(180, c.winfo_height())
        self._drawn_canvas_size = (max(1, c.winfo_width()), max(1, c.winfo_height()))
        c.configure(bg=self.BG)
        axis_tick_font = self._axis_tick_font
        x_title_font = self._x_title_font
        y_title_font = self._y_title_font
        x_tick_linespace = axis_tick_font.metrics("linespace")
        x_title_linespace = x_title_font.metrics("linespace")
        bottom_margin = x_tick_linespace + x_title_linespace + 28
        top, right, bottom = 24, width - 22, height - bottom_margin
        x_tick_y = bottom + 6 + x_tick_linespace / 2
        x_title_y = bottom + 6 + x_tick_linespace + 8 + x_title_linespace / 2

        xs, ys = self.visible_data()
        if not xs:
            left = self.PLOT_LEFT
            self._plot_rect = (left, top, right, bottom)
            c.create_rectangle(left, top, right, bottom, fill=self.BG, outline=self.BORDER)
            c.create_text(width / 2, height / 2 - 10, text=self.empty_title, fill="#334155", font=("Microsoft YaHei UI", 16, "bold"))
            c.create_text(
                width / 2,
                height / 2 + 22,
                text=self.empty_hint,
                fill="#64748b",
                font=("Microsoft YaHei UI", 10),
            )
            self.draw_legend_canvas()
            return

        x_bounds = self.current_x_bounds()
        if x_bounds is None:
            return
        xmin, xmax = x_bounds
        data_ymin, data_ymax = min(ys), max(ys)
        minimum_margin = 1.0 if "counts" in self.y_label.lower() else 1e-6
        if data_ymax <= data_ymin:
            data_ymax = data_ymin + max(abs(data_ymin) * 0.02, minimum_margin)
        data_span = data_ymax - data_ymin
        margin = max(data_span * 0.06, abs(data_ymax) * 0.02, minimum_margin)
        if self.y_min is not None and self.y_max is not None and self.y_max > self.y_min:
            ymin, ymax = self.y_min, self.y_max
        elif data_ymin >= 0:
            ymin, ymax = 0.0, data_ymax + margin
        elif data_ymax <= 0:
            ymin, ymax = data_ymin - margin, 0.0
        else:
            ymin, ymax = data_ymin - margin, data_ymax + margin
        self._current_y_bounds = (ymin, ymax)
        y_ticks = self.nice_ticks(ymin, ymax, 5)
        y_tick_labels = [f"{tick:.3g}" for tick in y_ticks]
        widest_y_tick = max((axis_tick_font.measure(label) for label in y_tick_labels), default=0)
        y_title_right = self.Y_TITLE_X + y_title_font.metrics("linespace") / 2
        left = max(self.PLOT_LEFT, int(y_title_right + 22 + widest_y_tick + 12))
        left = min(left, right - 100)

        legend_layout = None
        if self.legend_canvas is None:
            legend_layout = self._build_legend_layout(right - left - 16, bottom - top)

        self._plot_rect = (left, top, right, bottom)
        c.create_rectangle(left, top, right, bottom, fill=self.BG, outline=self.BORDER)

        def px(x):
            return left + (x - xmin) / (xmax - xmin or 1) * (right - left)

        def py(y):
            return bottom - (y - ymin) / (ymax - ymin or 1) * (bottom - top)

        x_ticks = self.nice_ticks(xmin, xmax, 7)
        x_tick_step = (
            min(second - first for first, second in zip(x_ticks, x_ticks[1:]))
            if len(x_ticks) >= 2
            else (xmax - xmin) / 7.0
        )
        for tick in x_ticks:
            x = px(tick)
            c.create_line(x, top, x, bottom, fill=self.GRID, dash=(4, 4))
            c.create_text(
                x,
                x_tick_y,
                text=self.format_axis_tick(tick, x_tick_step),
                fill=self.AXIS,
                font=axis_tick_font,
                tags="x_tick_label",
            )
        for tick, label in zip(y_ticks, y_tick_labels):
            y = py(tick)
            c.create_line(left, y, right, y, fill=self.GRID, dash=(4, 4))
            c.create_text(
                left - 12,
                y,
                text=label,
                fill=self.AXIS,
                anchor="e",
                font=axis_tick_font,
                tags="y_tick_label",
            )

        max_points = max(300, int(right - left) // 2)
        for series_index, item in enumerate(self.series):
            visible = self.visible_series_points(
                item,
                xmin,
                xmax,
                include_neighbors=True,
                finite_only=False,
            )
            finite_runs: list[list[tuple[float, float]]] = []
            current_run: list[tuple[float, float]] = []
            for point in visible:
                if math.isfinite(point[1]):
                    current_run.append(point)
                else:
                    if current_run:
                        finite_runs.append(current_run)
                    current_run = []
            if current_run:
                finite_runs.append(current_run)

            paths: list[list[float]] = []
            for run in finite_runs:
                points = [(px(x), py(y)) for x, y in self.peak_downsample(run, max_points)]
                current_path: list[float] = []
                for (x1, y1), (x2, y2) in zip(points, points[1:]):
                    clipped = self.clip_segment_to_rect(x1, y1, x2, y2, left, top, right, bottom)
                    if clipped is None:
                        if len(current_path) >= 4:
                            paths.append(current_path)
                        current_path = []
                        continue
                    cx1, cy1, cx2, cy2 = clipped
                    if current_path and (
                        abs(current_path[-2] - cx1) > 0.5 or abs(current_path[-1] - cy1) > 0.5
                    ):
                        if len(current_path) >= 4:
                            paths.append(current_path)
                        current_path = []
                    if not current_path:
                        current_path.extend((cx1, cy1))
                    current_path.extend((cx2, cy2))
                if len(current_path) >= 4:
                    paths.append(current_path)
            for path in paths:
                c.create_line(
                    *path,
                    fill=item.color,
                    width=self.ACTIVE_LINE_WIDTH if item.active else self.LINE_WIDTH,
                    dash=item.dash or (),
                    smooth=False,
                    tags=("spectrum_line", f"series_line_{series_index}"),
                )

        if self.legend_canvas is not None:
            self.draw_legend_canvas()
        elif legend_layout:
            self._render_legend(c, legend_layout, left, top, right, bottom)

        c.create_text(
            (left + right) / 2,
            x_title_y,
            text=self.x_label,
            fill=self.AXIS,
            font=x_title_font,
            tags="x_axis_title",
        )
        c.create_text(
            self.Y_TITLE_X,
            (top + bottom) / 2,
            text=self.y_label,
            fill=self.AXIS,
            angle=90,
            font=y_title_font,
            tags="y_axis_title",
        )

        self.update_cursor_overlay()
        self.update_crosshair_overlay()

    def _point_in_legend(self, x: float, y: float) -> bool:
        return any(
            left <= x <= right and top <= y <= bottom
            for left, top, right, bottom in self._legend_rects
        )

    def on_motion(self, event) -> None:
        return

    def on_leave(self, _event) -> None:
        return

    def data_at_pixel(self, x: float, y: float) -> tuple[float, float] | None:
        if self._plot_rect is None or self._current_y_bounds is None:
            return None
        x_bounds = self.current_x_bounds()
        if x_bounds is None:
            return None
        left, top, right, bottom = self._plot_rect
        if not (left <= x <= right and top <= y <= bottom) or self._point_in_legend(x, y):
            return None
        xlo, xhi = x_bounds
        ylo, yhi = self._current_y_bounds
        data_x = xlo + (x - left) / max(1.0, right - left) * (xhi - xlo)
        data_y = yhi - (y - top) / max(1.0, bottom - top) * (yhi - ylo)
        return data_x, data_y

    def on_mousewheel(self, event) -> None:
        if self._plot_rect is None or not self.wavelengths:
            return
        left, top, right, bottom = self._plot_rect
        if not (left <= event.x <= right and top <= event.y <= bottom):
            return
        bounds = self.current_x_bounds()
        if bounds is None:
            return
        lo, hi = bounds
        center = lo + (event.x - left) / (right - left) * (hi - lo)
        self.zoom_x(0.8 if event.delta > 0 else 1.25, center=center, redraw=False)
        self.request_interaction_draw()

    def on_press(self, event) -> None:
        if self._plot_rect is None or not self.wavelengths:
            return
        left, top, right, bottom = self._plot_rect
        if left <= event.x <= right and top <= event.y <= bottom and not self._point_in_legend(event.x, event.y):
            if self.interaction_mode == "normal":
                self._cursor_dragging = True
                self.canvas.configure(cursor="sb_h_double_arrow")
                self.set_cursor_from_pixel(event.x, redraw=False)
                self.update_cursor_overlay()
                return
            if self.interaction_mode == "crosshair":
                self._crosshair_dragging = True
                self._overlay_drag_preview = self.crosshair_drag_preview_enabled
                self.set_crosshair_from_pixel(event.x, event.y, redraw=False)
                self.update_crosshair_overlay()
                return
            point = self.data_at_pixel(event.x, event.y)
            if point is None:
                return
            if self.interaction_mode in {"zoom_in", "zoom_out"}:
                changed = self.zoom_both(0.75 if self.interaction_mode == "zoom_in" else 1.35, *point)
                if not changed and self.interaction_mode == "zoom_out" and self.limit_callback:
                    self.limit_callback()
                return
            if self.interaction_mode == "select":
                self._selection_start = (event.x, event.y)
                self.canvas.delete("zoom_selection")
                self.canvas.create_rectangle(
                    event.x,
                    event.y,
                    event.x,
                    event.y,
                    outline=self.CURSOR,
                    width=2,
                    dash=(5, 3),
                    fill="",
                    tags="zoom_selection",
                )
                return
            self._drag_start_x = event.x
            self._drag_start_y = event.y
            self._drag_start_bounds = self.current_x_bounds()
            self._drag_start_y_bounds = self._current_y_bounds
            self._dragging = False

    def on_drag(self, event) -> None:
        if self._plot_rect is None:
            return
        left, top, right, bottom = self._plot_rect
        if self.interaction_mode == "normal" and self._cursor_dragging:
            self.set_cursor_from_pixel(event.x, redraw=False)
            self.update_cursor_overlay()
            return
        if self.interaction_mode == "crosshair" and self._crosshair_dragging:
            self.set_crosshair_from_pixel(event.x, event.y, redraw=False)
            self.update_crosshair_overlay()
            return
        if self.interaction_mode == "select" and self._selection_start is not None:
            start_x, start_y = self._selection_start
            end_x = min(right, max(left, event.x))
            end_y = min(bottom, max(top, event.y))
            self._dragging = abs(end_x - start_x) > 3 or abs(end_y - start_y) > 3
            self.canvas.coords("zoom_selection", start_x, start_y, end_x, end_y)
            return
        if (
            self.interaction_mode != "drag"
            or self._drag_start_x is None
            or self._drag_start_y is None
            or self._drag_start_bounds is None
            or self._drag_start_y_bounds is None
        ):
            return
        if abs(event.x - self._drag_start_x) > 2:
            self._dragging = True
        lo, hi = self._drag_start_bounds
        shift = -(event.x - self._drag_start_x) / max(1.0, right - left) * (hi - lo)
        self.x_min, self.x_max = self.clamp_x_bounds(lo + shift, hi + shift)
        ylo, yhi = self._drag_start_y_bounds
        y_shift = (event.y - self._drag_start_y) / max(1.0, bottom - top) * (yhi - ylo)
        self.y_min, self.y_max = ylo + y_shift, yhi + y_shift
        self.request_interaction_draw()

    def on_release(self, event) -> None:
        if self.interaction_mode == "normal" and self._cursor_dragging:
            self.set_cursor_from_pixel(event.x, redraw=False)
            self._overlay_drag_preview = False
            self.update_cursor_overlay()
            self._cursor_dragging = False
            self.canvas.configure(cursor="arrow")
            return
        if self.interaction_mode == "crosshair" and self._crosshair_dragging:
            self.set_crosshair_from_pixel(event.x, event.y, redraw=False)
            self._overlay_drag_preview = False
            self.update_crosshair_overlay()
            self._crosshair_dragging = False
            self.canvas.configure(cursor="crosshair")
            return
        was_dragging = self._dragging
        if self.interaction_mode == "select" and self._selection_start is not None and self._plot_rect is not None:
            left, top, right, bottom = self._plot_rect
            start_x, start_y = self._selection_start
            end_x = min(right, max(left, event.x))
            end_y = min(bottom, max(top, event.y))
            start = self.data_at_pixel(start_x, start_y)
            end = self.data_at_pixel(end_x, end_y)
            if was_dragging and start is not None and end is not None:
                self.x_min, self.x_max = self.clamp_x_bounds(min(start[0], end[0]), max(start[0], end[0]))
                self.y_min, self.y_max = min(start[1], end[1]), max(start[1], end[1])
            self._selection_start = None
        self._drag_start_x = None
        self._drag_start_y = None
        self._drag_start_bounds = None
        self._drag_start_y_bounds = None
        self._dragging = False
        cursor = "fleur" if self.interaction_mode == "drag" else "crosshair" if self.interaction_mode != "normal" else "arrow"
        self.canvas.configure(cursor=cursor)
        self.draw()
