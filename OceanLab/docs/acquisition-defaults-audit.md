# OceanLab / 本机 OceanView 采集核对

核对日期：2026-10-10。此记录区分已确认事实与待验证解释，不将历史信号偏移补偿视为采集修复。

## 对照对象

- 用户确认 20260902 的 BG 与 S1-03 两份文件均由 OceanLab 采集。
- 本机官方软件根目录：`C:\Program Files\Ocean Optics\OceanView\oceanview`。模块实际位于其下 `oceanview\modules`，不是根目录直接下的 `modules`。
- OceanLab 使用 OceanDirect，官方 OceanView 使用随程序提供的 OmniDriver / SPAM；相同开关名称不代表 SDK 实现相同。
- 所有检查均只读官方安装目录；未修改 SDK、驱动、固件、EEPROM 或校准。

## 已核实的默认配置与处理路径

检查本机 `com-oceanoptics-omnidriver-compatibility.jar` 中 `OOISpectrometerFeature` 构造与 `getData()`，以及 `com-oceanoptics-spam.jar` 中 `SpectralProcessor`：

| 项目 | 本机 OceanView 初始默认 | OceanLab 初始默认 / 两份文件 |
| --- | --- | --- |
| Electric-dark correction | 开启 | 开启 / true |
| Nonlinearity correction | 关闭 | 关闭 / false |
| Boxcar | 0 | 0 / 0 |

OceanView 的初始扫描平均为 1；这两份 OceanLab 文件为用户设置的 20。尚未获取 OceanView 当前会话保存配置，因此这里只比较程序初始默认，不能声称两次实际采集条件完全一致。

OceanLab `adapters/oceandirect.py` 写入配置并读回验证，随后调用 `get_formatted_spectrum()`；原始文件保存这份 SDK 返回值，没有额外手动暗谱扣除或 AB 偏移。AB 偏移功能仅修改分析副本。因此 BG 的大面积负值已存在于采集文件中，不是历史绘图生成的。

## 确定的告警差异

OceanView `getData()` 在电暗校正之前调用平均谱累积器的 `isSaturated()`，再运行 `SpectralProcessor`。OceanLab 2.0.4 `ui/app.py:refresh_plot()` 以 SDK 校正后峰值除以告警阈值 63000 显示饱和百分比。

BG 已有连续平顶 55037.2875 counts，但这一逻辑只显示 87.3608%。校正减去常数后，已经截顶的谱也可能显示低于 100%，所以该百分比不能证明未饱和。这是 OceanLab 告警判断的局限，不是已确认的 8009-count 偏移根因。修复需要同帧原始饱和状态，或明确标为“疑似截顶”的保守提示；不能用事后补偿伪造 ADC 饱和状态。

## 实际文件证据

目录：`D:\Workspace\Work\Note\Exp_DATA\HTHP\hBN5nm\D260807_16GPa1740C\01_RawData\09_AB\20260902`。

| 文件 | 平顶 counts | 最小值 counts | 负值点 | 连续低于 -100 counts |
| --- | ---: | ---: | ---: | ---: |
| D260807-BG-40um-100ms-20times_QEPB17271_20-26-25-116.txt | 55037.2875 | -8032.8125 | 241 | 238 |
| D260807-S1-40um-100ms-20times-03_QEPB17271_20-27-58-248.txt | 63046.6375 | -23.0125 | 4 | 0 |

两份文件均为 QEPB1727、100 ms、平均 20、Boxcar 0、触发 0、电暗开启、非线性关闭，1044 点。平台差为 8009.3500 counts；历史 AB 对 BG 加此常数只是估算补偿，截顶真实信号仍不可恢复。

SDK 输出中候选暗像素索引 `(0,1,2,3,1040,1041,1042,1043)` 的均值在两份文件均接近零。BG 在索引 1040 为 55037.2875，而 S1 对应值仅 20.7875；BG 其余候选点约为 -8000，呈现一个大正值与其余负值相互抵消的形态。

这是“一个暗像素异常拉高均值、全谱被多扣常数”的强线索，但 **该候选索引不是已从连接设备读回确认的 OceanDirect 暗像素列表**。不能据此硬编码 SDK 像素掩码或断言硬件损坏。

本机 OmniDriver 的 QE65000 配置使用 `(1024,1025,1026,1027,1040,1041,1042,1043)`；`QESpectrumExchange` 将 ADC 前十个像素搬到格式化数组尾部，光学 1024 点放在前部。不同库的排列有差异，不能直接将 OmniDriver 索引套入 OceanDirect 数组。

SPAM 的电暗校正对配置的暗像素求均值并减到全谱，没有在该路径中看到排除饱和暗像素的逻辑。仅凭“原版没有遇到”不能证明默认开关差异，也不能排除两种库的数据排列、饱和处理或实际采集条件差异。

## 2.0.5：复用 OceanLab 现有 OceanDirect 库

用户明确要求复用 OceanLab 的库而非另写算法。已撤回尚未交付的自写处理链，运行时没有新增算法、Java 桥接或第三方库。

本机 `C:\OceanDirect\Python\oceandirect\OceanDirectAPI.py` 及 `include\api\OceanDirectAPI.h` 已确认以下厂商接口：

| 需求 | 库内现成接口 | 本次采集使用方式 |
| --- | --- | --- |
| 扫描平均 | `set_scans_to_average()` | SDK 自动用于返回谱 |
| 电暗校正 | `set_electric_dark_correction_usage()` | SDK 自动用于返回谱 |
| 非线性校正 | `set_nonlinearity_correction_usage()` | SDK 自动用于返回谱 |
| Boxcar | `set_boxcar_width()` | SDK 自动用于返回谱 |
| 获取结果 | `get_formatted_spectrum()` | 每次返回一份厂商处理结果，原样保存 |
| 额外暗谱 / 非线性 / 平滑 | `get_dark_corrected_spectrum1/2()`、`get_nonlinearity_corrected_spectrum1/2()`、`boxcar_correct_spectrum()` | 存在于库中，但不额外调用以免重复处理或接口冲突 |

新的暗谱接口要求提供实测或已存储的暗谱，不能用它冒充 electric-dark correction。头文件将电暗/非线性 usage 开关列为旧接口，并明确警告与部分新校正接口冲突；旧接口标记不证明当前单一路径错误，也不是混用接口的理由。

本机 OceanView 另确认初始积分 100000 us、平均 1、Boxcar 0。2.0.5 统一 GUI 新设置与 CLI 单次诊断为上述默认；已有用户预设不强制覆盖。这两份历史文件原本就是 100 ms，所以 10 → 100 ms 的默认修改不能解释其旧偏移。

同时修复最终状态只做逐项即时确认的局限：设置所有选项后再次读回全部支持项，既检测 SDK 选项联动，也正确记录未显式设置但被联动改变的实际值；核心参数在最终阶段也复验。配置失败不生成采集上下文。

QE65000 使用 SDK 返回的 ADC 最大强度作参考，已核对 OceanView 在 `getData()` 初始化中设置 `saturationThreshold = getMaxIntensityCounts()`。2.0.5 曾以连续平顶作“疑似截顶”红色提示，不伪造同帧原始饱和标志。该轮 134 项离线测试覆盖厂商单次委托、无额外处理、最终配置联动、GUI/CLI 初始配置和平台提示。真机数值对齐仍需下述验证。

2.1.1 按用户最新要求取消界面的饱和判断文字：仅显示有限原始信号峰值 / 设备满量程 × 100%，包括超过 100% 的数值。保留原有比例颜色提示：低于 80% 绿色、80% 至低于 95% 黄色、95% 及以上红色，不按平台检测强制标红。AB 侧栏不再显示截顶警告，偏移详情仅显示平台数值与点数。平台检测继续用于偏移估算及导出记录；这一展示调整不改变采集配置、SDK 返回值或物理可恢复性，也不构成新的真机验证。

## 2.1.2 逐扫描停止更新（2026-10-14）

只读核对本机 OceanView 的 OOISpectrometerFeature.getData()：每次新建 SpectrumAccumulator，逐次读取原始计数，在第 2 次及以后扫描之前检查 isStoppingAverages()，最后处理平均谱。这是停止平均路径；尚未将全部界面按钮调用逐个映射为该标志，不声称能中断正在进行的积分。

OceanDirect Python 3.1.4 没有平均循环停止回调。Advanced.abort_acquisition() 的说明限制为少数支持缓冲采集 / 空闲状态的设备，不能作为 QE65000 通用取消功能，也不应并发调用 SDK 打断阻塞读谱。

按用户要求，2.1.2 改为 SDK 平均 1 次、逐扫描累加求平均，共享事件只传递停止请求；仪器读取及校正始终由原设备进程串行执行。暂停 / 取消等待当前扫描后跳过剩余次数，取消批次不显示或保存，保留连接。平均 1 次仍使用原 get_formatted_spectrum() 路线。

多次平均保留 SDK 电暗校正，暂时关闭非线性及 Boxcar。线性电暗减法与平均可交换次序，舍入及 SDK 实现差异仍须真机核对。平均后先关闭电暗开关，调用官方 nonlinearity_correct_spectrum2(零暗谱, 平均谱)，避免重复电暗 / 存储暗谱扣除，再调用 boxcar_correct_spectrum()；不另行采谱、不修改存储暗谱、不实现自有校正公式。finally 恢复并复验原平均次数、Boxcar、非线性及电暗，恢复失败走设备异常释放路径。元数据只记录完整完成的平均次数。

这次对齐停止粒度，不等于已证明 OmniDriver / SPAM 与 OceanDirect 数值完全一致。离线回归覆盖第 1 次扫描暂停后跳过剩余 19 次、保留同一设备进程并重新完整平均，以及先平均再非线性 / Boxcar、异常与恢复失败。

## 尚需真机验证

本轮探测 OceanDirect API 3.1.4 正常加载，USB devices: 0。尚不能获得设备实际暗像素列表、同帧 ADC 数据与校正前饱和状态。

验证应在同一光路、相同积分 / 平均 / Boxcar 下，先关闭 OceanView 释放设备，再分别核对两软件的实际开关与输出；需要同时观察电暗开 / 关的基线变化和低积分下的平台情况。任何额外测试采集均应明确发起，不在连接或参数确认时隐式采集。若 SDK 无法提供同帧 ADC，不以两个不同采集帧冒充同帧对照。

官方说明作为背景：[Ocean Optics Software FAQs](https://www.oceanoptics.com/faq/software-faqs/) 描述电暗校正使用暗像素均值，并指出饱和影响有效测量。具体本机初始配置和处理顺序以上述安装代码为依据。
