from __future__ import annotations

from ...config import TRIGGER_MODE_OPTIONS
from ..widgets import LargeCheckbutton


def build_acquisition_workspace(app, parent) -> None:
    app.ttk.Label(parent, text="运行控制", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
    run_row = app.ttk.Frame(parent, style="Card.TFrame")
    run_row.pack(fill="x")
    for column, weight in enumerate((1, 3, 2)):
        run_row.columnconfigure(column, weight=weight, uniform="run")
    app.start_button = app.ttk.Button(run_row, text="开始", command=app.start_acquisition)
    app.start_button.grid(row=0, column=0, sticky="ew")
    app.single_button = app.ttk.Button(
        run_row,
        text="单次",
        style="Primary.TButton",
        command=app.single_acquisition,
    )
    app.single_button.grid(row=0, column=1, sticky="ew", padx=6)
    app.stop_button = app.ttk.Button(run_row, text="暂停", style="Stop.TButton", command=app.stop_acquisition)
    app.stop_button.grid(row=0, column=2, sticky="ew")
    app.run_state_label = app.ttk.Label(parent, textvariable=app.run_state_var, style="RunIdle.TLabel")
    app.run_state_label.pack(anchor="w", pady=(7, 5))
    progress_row = app.ttk.Frame(parent, style="Card.TFrame")
    progress_row.pack(fill="x", pady=(0, 12))
    app.acquisition_progress = app.ttk.Progressbar(
        progress_row,
        variable=app.single_progress_var,
        maximum=100,
        mode="determinate",
        style="Acquisition.Horizontal.TProgressbar",
    )
    app.acquisition_progress.pack(side="left", fill="x", expand=True)
    app.status_lamp = app.tk.Canvas(
        progress_row,
        width=30,
        height=26,
        background="#ffffff",
        highlightthickness=0,
    )
    app.status_lamp.pack(side="right", padx=(10, 1))
    app.status_lamp_dot = app.status_lamp.create_oval(
        3,
        1,
        27,
        25,
        fill="#94a3b8",
        outline="#64748b",
        width=2,
    )

    app.ttk.Separator(parent).pack(fill="x", pady=(0, 12))
    common_header = app.ttk.Frame(parent, style="Card.TFrame")
    app.common_parameter_header = common_header
    common_header.pack(fill="x", pady=(0, 7))
    app.ttk.Label(common_header, text="常用采集参数", style="Section.TLabel").pack(side="left")
    preset_header = app.ttk.Frame(common_header, style="Card.TFrame")
    app.acquisition_preset_header = preset_header
    preset_header.pack(side="right")
    app.build_acquisition_preset_selector(preset_header)
    app.integration_spin = app.stepper_field(
        parent,
        "积分时间",
        app.integration_var,
        app.adjust_integration,
        unit_variable=app.integration_unit_var,
    )
    app.integration_unit_combo.bind("<<ComboboxSelected>>", app.on_integration_unit_changed)
    app.integration_spin.bind("<Return>", app.confirm_integration_preset_edit)
    app.integration_spin.bind("<FocusOut>", app.confirm_integration_preset_edit)
    app.averages_spin = app.stepper_field(
        parent,
        "扫描平均次数",
        app.averages_var,
        app.adjust_averages,
        suffix="次",
    )

    advanced_container = app.ttk.Frame(parent, style="Card.TFrame")
    advanced_container.pack(fill="x", pady=(5, 7))
    app.advanced_toggle_button = app.ttk.Button(
        advanced_container,
        text="展开高级采集选项",
        style="Tool.TButton",
        command=app.toggle_advanced_options,
    )
    app.advanced_toggle_button.pack(fill="x")
    app.advanced_frame = app.ttk.Frame(advanced_container, style="Card.TFrame")
    app.interval_spin = app.spin_field(app.advanced_frame, "刷新间隔", app.interval_var, 5, 5000, 5, "ms")
    app.attach_tooltip(
        app.interval_spin,
        "连续采集的目标刷新周期。实际帧间隔不会短于积分时间×平均次数；数值越小，界面尝试更新越快。",
    )
    app.boxcar_spin = app.spin_field(app.advanced_frame, "滑动平均宽度", app.boxcar_var, 0, 50, 1, "像素")

    trigger_row = app.ttk.Frame(app.advanced_frame, style="Card.TFrame")
    trigger_row.pack(fill="x", pady=4)
    app.ttk.Label(trigger_row, text="触发模式", style="Card.TLabel").pack(side="left")
    app.trigger_combo = app.ttk.Combobox(
        trigger_row,
        textvariable=app.trigger_mode_var,
        values=TRIGGER_MODE_OPTIONS,
        state="disabled",
        width=16,
    )
    app.trigger_combo.pack(side="right")

    x_axis_row = app.ttk.Frame(app.advanced_frame, style="Card.TFrame")
    x_axis_row.pack(fill="x", pady=4)
    app.ttk.Label(x_axis_row, text="X 轴", style="Card.TLabel").pack(side="left")
    app.x_axis_combo = app.ttk.Combobox(
        x_axis_row,
        textvariable=app.x_axis_var,
        values=["波长 (nm)", "像素索引"],
        state="disabled",
        width=16,
    )
    app.x_axis_combo.pack(side="right")
    app.x_axis_combo.bind("<<ComboboxSelected>>", app.on_x_axis_changed)

    app.edc_check = LargeCheckbutton(
        app.advanced_frame,
        text="启用暗噪声校正",
        variable=app.electric_dark_var,
        command=app.on_setting_changed,
    )
    app.edc_check.pack(anchor="w", pady=1)
    app.nl_check = LargeCheckbutton(
        app.advanced_frame,
        text="启用非线性校正",
        variable=app.nonlinearity_var,
        command=app.on_setting_changed,
    )
    app.nl_check.pack(anchor="w", pady=1)
    app.decouple_check = LargeCheckbutton(
        app.advanced_frame,
        text="解耦校正（当前 SDK 未开放）",
        variable=app.decouple_var,
        state="disabled",
    )
    app.decouple_check.pack(anchor="w", pady=1)
    app.light_source_check = LargeCheckbutton(
        app.advanced_frame,
        text="启用内置光源",
        variable=app.light_source_var,
        command=app.on_setting_changed,
    )
    app.light_source_check.pack(anchor="w", pady=1)
    app.ttk.Label(
        app.advanced_frame,
        textvariable=app.advanced_capability_var,
        style="Hint.TLabel",
        wraplength=500,
    ).pack(anchor="w", pady=(5, 0))
