from __future__ import annotations

import math
from dataclasses import replace

from ..config import APP_VERSION
from ..domain.results import PlotSeries
from ..domain.result_processing import photon_energy_ev
from .dialogs import center_dialog, close_modal
from .plot import SpectrumPlot
from .tauc_modes import TAUC_OPTIONS, normalize_tauc_mode, tauc_exponent, tauc_label


class SignalOffsetDialog:
    def __init__(self, app):
        self.app = app
        self.service = app.historical_absorbance_service
        self.group_id = self.service.current_group_id
        self.signals = self.service.group_signals()
        tk, ttk = app.tk, app.ttk
        self.window = tk.Toplevel(app.root)
        self.window.title(f"OceanLab {APP_VERSION} · {self.service.current_group.name} · 信号校正")
        self.window.transient(app.root)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda _event: self.close())
        self.selection = tk.StringVar(value='')
        self.target = tk.StringVar(value='')
        self.estimated = tk.BooleanVar(value=True)
        self.info = tk.StringVar(value='')
        self.source = tk.StringVar(value='')
        self.summary = tk.StringVar(value='')
        self.state = tk.StringVar(value='')
        self.file_name = tk.StringVar(value='')
        self.mode = tk.StringVar(value=normalize_tauc_mode(app.ab_mode_var.get()))
        self.axis = tk.StringVar(value=app.ab_x_axis_var.get())
        self.chart_context = tk.StringVar(value='')
        self.show_original = tk.BooleanVar(value=False)
        self.suggested_target = None
        self.suggested_source = ''
        self.correction = None
        shell = ttk.Frame(self.window, padding=16)
        shell.pack(fill='both', expand=True)
        header = ttk.Frame(shell)
        header.pack(fill='x', pady=(0, 10))
        ttk.Label(header, text='信号校正', font=('Microsoft YaHei UI', 13, 'bold')).pack(side='left')
        ttk.Label(header, textvariable=self.state).pack(side='right')
        footer = ttk.Frame(shell)
        footer.pack(side='bottom', fill='x', pady=(12, 0))
        self.restore_button = ttk.Button(footer, text='恢复此信号原值', command=self.restore)
        self.restore_button.pack(side='left')
        self.apply_button = ttk.Button(footer, text='应用修改并关闭', command=self.apply, state='disabled')
        self.apply_button.pack(side='right')
        ttk.Button(footer, text='返回主图', command=self.close).pack(side='right', padx=8)
        ttk.Label(shell, textvariable=self.summary, wraplength=1020).pack(side='bottom', fill='x', pady=(8, 0))
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        width = min(1120, self.window.winfo_screenwidth() - 60)
        self.parameter_width = min(320, max(260, int(width * .28)))
        self.parameters = ttk.Frame(body, width=self.parameter_width)
        self.parameters.pack(side='left', fill='y', padx=(0, 16))
        self.parameters.pack_propagate(False)
        ttk.Label(self.parameters, text='校正对象').pack(anchor='w')
        self.combo = ttk.Combobox(self.parameters, textvariable=self.selection, state='readonly',
                                 values=tuple('BG 参考' if key == 'BG' else f'样品 {i}: {name}'
                                              for i, (key, name, _s) in enumerate(self.signals)))
        self.combo.pack(fill='x', pady=(5, 8))
        self.combo.bind('<<ComboboxSelected>>', self.select_signal)
        ttk.Label(self.parameters, textvariable=self.file_name, wraplength=self.parameter_width,
                  style='Hint.TLabel').pack(anchor='w', fill='x', pady=(0, 12))
        ttk.Label(self.parameters, textvariable=self.info, wraplength=self.parameter_width).pack(anchor='w', fill='x')
        ttk.Separator(self.parameters).pack(fill='x', pady=12)
        ttk.Label(self.parameters, text='基准平台 (counts)').pack(anchor='w')
        self.target_entry = ttk.Entry(self.parameters, textvariable=self.target)
        self.target_entry.pack(fill='x', pady=(5, 8))
        ttk.Checkbutton(self.parameters, text='基准为估算值', variable=self.estimated).pack(anchor='w')
        self.preview_button = ttk.Button(self.parameters, text='更新预览', command=self.preview)
        self.preview_button.pack(fill='x', pady=(10, 12))
        ttk.Label(self.parameters, textvariable=self.source, wraplength=self.parameter_width,
                  style='Hint.TLabel').pack(anchor='w', fill='x')
        ttk.Label(self.parameters, text='只调整本组计算副本。\n原文件保持不变。\n计算：原强度 + 偏移量。',
                  wraplength=self.parameter_width, style='Hint.TLabel').pack(side='bottom', anchor='w', fill='x', pady=(12, 0))
        plot_shell = ttk.Frame(body)
        plot_shell.pack(side='left', fill='both', expand=True)
        plot_controls = ttk.Frame(plot_shell)
        plot_controls.pack(fill='x', pady=(0, 6))
        ttk.Label(plot_controls, text='显示').pack(side='left')
        self.mode_combo = ttk.Combobox(plot_controls, textvariable=self.mode, state='readonly', width=17,
                                      values=('原始信号', '透过率 (%)', '吸光度 (A)', *TAUC_OPTIONS))
        self.mode_combo.pack(side='left', padx=(8, 12))
        self.mode_combo.bind('<<ComboboxSelected>>', self.preview)
        ttk.Checkbutton(plot_controls, text='显示校正前对照', variable=self.show_original,
                        command=self.preview).pack(side='left')
        self.axis_combo = ttk.Combobox(plot_controls, textvariable=self.axis, state='readonly', width=17,
                                      values=('波长 (nm)', '光子能量 (eV)'))
        self.axis_combo.pack(side='right')
        self.axis_combo.bind('<<ComboboxSelected>>', self.preview)
        ttk.Label(plot_shell, textvariable=self.chart_context, style='Hint.TLabel',
                  wraplength=650).pack(fill='x', pady=(0, 6))
        legend = tk.Canvas(plot_shell, height=36, highlightthickness=0)
        legend.pack(fill='x')
        canvas = tk.Canvas(plot_shell, height=400, highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        self.plot = SpectrumPlot(canvas, legend)
        self.plots = [self.plot]
        self.target.trace_add('write', self.invalidate_preview)
        self.estimated.trace_add('write', self.invalidate_preview)
        height = min(760, self.window.winfo_screenheight() - 90)
        self.window.minsize(min(width, 920), min(height, 620))
        center_dialog(app.root, self.window, width, height)
        self.window.grab_set()
        self.combo.current(0)
        self.select_signal()

    def invalidate_preview(self, *_args, clear=True):
        self.correction = None
        self.apply_button.configure(state='disabled')
        if clear:
            self.state.set('参数修改尚未应用')
            self.summary.set('点击“更新预览”查看修改结果；主图保持已应用的校正。')
            self.plot.set_empty_message('参数已修改', '点击左侧“更新预览”')
            self.plot.set_series([], '计算结果')

    def signal(self):
        return self.signals[self.combo.current()]

    def select_signal(self, _event=None):
        key, _name, spectrum = self.signal()
        assessment, self.suggested_target, self.suggested_source = self.service.offset_suggestion(key)
        existing = self.service.current_group.offsets.get(key)
        self.target.set(f'{existing.target:.12g}' if existing else
                        f'{self.suggested_target:.12g}' if self.suggested_target is not None else '')
        self.estimated.set(existing.estimated if existing else True)
        self.file_name.set(spectrum.source_path.name)
        platform = f'{assessment.plateau:.4f}' if assessment.plateau is not None else '未检出'
        self.info.set(f'原平台  {platform}\n原负值  {assessment.negative_points} 点')
        self.restore_button.configure(state='normal' if existing else 'disabled')
        self.source.set(existing.source if existing else self.suggested_source)
        self.preview()

    def preview(self, _event=None):
        self.invalidate_preview(clear=False)
        key, _name, spectrum = self.signal()
        if self.service.current_group_id != self.group_id:
            self.state.set('分析组已改变')
            self.summary.set('请返回主图后重新打开此窗口。')
            self.plot.set_series([], '计算结果')
            return None
        try:
            target = float(self.target.get())
            automatic = self.suggested_target is not None and math.isclose(target, self.suggested_target, rel_tol=0, abs_tol=1e-6)
            source = self.suggested_source if automatic else '用户输入的基准平台'
            applied = self.service.current_group.offsets.get(key)
            correction = self.service.make_offset(key, target, source, self.estimated.get())
            matches = (applied is not None and math.isclose(applied.delta, correction.delta, rel_tol=0, abs_tol=1e-6)
                       and applied.estimated == correction.estimated)
            if matches:
                correction = applied
        except (ValueError, OverflowError) as exc:
            self.state.set('无法预览校正')
            self.summary.set(str(exc) if self.target.get().strip() else '没有可靠的平台基准，请填写已确认的数值。')
            self.plot.set_empty_message('没有有效校正预览', self.summary.get())
            self.plot.set_series([], '计算结果')
            return None
        source_display = ('本组同仪器、同采集参数的光谱平台（估算）'
                          if correction.source.startswith('同组匹配采集参数的平台估算：') else correction.source)
        self.source.set('基准来源\n' + source_display)
        corrected = tuple(value + correction.delta for value in spectrum.raw)
        mode = self.mode.get()
        energy = self.axis.get() == '光子能量 (eV)' and mode != '原始信号'
        self.axis_combo.configure(state='disabled' if mode == '原始信号' else 'readonly')
        if mode == '原始信号':
            old_item = PlotSeries('校正前', spectrum.wavelengths, spectrum.raw, '#3274a1')
            new_item = PlotSeries('已应用校正' if matches else '校正预览', spectrum.wavelengths, corrected, '#16866e', active=True)
            label = '强度 (counts)'
            self.chart_context.set('信号：' + ('BG 参考' if key == 'BG' else _name))
        else:
            before = dict(self.service.current_group.offsets)
            before.pop(key, None)
            after = dict(before)
            after[key] = correction
            exponent = tauc_exponent(mode)
            arguments = {'tauc': exponent is not None, 'tauc_exponent': exponent if exponent is not None else 2.0}
            old = self.service.build_plot(mode != '透过率 (%)', correction_overrides=before, **arguments)
            new = self.service.build_plot(mode != '透过率 (%)', correction_overrides=after, **arguments)
            group = self.service.current_group
            entry = next((entry for entry in group.entries if entry.entry_id == key), None)
            if entry is None:
                entry = next((entry for entry in group.entries if entry.entry_id == self.service.active_id and entry.visible),
                             next((entry for entry in group.entries if entry.visible), None))
            old_item = new_item = None
            self.chart_context.set('本组尚无可见样品；可切换“原始信号”查看所选信号。')
            if entry is not None:
                self.chart_context.set('计算样品：' + entry.display_name)
                for result, title, color in ((old, '校正前', '#3274a1'),
                                              (new, '已应用校正' if matches else '校正预览', '#16866e')):
                    item = next((item for item in result.series if dict(item.metadata).get('Source file') == str(entry.spectrum.source_path)), None)
                    if item:
                        if energy:
                            points = sorted((photon_energy_ev(x), y) for x, y in zip(item.wavelengths, item.values)
                                            if math.isfinite(photon_energy_ev(x)))
                            item = replace(item, wavelengths=tuple(x for x, _y in points), values=tuple(y for _x, y in points))
                        item = replace(item, name=title, color=color, active=color == '#16866e')
                        if result is old:
                            old_item = item
                        else:
                            new_item = item
            label = tauc_label(exponent) if exponent is not None else mode
        displayed = ([old_item] if self.show_original.get() and old_item else []) + ([new_item] if new_item else [])
        if new_item is not None and mode != '原始信号':
            invalid = sum(not math.isfinite(value) for value in new_item.values)
            self.chart_context.set(self.chart_context.get() + f'  ·  无效点 {invalid}')
        self.plot.x_min = self.plot.x_max = self.plot.y_min = self.plot.y_max = None
        self.plot.set_empty_message('没有可显示的结果', '请选择本组 BG 和可见样品，或切换“原始信号”')
        self.plot.set_series(displayed, label, '光子能量 (eV)' if energy else '波长 (nm)')
        self.state.set('已自动应用到主图' if matches and applied.automatic else '已应用到主图' if matches else '预览修改尚未应用')
        self.summary.set(f'补偿 +{correction.delta:.4f} counts  ·  负值 {sum(v < 0 for v in spectrum.raw)} → '
                         f'{sum(v < 0 for v in corrected)} 点' +
                         ('  ·  主图正在使用此校正。' if matches else '  ·  点“应用修改并关闭”更新主图。'))
        self.correction = correction
        self.apply_button.configure(state='normal', text='完成' if matches else '应用修改并关闭')
        return correction

    def apply(self):
        correction = self.preview()
        if correction is None:
            return
        key, name, _spectrum = self.signal()
        self.service.set_offset(key, correction)
        self.app.ab_action_status_var.set(f'{self.service.current_group.name}：{name} 偏移 +{correction.delta:.4f} counts 已应用。')
        self.finish()

    def restore(self):
        if self.service.current_group_id != self.group_id:
            return
        key, name, _spectrum = self.signal()
        self.service.set_offset(key, None)
        self.app.ab_action_status_var.set(f'{self.service.current_group.name}：{name} 已恢复原信号，重新读取此信号前不再自动校正。')
        self.finish()

    def finish(self):
        self.app.plot.y_min = self.app.plot.y_max = None
        self.app.sync_ab_group_controls()
        self.app.refresh_plot()
        self.close()

    def close(self):
        self.app.ab_offset_dialog = None
        close_modal(self.window)
