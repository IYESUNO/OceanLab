# OceanLab Spectroscopy Software — Windows 7 Branch

OceanLab `2.1.2` on this branch is the dedicated 64-bit Windows 7 compatibility edition. It preserves the complete acquisition, processing, PL fluorescence, and AB absorption feature set while using an isolated legacy Python toolchain. The standard Windows 10/11 source and release remain on the `win64` branch.

## Quick Start

Run from source:

```powershell
cd OceanLab
python launcher.py
```

Without a connected spectrometer, select simulation mode. Use the command line to check the version or probe for devices:

```powershell
python launcher.py --version
python launcher.py --probe --sdk-home C:\OceanDirect
```

For the portable edition, extract the complete ZIP archive and run `OceanLab-2.1.2.exe`. Python is bundled, while OceanDirect is not.

## Win7 安装版构建（2.1.2）

当前 2.1.2 提供绿色 ZIP 和对应安装 EXE，各附同名 SHA256 校验文件；两者使用相同程序载荷。

双击 `OceanLab-2.1.2-win7-x64-setup.exe` 即可安装，默认位置为当前用户的 `%LOCALAPPDATA%\Programs\OceanLab-Win7`，无需管理员权限。桌面和开始菜单会创建 `OceanLab (Win7)` 快捷方式。

以后升级时先退出 OceanLab，再双击新版安装包。安装包沿用上次位置，更新快捷方式并清理旧版程序文件；同版本可重新安装修复，不允许覆盖安装更旧版本。设置 `%LOCALAPPDATA%\OceanLab\settings.json` 和光谱数据在升级、卸载时保留。从绿色版迁移也会沿用同一用户的设置，原绿色版目录不会删除。

安装包离线包含 Python、Tkinter 和运行库。真机仍需另行安装 OceanDirect 和 USB 驱动。安装版与绿色版采用同一程序载荷，设备进程或界面仍在运行时会阻止升级。

构建安装版（默认先重新构建绿色包）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_win7_installer_toolchain.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_win7_installer.ps1 -Version "2.1.2"
```

编译工具固定为 Inno Setup 6.2.2，下载校验 SHA256 和 Authenticode 签名后提取到忽略的 `.build-win7` 目录；不增加应用运行依赖。若当前绿色包已完成验证，可追加 `-SkipPortableBuild`。输出 EXE 和对应 SHA256 文件位于 `release`。

`test_win7_installer.ps1` 使用独立测试标识、工作区安装目录和内部快捷方式，验证首次安装、覆盖升级、修复、降级拦截、运行中拦截、卸载及数据保留，最后卸载测试注册项；不替换正式安装。测试需要允许写入当前用户的临时卸载注册项，日志保留在 `.build-win7`。

## OceanDirect Driver

For physical instruments, obtain and install the official 64-bit OceanDirect SDK and USB driver from Ocean Insight/Ocean Optics and comply with their license terms. OceanLab does not bundle, copy, or modify the SDK, drivers, firmware, EEPROM, or calibration data.

OceanLab checks `OCEANDIRECT_HOME`, `C:\OceanDirect`, and common installation locations. You can also select the SDK root from the lower-right corner of the application. Simulation mode remains available without OceanDirect.

## 2.1.2 暂停和取消后保留连接

同版本界面更新：AB 分析组使用统一浅色卡片与标题栏，当前编辑组的标题突出显示。眼睛和垃圾桶图标放在标题文字前，长文字自动省略且悬停可查看完整标题；图标位置和点击区域固定。眼睛控制整组显隐，保留各条曲线自己的显隐选择。删除必须二次确认，默认选择“否”，只移除对应组的样品、BG 和校正状态，源文件保留。删除最后一组后回到空的“分析组 1”；唯一空组的删除按钮置灰，不因反复删除增加编号。点击组标题切换当前编辑组。隐藏组不参与主图和曲线导出。默认曲线导出文件名明确区分 `Tauc_Direct_2` 与 `Tauc_Indirect_1-2`，处理页与 AB 页共用命名规则。

连续采集点击“暂停”或单次点击“取消单次”后，只等待当前扫描或参数同步完成，不再执行剩余平均扫描。平均 20 次采用逐次读取，每次之间检查停止标志，与本机 OceanView 的停止平均粒度一致。等待期间禁用新的采集操作、保留之前的曲线；被取消的批次不成为保存候选。确认停止后保留仪器连接和已验证参数，可直接再采。

普通停止与强制中断使用不同标志，不调用 SDK 的取消接口、不关闭设备进程。SDK 超时、设备异常、主动断开或退出仍通过原有机制释放设备进程；没有通过并发 SDK 线程打断读取。旧请求的迟到帧、开始 / 停止消息均按请求标识过滤。当前无 USB 仪器，真机暂停时长仍需现场验收。

平均 1 次仍直接使用原官方处理路线。平均多次时暂时设 SDK 平均为 1、非线性关闭、Boxcar 0，保留 SDK 电暗校正，逐次累加求平均；线性电暗减法与平均可交换次序，但跨 SDK 数值仍未实测。非线性必须在平均后调用官方 `nonlinearity_correct_spectrum2()`，以零暗谱避免重复扣除电暗且不修改存储暗谱，之后调用官方 `boxcar_correct_spectrum()`。成功、取消或异常均恢复并复验原配置；恢复失败释放连接。保存元数据仍记录完整完成的实际平均次数，不将取消的部分平均标成完整结果。

## 2.1.1 PL / AB 紧凑布局

PL 和 AB 样品名称只读显示，不再提供行内编辑框。名称优先占用文件行宽度，超长部分显示省略号，悬停 600 ms 只显示源文件完整文件名（含扩展名），不重复显示曲线基本名；提示按内容展开，仅超过屏幕可用宽度时换行。移开、点击、滚动或移除该行后关闭提示。点击名称或按 Enter / 空格可设置活动曲线；显隐、颜色与滚轮操作保持可用。

同版本绿色包更新：文件行使用 [Lucide](https://lucide.dev/icons) 的统一线条图标：眼睛 / 带斜线眼睛表示显隐，准星表示活动曲线，调色盘显示当前曲线颜色，垃圾桶用于移除。活动准星有蓝色背景，图标支持悬停中文说明及 Enter / 空格操作；Turbo 控制颜色时手动调色禁用。图标按系统 DPI 选择抗锯齿图片，按钮点击区域为 24 DIP、图形为 18 DIP，不增加第三方运行依赖。SVG 来源及 ISC / MIT 许可随包放在 `assets/history_icons`。

上方使用独立的紧凑按钮样式，BG 文件名同样只读并支持完整名称提示。AB 的结果模式和 nm/eV 横轴放在同一行，清空本组及所有组收进“管理”菜单；偏移状态使用简短摘要，保留疑似整体偏移提示，详情记录原平台值及点数。PL 去掉每行重复 BG 说明，并使用紧凑的归一化 / Turbo 开关。依据 [Windows 截断名称提示指南](https://learn.microsoft.com/en-us/windows/win32/uxguide/ctrl-tooltips-and-infotips) 保留完整原文。

按用户要求，同版本更新的饱和度只显示 `原始信号峰值 / 设备满量程 × 100%`，例如 `96.2%`，超过 100% 也按实际数值显示。保留原有比例颜色提示：低于 80% 绿色、80% 至低于 95% 黄色、95% 及以上红色，无数据为普通样式。不再用“疑似截顶”替换数值或根据平台检测强制标红；AB 侧栏及偏移窗口同样取消饱和判断文字。平台检测仍用于偏移估算及导出记录，采集信号和计算公式保持原样。

## 2.1.0 Tauc 直接 / 间接模式

实时采集光谱的“处理”页、历史“AB吸收”页和信号校正预览都提供 `Tauc 直接（2）` 与 `Tauc 间接（1/2）` 两个选项，分别绘制 `(A·hν)²` 与 `sqrt(A·hν)`，用于直接允许跃迁和间接允许跃迁的 Tauc 分析。仍使用吸光度 `A = -log10(sample / BG)`；处理页沿用波长/像素横轴，AB 页可选择光子能量 eV 横轴。这里只增加显示模式，不自动拟合带隙或判定跃迁类型。

间接模式中负吸光度没有实数平方根，保留为 `NaN` 并断开该处曲线，AB 状态栏提示数量；不取绝对值、不补零。零吸光度对应 0。直接模式保持负吸光度平方的原有行为，旧 `Tauc 计算结果` 偏好自动迁移为直接模式。导出保留所选计算结果、无效点及公式，2.1.2 更新后间接模式文件名包含 `Tauc_Indirect_1-2`，与直接模式区分。

指数约定参见 [Limitations of the Tauc Plot Method](https://doi.org/10.1002/adfm.202304523)。

## 2.0.5 默认采集参数与 SDK 处理

全新设置的默认积分时间改为 100 ms、平均 1 次、Boxcar 0，电暗校正开启、非线性关闭、正常触发、波长横轴，与本机 OceanView 程序初始默认对应。已有保存的积分时间和三个预设保持用户设置；`--once` 同样使用这些初始采集参数。

2.0.5 至 2.1.2 的最初暂停实现使用官方 SDK 整批平均及 `get_formatted_spectrum()` 处理路线。2.1.2 本次逐扫描停止更新改为上文所述的逐次平均，仍使用官方电暗、非线性与 Boxcar 处理，不自行实现校正算法。所有设置写完后读回最终状态，防止开关联动造成元数据与实际设置不一致。

QE65000 饱和参考改为设备 ADC 满量程，与本机 OceanView 使用 `getMaxIntensityCounts()` 对应；不再把 63000 或校正后的 63047 平台当作 ADC 校准。2.0.5 曾以连续平顶显示保守提示；2.1.1 按用户要求改为只显示数字百分比，饱和与否由用户判断。

本机 OceanView 使用 OmniDriver / SPAM，OceanLab 使用 OceanDirect 3.1.4。默认参数对齐及调用测试不等于两种 SDK 的真机数值已经相同；目前无 USB 仪器，未完成同一光路下的电暗、非线性、平均及 Boxcar 数值对照。旧 BG 的 8009-count 偏移根因仍待真机验证，这次更新不声称恢复截顶信号。

SDK 接口核对见 [采集核对记录](docs/acquisition-defaults-audit.md)。

## Main Features

- Four workspaces: Acquisition, Processing, PL Fluorescence, and AB Absorption.
- Single and continuous acquisition, three acquisition presets, electric-dark correction, and parameter status feedback.
- Raw intensity, peak normalization, transmittance, and absorbance processing.
- Multi-file PL overlays, optional BG subtraction, AB sample and BG reference analysis groups.
- Import support for OceanView GB18030 TXT, OceanLab CSV/TXT, and generic two-column spectra.
- Save-time reopen verification for CSV/TXT; TXT exports use an OceanView-compatible two-column data format.

## AB 吸收分析组（2.0.3）

- 每个分析组使用独立的 BG 参考和多份样品；各组计算结果可叠图比较。
- 首次导入使用分析组 1。另一批测量请先点击“新建一组”，再添加本组 BG 和样品。
- “添加本组样品”“更换本组 BG”“清空本组”只影响当前组；“清空全部”清除所有组。
- 切换组会恢复该组的 BG 状态；点击活动曲线也会切换到所属组。
- 重复添加同路径样品不会改变原数据、显隐、活动曲线或缩放。文件内容更新后点击“重读本组样品”；BG 通过“更换本组 BG”重新选择。
- 波长网格不兼容或读取失败时保留原数据并提示。
- AB 历史结果默认自动适配 Y 轴，完整显示负吸光度和超过 100% 的透过率。手动缩放和范围仍可使用。
- BG 非正值或样品负值不参与 AB 计算，两个负信号相除也不能成为有效点，避免 Tauc 假峰压扁图像。零样品只可显示 0% 透过率；吸光度/Tauc 无效。状态栏提示异常信号点数，游标显示“无效”；负吸光度与负原始信号需区分，正样品大于正 BG 时的负吸光度仍保留。
- 分析组仅在本次运行内保留，不自动扫描或记忆历史文件路径。

## Win7 AB 信号偏移校正试用（2.0.4）

在 AB 吸收页导入本组 BG 和样品，满足连续明显负值、多点平台及可靠同组基准条件时，默认直接应用估算偏移，主图标明“自动估算”。导入顺序不限；无可靠基准时只提示，不自动补偿。
“信号偏移校正…”窗口采用左侧参数、右侧大图布局，默认显示已应用的结果，可打开校正前对照；计算样品、无效点及主图应用状态独立显示。可手动填写基准，再点“更新预览”和“应用修改并关闭”；修改参数后旧预览立即失效。
“恢复此信号原值”撤销所选信号的补偿；恢复后，切换显示、重绘及重复导入都不会重新自动校正，重新读取该信号才开始新的评估。
仅在连续明显负值与多点平台同时存在时建议校正，小负噪声不触发。补偿为基准平台减原平台，并加到所选信号的全谱；没有可靠基准时不自动填入 63000 或 63047。
原文件保持不变，其他分析组不受影响。校正只在本次运行内保留；更换 BG 或成功重读样品会清除并重新评估对应信号的校正。导出参数记录补偿量、基准来源、估算状态及是否自动应用。
整体偏移只加减常数，无法恢复已经截顶的信号。2.1.1 界面仅显示平台值与点数，不自动判断饱和与否。

窗口交互参考 [Origin 官方信号处理教程](https://docs.originlab.com/tutorials/smoothing/)的参数/右侧预览分区，以及 [Adobe Camera Raw 官方工作区说明](https://helpx.adobe.com/camera-raw/desktop/get-started/overview-and-setup/introduction-camera-raw.html)的前后对照开关与保留原始数据方式。采用现有 Tk 控件风格；这些参考用于界面交互，不作为偏移算法依据。

## 简化暗谱选项（2.0.3）

处理页和 AB 页不再提供手动仪器暗谱记录、导入或清除选项，处理页不再提供“暗谱扣除”显示模式。
实时透过率、吸光度、Tauc 只需记录 AB 参考；历史 AB 只需导入样品和 BG 参考。
计算使用样品与参考信号之比，旧偏好中的“暗谱扣除”自动回退到原始强度。
采集页的仪器“启用暗噪声校正”和 PL 的 BG 扣除保持原功能；旧 CSV 扩展列仍可读取。

## Win7 饱和度显示修复（2.0.3）

饱和度标签按完整文本和实际字体自适应宽度，取消紧凑布局的固定字符宽度，避免中文字体或 DPI 差异导致小数与百分号被裁切。
2.1.1 按用户要求保留原有百分比颜色提示，文字仅显示数值。

## 采集可靠性修复（2.0.3）

- 连接后仅确认当前预设，不再自动生成一帧无法关联到用户点击的预览。
- 参数确认期间锁定采集按钮，参数应用合并为一次操作；未变化的仪器参数不重复写入，保留读回验证。
- 每次采集带有请求标识；旧帧、取消后的帧不得成为当前单次结果或保存候选。
- 真机单次支持取消。设备操作在独立进程中串行执行，卡住时释放设备进程并提示重新连接，不在多个线程中并发调用 SDK。
- 配置、连接等操作等待上限默认 15 秒；读取上限为 `max(15 秒, 积分时间 × 平均次数 × 2 + 5 秒)`，为长积分与扫描平均保留余量。
- 读取中的取消或超时通常需要重新连接仪器。取消不会自动保存本次数据。
- 绘图或事件处理异常会记录日志和报告错误，后续设备事件轮询仍继续。
- 修复采集线程的停止方法与 Python 线程内部方法重名，保证失败后能更换 SDK、退出线程。
- 日志记录请求标识、阶段、设备操作耗时和失败信息，方便区分参数同步、等待数据和界面处理问题。

## 当前曲线 TXT 导出（2.0.3）

绘图工具栏最后的软盘保存图标只在“处理”“PL荧光”“AB吸收”三个工作区显示，采集页使用左下角原有保存区。单条曲线弹出 Windows 标准“另存为”窗口，可选择文件夹并修改文件名；默认名使用原始文件基本名（实时数据使用当前变量）、分析组（如有）、显示模式及横轴单位，例如 `sample01_分析组 1_Tauc_eV.txt`。多条曲线只弹一次系统文件夹选择窗口，按各自原始文件名生成每曲线一个 TXT，已有同名文件追加 `_2`、`_3` 等序号，不覆盖。默认打开最后导出文件夹；首次可使用来源文件所在目录或采集保存目录。

处理、PL、AB 的曲线图标保存所有可见且有有效点的曲线；采集页原有保存区继续保存最新原始光谱。

曲线 TXT 的 Y 值保留 8 位有效数字，省略多余尾零，极小值使用科学计数法；这是限制导出舍入误差的格式选择（非零值相对误差约不超过 `5×10⁻⁸`），不代表仪器测量精度。它适用于强度、归一化、透过率、吸光度和 Tauc，避免固定小数位截断弱信号。波长/eV 默认 12 位有效数字，必要时增加至 17 位，保证严格递增并将横轴舍入误差控制在每个点最近相邻间距的百万分之一以内；像素索引保持整数。参数行注明实际输出精度。计算与绘图使用未舍入数据；原始采集 TXT 保持原有 12 位有效数字，供后续背景扣除和重新分析使用。

文件为 GB18030 编码，参数行以 `#` 开头，`>>>>>Begin Spectral Data<<<<<` 下一行直接开始 Tab 分隔的两列数值，不写 X/Y 列标题。X/Y 保留当前 nm、像素或 eV 横轴及原始强度、BG 扣除、归一化、透过率、吸光度或 Tauc 计算结果。导出完整计算点，不随图表缩放裁切、不使用绘图抽稀点；无效计算点保留为 `NaN`。参数包含单位、曲线名、来源文件、分析组、BG、计算公式，以及可获得的采集参数。

点击时固定数据，连续采集后续帧不会混入本次文件。全部文件写入并重新打开复验通过后，工具栏图标外框变为绿色；数据、显隐或显示方式改变后恢复普通工具栏样式。此导出不提交采集页单次历史，也不替代原始光谱保存。结果 TXT 明确标记为处理曲线，导入器会阻止其误作原始 PL/AB 光谱。

## Development and Validation

Run the complete test suite:

```powershell
cd OceanLab
python -m unittest discover -s tests -v
```

Runtime code lives in the `oceanlab` package. OceanDirect is dynamically loaded only by the hardware adapter. Simulation mode has no third-party runtime dependency.

## Windows 7 Legacy Build

The standard portable package targets Windows 10/11. A separate, isolated toolchain can produce a 64-bit Windows 7 compatibility build:

```powershell
cd OceanLab
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_win7_toolchain.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_win7_legacy.ps1 -Version "2.1.1"
```

The setup script installs Python 3.7.9 and PyInstaller 5.13.2 only under the repository's ignored `.build-win7` directory. It does not add Python to the system `PATH`. Python 3.7 avoids Python 3.8's Windows 7 dependency on KB2533623. The output is written to `release\OceanLab-2.1.1-win7-x64-legacy.zip`.

Python 3.7 is end-of-life, so the Legacy package is intended only for a required offline or controlled instrument computer. Physical OceanDirect compatibility must be validated on the actual Windows 7 system.

## Notes

- OceanLab is a Windows Tkinter desktop application, not a GitHub Pages website.
- Do not run OceanLab together with OceanView or another application that controls the same spectrometer.
- OceanDirect and OceanView are product names or trademarks of their respective owners. This project is not an official vendor product.
- See [NOTICE.md](NOTICE.md) for third-party components, toolbar assets, and copyright boundaries.
