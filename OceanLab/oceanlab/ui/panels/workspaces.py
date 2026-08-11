from __future__ import annotations

from ..widgets import LargeCheckbutton


def build_processing_workspace(app, parent) -> None:
    app.ttk.Label(parent, text="实时光谱处理", style="Section.TLabel").pack(anchor="w")
    app.ttk.Label(
        parent,
        text="处理采集页当前光谱；记录暗谱和 AB 参考后可实时显示透过率、吸光度或 Tauc 结果。",
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
    app.dark_button = app.ttk.Button(capture_row, text="记录暗谱", command=app.capture_dark)
    app.dark_button.pack(side="left", fill="x", expand=True)
    app.processing_reference_button = app.ttk.Button(capture_row, text="记录 AB 参考", command=app.capture_reference)
    app.processing_reference_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
    clear_row = app.ttk.Frame(parent, style="Card.TFrame")
    clear_row.pack(fill="x", pady=(6, 0))
    app.clear_dark_button = app.ttk.Button(clear_row, text="清除暗谱", command=app.clear_dark)
    app.clear_dark_button.pack(side="left", fill="x", expand=True)
    app.processing_clear_baselines_button = app.ttk.Button(clear_row, text="清除暗谱和参考", command=app.clear_baselines)
    app.processing_clear_baselines_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

    app.ttk.Separator(parent).pack(fill="x", pady=12)
    app.ttk.Label(parent, text="显示方式", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
    app.mode_combo = app.ttk.Combobox(
        parent,
        textvariable=app.mode_var,
        state="readonly",
        values=("原始强度", "暗谱扣除", "峰值归一化", "透过率 (%)", "吸光度 (A)", "Tauc 计算结果"),
    )
    app.mode_combo.pack(fill="x", pady=(0, 10))
    app.mode_combo.bind("<<ComboboxSelected>>", app.on_processing_mode_changed)
    _build_range_controls(app, parent)


def build_pl_workspace(app, parent) -> None:
    app.ttk.Label(parent, text="历史 PL 绘图", style="Section.TLabel").pack(anchor="w")
    app.ttk.Label(
        parent,
        text="选择多份样品 PL；可另选一份 Si 基底 BG。未选择 BG 时直接显示原始 PL。",
        style="Hint.TLabel",
        wraplength=500,
    ).pack(anchor="w", pady=(4, 10))
    button_row = app.ttk.Frame(parent, style="Card.TFrame")
    button_row.pack(fill="x")
    app.pl_open_button = app.ttk.Button(button_row, text="选择 PL 文件", style="Primary.TButton", command=app.select_pl_files)
    app.pl_open_button.pack(side="left", fill="x", expand=True)
    app.pl_background_button = app.ttk.Button(button_row, text="选择 BG 文件", command=app.select_pl_background)
    app.pl_background_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

    background_row = app.ttk.Frame(parent, style="Card.TFrame")
    background_row.pack(fill="x", pady=(8, 0))
    app.ttk.Label(
        background_row,
        textvariable=app.pl_background_status_var,
        style="Hint.TLabel",
        wraplength=390,
    ).pack(side="left", fill="x", expand=True)
    app.pl_background_clear_button = app.ttk.Button(background_row, text="清除 BG", command=app.clear_pl_background)
    app.pl_background_clear_button.pack(side="right", padx=(6, 0))

    option_row = app.ttk.Frame(parent, style="Card.TFrame")
    option_row.pack(fill="x", pady=(8, 6))
    app.pl_normalize_check = LargeCheckbutton(
        option_row,
        text="峰值归一化",
        variable=app.pl_normalize_var,
        command=app.on_pl_display_changed,
    )
    app.pl_normalize_check.pack(side="left")
    app.pl_turbo_check = LargeCheckbutton(
        option_row,
        text="Turbo 渐变色",
        variable=app.pl_turbo_var,
        command=app.on_pl_display_changed,
    )
    app.pl_turbo_check.pack(side="left", padx=(8, 0))
    app.pl_clear_button = app.ttk.Button(option_row, text="清空全部", command=app.clear_pl_files)
    app.pl_clear_button.pack(side="right")

    app.pl_status_label = app.ttk.Label(
        parent,
        textvariable=app.pl_status_var,
        style="Hint.TLabel",
        wraplength=500,
    )
    app.pl_status_label.pack(anchor="w", fill="x", pady=(0, 7))

    list_shell = app.ttk.Frame(parent, style="Card.TFrame")
    list_shell.pack(fill="both", expand=True)
    app.pl_list_canvas = app.tk.Canvas(list_shell, background="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
    scrollbar = app.ttk.Scrollbar(list_shell, orient="vertical", command=app.pl_list_canvas.yview)
    app.pl_list_canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    app.pl_list_canvas.pack(side="left", fill="both", expand=True)
    app.pl_list_frame = app.ttk.Frame(app.pl_list_canvas, style="Card.TFrame", padding=6)
    app.pl_list_window = app.pl_list_canvas.create_window((0, 0), window=app.pl_list_frame, anchor="nw")
    app.pl_list_frame.bind(
        "<Configure>",
        lambda _event: app.pl_list_canvas.configure(scrollregion=app.pl_list_canvas.bbox("all")),
    )
    app.pl_list_canvas.bind(
        "<Configure>",
        lambda event: app.pl_list_canvas.itemconfigure(app.pl_list_window, width=event.width),
    )
    app.bind_list_mousewheel(app.pl_list_canvas, app.pl_list_canvas)
    app.bind_list_mousewheel(app.pl_list_frame, app.pl_list_canvas)


def build_absorbance_workspace(app, parent) -> None:
    app.ttk.Label(parent, text="AB 吸收", style="Section.TLabel").pack(anchor="w")
    app.ttk.Label(
        parent,
        text="选择多份样品信号、一份 BG 参考；仪器暗谱可选。",
        style="Hint.TLabel",
        wraplength=500,
    ).pack(anchor="w", pady=(4, 9))
    source_buttons = app.ttk.Frame(parent, style="Card.TFrame")
    source_buttons.pack(fill="x")
    app.ab_samples_button = app.ttk.Button(
        source_buttons,
        text="选择样品信号",
        style="Primary.TButton",
        command=app.select_ab_sample_files,
    )
    app.ab_samples_button.pack(fill="x")
    reference_row = app.ttk.Frame(parent, style="Card.TFrame")
    reference_row.pack(fill="x", pady=(6, 0))
    app.ab_reference_file_button = app.ttk.Button(
        reference_row,
        text="选择 BG 参考",
        command=app.select_ab_reference_file,
    )
    app.ab_reference_file_button.pack(side="left", fill="x", expand=True)
    app.ab_dark_file_button = app.ttk.Button(
        reference_row,
        text="选择暗谱（可选）",
        command=app.select_ab_dark_file,
    )
    app.ab_dark_file_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
    reference_status_row = app.ttk.Frame(parent, style="Card.TFrame")
    reference_status_row.pack(fill="x", pady=(7, 0))
    app.ab_reference_clear_button = app.ttk.Button(
        reference_status_row,
        text="清除参考",
        command=app.clear_ab_reference,
        width=10,
    )
    app.ab_reference_clear_button.pack(side="right", padx=(6, 0))
    app.ttk.Label(
        reference_status_row,
        textvariable=app.ab_reference_status_var,
        style="Hint.TLabel",
        wraplength=350,
    ).pack(side="left", fill="x", expand=True)
    dark_status_row = app.ttk.Frame(parent, style="Card.TFrame")
    dark_status_row.pack(fill="x", pady=(2, 0))
    app.ttk.Label(
        dark_status_row,
        textvariable=app.ab_dark_status_var,
        style="Hint.TLabel",
        wraplength=390,
    ).pack(side="left", fill="x", expand=True)
    app.ttk.Button(dark_status_row, text="清除暗谱", command=app.clear_ab_dark).pack(side="right", padx=(6, 0))

    app.ttk.Separator(parent).pack(fill="x", pady=(10, 8))
    mode_row = app.ttk.Frame(parent, style="Card.TFrame")
    mode_row.pack(fill="x")
    app.ttk.Label(mode_row, text="结果", style="Card.TLabel").pack(side="left")
    app.ab_mode_combo = app.ttk.Combobox(
        mode_row,
        textvariable=app.ab_mode_var,
        values=("透过率 (%)", "吸光度 (A)", "Tauc 计算结果"),
        state="readonly",
        width=16,
    )
    app.ab_mode_combo.pack(side="right")
    app.ab_mode_combo.bind("<<ComboboxSelected>>", app.on_ab_display_changed)
    app.ab_clear_button = app.ttk.Button(mode_row, text="清空全部", command=app.clear_historical_ab)
    app.ab_clear_button.pack(side="right", padx=(0, 8))

    display_row = app.ttk.Frame(parent, style="Card.TFrame")
    display_row.pack(fill="x", pady=(6, 0))
    app.ttk.Label(display_row, text="显示", style="Card.TLabel").pack(side="left")
    axis_buttons = app.ttk.Frame(display_row, style="Card.TFrame")
    axis_buttons.pack(side="right")
    app.ab_wavelength_axis_button = app.ttk.Button(
        axis_buttons,
        text="波长 nm",
        command=lambda: app.set_ab_x_axis("波长 (nm)"),
        width=10,
    )
    app.ab_wavelength_axis_button.pack(side="left")
    app.ab_energy_axis_button = app.ttk.Button(
        axis_buttons,
        text="光子能量 eV",
        command=lambda: app.set_ab_x_axis("光子能量 (eV)"),
        width=13,
    )
    app.ab_energy_axis_button.pack(side="left", padx=(6, 0))
    app.update_ab_axis_button_styles()
    app.ttk.Label(parent, textvariable=app.ab_result_status_var, style="Hint.TLabel", wraplength=500).pack(
        anchor="w", fill="x", pady=(6, 5)
    )

    list_shell = app.ttk.Frame(parent, style="Card.TFrame")
    list_shell.pack(fill="both", expand=True)
    app.ab_list_canvas = app.tk.Canvas(list_shell, background="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
    scrollbar = app.ttk.Scrollbar(list_shell, orient="vertical", command=app.ab_list_canvas.yview)
    app.ab_list_canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    app.ab_list_canvas.pack(side="left", fill="both", expand=True)
    app.ab_list_frame = app.ttk.Frame(app.ab_list_canvas, style="Card.TFrame", padding=6)
    app.ab_list_window = app.ab_list_canvas.create_window((0, 0), window=app.ab_list_frame, anchor="nw")
    app.ab_list_frame.bind(
        "<Configure>",
        lambda _event: app.ab_list_canvas.configure(scrollregion=app.ab_list_canvas.bbox("all")),
    )
    app.ab_list_canvas.bind(
        "<Configure>",
        lambda event: app.ab_list_canvas.itemconfigure(app.ab_list_window, width=event.width),
    )
    app.bind_list_mousewheel(app.ab_list_canvas, app.ab_list_canvas)
    app.bind_list_mousewheel(app.ab_list_frame, app.ab_list_canvas)


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
