from __future__ import annotations
from ..tauc_modes import TAUC_OPTIONS

from ..widgets import LargeCheckbutton


def build_processing_workspace(app, parent) -> None:
    app.ttk.Label(parent, text="实时光谱处理", style="Section.TLabel").pack(anchor="w")
    app.ttk.Label(
        parent,
        text="处理采集页当前光谱；记录 AB 参考后可实时显示透过率、吸光度或 Tauc 结果。",
        style="Hint.TLabel",
        wraplength=500,
    ).pack(anchor="w", pady=(4, 10))
    app.baseline_state_label = app.ttk.Label(
        parent,
        textvariable=app.baseline_state_var,
        style="BaselineEmpty.TLabel",
    )
    app.baseline_state_label.pack(anchor="w", fill="x", pady=(0, 7))
    capture_row = app.ttk.Frame(parent, style="Card.TFrame")
    capture_row.pack(fill="x")
    app.processing_reference_button = app.ttk.Button(capture_row, text="记录 AB 参考", command=app.capture_reference)
    app.processing_reference_button.pack(fill="x")
    clear_row = app.ttk.Frame(parent, style="Card.TFrame")
    clear_row.pack(fill="x", pady=(6, 0))
    app.processing_clear_baselines_button = app.ttk.Button(clear_row, text="清除 AB 参考", command=app.clear_baselines)
    app.processing_clear_baselines_button.pack(fill="x")

    app.ttk.Separator(parent).pack(fill="x", pady=12)
    app.ttk.Label(parent, text="显示方式", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
    app.mode_combo = app.ttk.Combobox(
        parent,
        textvariable=app.mode_var,
        state="readonly",
        values=("原始强度", "峰值归一化", "透过率 (%)", "吸光度 (A)", *TAUC_OPTIONS),
    )
    app.mode_combo.pack(fill="x", pady=(0, 10))
    app.mode_combo.bind("<<ComboboxSelected>>", app.on_processing_mode_changed)
    _build_range_controls(app, parent)


def _history_name(app, parent, variable):
    from ..history_widgets import EllipsisLabel
    label = EllipsisLabel(parent, textvariable=variable, style="Hint.TLabel")
    app.attach_tooltip(label, variable.get, delay_ms=600, max_width=None)
    return label


def _history_status(app, parent, variable):
    label = app.ttk.Label(parent, textvariable=variable, style="Hint.TLabel", wraplength=300)
    label.pack(fill="x", pady=(3, 3))
    label.bind("<Configure>", lambda event: label.configure(wraplength=max(100, event.width - 4)))
    return label


def _history_list(app, parent, prefix):
    shell = app.ttk.Frame(parent, style="Card.TFrame")
    shell.pack(fill="both", expand=True)
    canvas = app.tk.Canvas(shell, background="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
    scrollbar = app.ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    frame = app.ttk.Frame(canvas, style="Card.TFrame", padding=4)
    window = canvas.create_window((0, 0), window=frame, anchor="nw")
    frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
    for suffix, widget in (("canvas", canvas), ("frame", frame), ("window", window)):
        setattr(app, prefix + "_list_" + suffix, widget)
    app.bind_list_mousewheel(canvas, canvas)
    app.bind_list_mousewheel(frame, canvas)


def build_pl_workspace(app, parent) -> None:
    header = app.ttk.Frame(parent, style="Card.TFrame")
    header.pack(fill="x", pady=(0, 5))
    app.ttk.Label(header, text="PL 样品", style="Section.TLabel").pack(side="left")
    app.pl_clear_button = app.ttk.Button(header, text="清空全部", width=7, style="History.TButton", command=app.clear_pl_files)
    app.pl_clear_button.pack(side="right")
    buttons = app.ttk.Frame(parent, style="Card.TFrame")
    buttons.pack(fill="x")
    buttons.columnconfigure((0, 1), weight=1, uniform="sources")
    app.pl_open_button = app.ttk.Button(buttons, text="添加样品…", width=10, style="Primary.History.TButton", command=app.select_pl_files)
    app.pl_open_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
    app.pl_background_button = app.ttk.Button(buttons, text="选择 BG…", width=10, style="History.TButton", command=app.select_pl_background)
    app.pl_background_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
    reference = app.ttk.Frame(parent, style="Card.TFrame")
    reference.pack(fill="x", pady=(5, 0))
    app.ttk.Label(reference, text="BG", style="Hint.TLabel").pack(side="left", padx=(0, 6))
    app.pl_background_clear_button = app.ttk.Button(reference, text="清除", width=4, style="History.TButton", command=app.clear_pl_background)
    app.pl_background_clear_button.pack(side="right", padx=(5, 0))
    app.pl_background_name_label = _history_name(app, reference, app.pl_background_status_var)
    app.pl_background_name_label.pack(side="left", fill="x", expand=True)
    options = app.ttk.Frame(parent, style="Card.TFrame")
    options.pack(fill="x", pady=(5, 2))
    app.pl_normalize_check = app.ttk.Checkbutton(options, text="峰值归一化", style="History.TCheckbutton",
                                              variable=app.pl_normalize_var, command=app.on_pl_display_changed)
    app.pl_normalize_check.pack(side="left")
    app.pl_turbo_check = app.ttk.Checkbutton(options, text="Turbo 渐变色", style="History.TCheckbutton",
                                          variable=app.pl_turbo_var, command=app.on_pl_display_changed)
    app.pl_turbo_check.pack(side="left", padx=(12, 0))
    app.pl_status_label = _history_status(app, parent, app.pl_status_var)
    _history_list(app, parent, "pl")


def build_absorbance_workspace(app, parent) -> None:
    groups = app.ttk.Frame(parent, style="Card.TFrame")
    groups.pack(fill="x", pady=(0, 5))
    app.ttk.Label(groups, text="分析组", style="Section.TLabel").pack(side="left", padx=(0, 6))
    app.ab_clear_button = app.ttk.Menubutton(groups, text="管理…", width=6, style="History.TMenubutton")
    menu = app.tk.Menu(app.ab_clear_button, tearoff=False)
    menu.add_command(label="清空本组", command=app.clear_ab_group)
    menu.add_separator()
    menu.add_command(label="清空全部分析组", command=app.clear_historical_ab)
    app.ab_clear_button.configure(menu=menu)
    app.ab_clear_button.pack(side="right", padx=(5, 0))
    app.ab_new_group_button = app.ttk.Button(groups, text="新建", width=4, style="History.TButton", command=app.create_ab_group)
    app.ab_new_group_button.pack(side="right", padx=(5, 0))
    app.ab_group_combo = app.ttk.Combobox(groups, textvariable=app.ab_group_var, state="readonly", width=12, style="History.TCombobox")
    app.ab_group_combo.pack(side="left", fill="x", expand=True)
    app.ab_group_combo.bind("<<ComboboxSelected>>", app.on_ab_group_changed)
    app.attach_tooltip(app.ab_group_combo, "本组独立使用 BG；换一批测量先新建一组，各组结果可叠图")
    sources = app.ttk.Frame(parent, style="Card.TFrame")
    sources.pack(fill="x")
    sources.columnconfigure((0, 1), weight=1, uniform="sources")
    app.ab_samples_button = app.ttk.Button(sources, text="添加样品…", width=9, style="Primary.History.TButton", command=app.select_ab_sample_files)
    app.ab_samples_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
    app.ab_reference_file_button = app.ttk.Button(sources, text="选择 BG…", width=9, style="History.TButton", command=app.select_ab_reference_file)
    app.ab_reference_file_button.grid(row=0, column=1, sticky="ew", padx=3)
    app.ab_reload_button = app.ttk.Button(sources, text="重读", width=4, style="History.TButton", command=app.reload_ab_samples)
    app.ab_reload_button.grid(row=0, column=2, padx=(3, 0))
    app.attach_tooltip(app.ab_reload_button, "重读本组样品文件，保留名称、颜色和显隐状态")
    reference = app.ttk.Frame(parent, style="Card.TFrame")
    reference.pack(fill="x", pady=(5, 0))
    app.ttk.Label(reference, text="BG", style="Hint.TLabel").pack(side="left", padx=(0, 6))
    app.ab_reference_clear_button = app.ttk.Button(reference, text="清除", width=4, style="History.TButton", command=app.clear_ab_reference)
    app.ab_reference_clear_button.pack(side="right", padx=(5, 0))
    app.ab_reference_name_label = _history_name(app, reference, app.ab_reference_status_var)
    app.ab_reference_name_label.pack(side="left", fill="x", expand=True)
    correction = app.ttk.Frame(parent, style="Card.TFrame")
    correction.pack(fill="x", pady=(4, 0))
    app.ab_offset_button = app.ttk.Button(correction, text="偏移校正…", width=8, style="History.TButton", command=app.open_ab_offset_dialog)
    app.ab_offset_button.pack(side="right", padx=(5, 0), anchor="n")
    app.ab_offset_summary_var = app.tk.StringVar(value="偏移：未应用")
    summary = app.ttk.Label(correction, textvariable=app.ab_offset_summary_var, style="Hint.TLabel", wraplength=250)
    summary.pack(side="left", fill="x", expand=True)
    summary.bind("<Configure>", lambda event: summary.configure(wraplength=max(100, event.width - 4)))
    app.attach_tooltip(summary, app.ab_offset_status_var.get, delay_ms=600)
    app.ttk.Separator(parent).pack(fill="x", pady=(6, 6))
    mode = app.ttk.Frame(parent, style="Card.TFrame")
    mode.pack(fill="x")
    app.ttk.Label(mode, text="结果", style="Hint.TLabel").pack(side="left", padx=(0, 6))
    app.ab_energy_axis_button = app.ttk.Button(mode, text="eV", width=3, style="History.TButton",
                                              command=lambda: app.set_ab_x_axis("光子能量 (eV)"))
    app.ab_energy_axis_button.pack(side="right", padx=(3, 0))
    app.ab_wavelength_axis_button = app.ttk.Button(mode, text="nm", width=3, style="History.TButton",
                                                  command=lambda: app.set_ab_x_axis("波长 (nm)"))
    app.ab_wavelength_axis_button.pack(side="right", padx=(6, 0))
    app.attach_tooltip(app.ab_energy_axis_button, "横轴：光子能量 (eV)")
    app.attach_tooltip(app.ab_wavelength_axis_button, "横轴：波长 (nm)")
    app.ab_mode_combo = app.ttk.Combobox(mode, textvariable=app.ab_mode_var, state="readonly", width=14,
                                       values=("透过率 (%)", "吸光度 (A)", *TAUC_OPTIONS), style="History.TCombobox")
    app.ab_mode_combo.pack(side="left", fill="x", expand=True)
    app.ab_mode_combo.bind("<<ComboboxSelected>>", app.on_ab_display_changed)
    app.update_ab_axis_button_styles()
    _history_status(app, parent, app.ab_result_status_var)
    action = _history_name(app, parent, app.ab_action_status_var)
    action.pack(fill="x", pady=(0, 4))
    _history_list(app, parent, "ab")


def _build_range_controls(app, parent) -> None:
    range_row = app.ttk.Frame(parent, style="Card.TFrame")
    range_row.pack(fill="x", pady=(0, 6))
    app.ttk.Label(range_row, textvariable=app.range_name_var, style="Card.TLabel").pack(side="left")
    app.xmin_entry = app.ttk.Entry(range_row, textvariable=app.xmin_var, width=9)
    app.xmin_entry.pack(side="left", padx=(8, 3))
    app.ttk.Label(range_row, text="–", style="Card.TLabel").pack(side="left")
    app.xmax_entry = app.ttk.Entry(range_row, textvariable=app.xmax_var, width=9)
    app.xmax_entry.pack(side="left", padx=(3, 0))
    app.ttk.Label(range_row, textvariable=app.range_unit_var, style="Card.TLabel").pack(side="left", padx=(5, 0))
    app.range_button = app.ttk.Button(parent, text="应用显示范围", command=app.apply_range)
    app.range_button.pack(fill="x")
