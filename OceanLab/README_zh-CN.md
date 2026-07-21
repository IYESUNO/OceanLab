# OceanLab 光谱采集软件

OceanLab 1.0.1 是面向 Windows 10/11 的轻量级光谱采集与分析软件，支持模拟模式、OceanDirect 真机采集、实时处理、PL 荧光叠图和 AB 吸收分析。

## 快速开始

源码运行：

```powershell
cd OceanLab
python launcher.py
```

没有仪器时可以选择模拟模式。命令行检查版本或设备：

```powershell
python launcher.py --version
python launcher.py --probe --sdk-home C:\OceanDirect
```

绿色版解压后运行 `OceanLab.exe`，不需要另外安装 Python。绿色版压缩包不包含 OceanDirect。

## OceanDirect 驱动

连接真实仪器时，请从 Ocean Insight/Ocean Optics 官方渠道自行获取并安装 64 位 OceanDirect SDK 和 USB 驱动，并遵守其许可条款。OceanLab 不包含、复制或修改该 SDK、驱动、固件、EEPROM 或校准数据。

软件会检查 `OCEANDIRECT_HOME`、`C:\OceanDirect` 及常见安装目录；也可以在界面右下角手动选择 SDK 根目录。未安装驱动时仍可使用模拟模式。

## 主要功能

- 四个工作区：采集、处理、PL荧光、AB吸收。
- 单次/连续采集、三组采集预设、暗噪声校正和参数状态提示。
- 原始强度、暗谱扣除、归一化、透过率和吸光度处理。
- 多文件 PL 叠图、BG 背景扣除、AB 的 BG 参考和可选仪器暗谱。
- 兼容 OceanView GB18030 TXT、OceanLab CSV/TXT 和通用两列光谱文件。
- CSV/TXT 保存后自动重新打开校验；TXT 使用 OceanView 兼容的两列数据格式。

## 开发与验证

运行全部测试：

```powershell
cd OceanLab
python -m unittest discover -s tests -v
```

运行时代码位于 `oceanlab` 包中。OceanDirect 只在硬件适配器中动态加载，模拟模式不需要第三方运行时依赖。

## 注意事项

- OceanLab 是 Windows Tkinter 桌面软件，不是 GitHub Pages 网站。
- 不要与 OceanView 或其他占用同一台光谱仪的软件同时运行。
- OceanDirect 和 OceanView 是其权利人的产品名称/商标；本项目不代表官方产品。
- 第三方组件、图标资源和版权边界见 [NOTICE_zh-CN.md](NOTICE_zh-CN.md)。
