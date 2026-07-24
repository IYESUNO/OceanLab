from __future__ import annotations


def build_header(app, root) -> None:
    padding = (10, 6, 10, 6) if app.compact_layout else (18, 10, 18, 10)
    header = app.ttk.Frame(root, style="Header.TFrame", padding=padding)
    header.pack(fill="x")
    app.ttk.Label(header, text="OceanLab", style="Title.TLabel").pack(side="left")
    app.ttk.Label(header, text="荧光光谱采集", style="Sub.TLabel").pack(side="left", padx=(12, 0), pady=(6, 0))
    app.connect_button = app.ttk.Button(header, text="连接仪器", style="ConnectIdle.TButton", command=app.toggle_connection)
    app.connect_button.pack(side="right")
    app.simulation_button = app.ttk.Button(
        header,
        text="模拟模式",
        style="Simulation.TButton",
        command=app.toggle_simulation_mode,
    )
    app.simulation_button.pack(side="right", padx=(0, 8))
    app.ttk.Label(header, textvariable=app.device_var, style="Sub.TLabel").pack(side="right", padx=(8, 14))
    app.device_status_label = app.ttk.Label(header, text="未连接", style="DeviceOff.TLabel")
    app.device_status_label.pack(side="right")


def build_footer(app, root) -> None:
    padding = (10, 1, 10, 4) if app.compact_layout else (16, 2, 16, 9)
    footer = app.ttk.Frame(root, padding=padding)
    footer.pack(fill="x")
    app.ttk.Label(footer, textvariable=app.status_var, style="Footer.TLabel").pack(side="left")
    sdk_area = app.ttk.Frame(footer)
    sdk_area.pack(side="right")
    app.ttk.Label(sdk_area, textvariable=app.sdk_footer_var, style="Footer.TLabel").pack(side="left")
    app.sdk_select_button = app.ttk.Button(
        sdk_area,
        text="选择 OceanDirect…",
        style="FooterTool.TButton" if app.compact_layout else "Tool.TButton",
        command=app.choose_oceandirect_home,
    )
    if not app.sdk_validator(app.sdk_home):
        app.sdk_select_button.pack(side="left", padx=(8, 0))
