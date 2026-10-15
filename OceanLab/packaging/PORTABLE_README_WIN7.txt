OceanLab 2.1.2 Windows 7 x64 Legacy Edition
================================================

Compatibility
-------------
This package is an isolated compatibility build for 64-bit Windows 7.
The standard OceanLab package remains the recommended edition for Windows 10/11.

The Legacy edition embeds Python 3.7.9, which reached end of life and no longer
receives security updates. Use this package only on the required offline or
controlled instrument computer. Do not expose that computer directly to
untrusted networks.

Unlike Python 3.8, this runtime does not require the Windows 7 KB2533623 update
for its DLL loading behavior. Windows 7 Service Pack 1 is still recommended.

Pause / Cancel (2.1.2)
----------------------
AB groups use pale cards with group show/hide and remove icons in each header.
Both icons precede the title; long text is ellipsized with a full-title tooltip.
Group deletion requires confirmation (default No). Removing the last group
resets to Analysis group 1; the sole empty group's delete icon is disabled.
Group hiding retains individual visibility choices and excludes that group
from chart export. Group removal keeps the source files.
Tauc export filenames distinguish Tauc_Direct_2 and Tauc_Indirect_1-2.
Normal pause and single cancellation retain the instrument connection.
Multi-scan averaging checks pause after each scan, as OceanView does.
Wait for the current scan or configuration to finish, skip remaining scans,
discard cancelled data, then start another acquisition directly. Timeout / hardware failure
still releases the connection. Disconnect and exit still interrupt waits.

Compact PL / AB Layout (2.1.1)
------------------------------
Sample names are read-only. Hover for 600 ms to see only the full source
filename, with no duplicate curve name. Compact remove buttons leave more width for names. Click a name,
or press Enter / Space on it, to select the active curve.
AB combines result mode and nm/eV controls; clear actions are under Manage.
This same-version update uses Lucide eye / eye-off, crosshair, palette and
trash icons in both lists. Blue crosshair means active; palette shows the
curve color. Icons include hover tips, keyboard support and DPI-scaled images.
No extra runtime dependency. Sources and licenses: assets/history_icons.
Saturation shows the measured peak / device full-scale percentage only,
including values above 100%, without plateau judgement text. Original ratio
colors remain: green below 80%, yellow from 80% to below 95%, red at 95% or above.

Tauc Modes (2.1.0)
------------------
Processing and AB absorption offer direct allowed (A*hnu)^2 and indirect
allowed sqrt(A*hnu), including correction previews and curve TXT export.
Negative absorbance is invalid for the square-root mode; gaps remain NaN.
Previous Tauc preferences retain the direct mode.

Getting Started
---------------
1. Fully extract the ZIP archive. Do not run it from an archive preview window.
2. Keep OceanLab-2.1.2.exe and all adjacent files and directories together.
3. Double-click OceanLab-2.1.2.exe.
4. Simulation mode does not require OceanDirect.

OceanLab automatically checks C:\OceanDirect and the common 64-bit
"Program Files\Ocean Optics\OceanDirect" / "Ocean Insight\OceanDirect"
locations. The SDK can still be selected manually from the lower-right corner.

OceanDirect and Physical Instruments
------------------------------------
OceanDirect and USB drivers are not included. Install a 64-bit OceanDirect
release and device driver that are compatible with Windows 7 and the target
spectrometer. Compatibility with a physical instrument must be validated on
the actual Windows 7 computer.

The Windows 7-compatible Microsoft VC142 14.29 runtime DLLs required by
OceanDirect.dll are included beside the OceanLab executable and loaded application-locally.
No separate Visual C++ Redistributable installation is normally required.

OceanLab does not modify instrument firmware, EEPROM, or calibration data.
Do not run OceanLab and OceanView at the same time when both would access the
same spectrometer.

Configuration and Logs
----------------------
User settings: %LOCALAPPDATA%\OceanLab\settings.json
Runtime log:   %TEMP%\OceanLab\OceanLab.log

Build Information
-----------------
Application: OceanLab 2.1.2
Runtime:     CPython 3.7.9 x64
Packager:    PyInstaller 5.13.2
Target:      Windows 7 x64 Legacy

AB 信号偏移校正试用
------------------
在 AB 吸收页导入 BG 和样品，检测到连续明显负值、多点平台，并有同组匹配参数的
可靠平台基准时，默认直接应用估算偏移；主图显示“自动估算”。无可靠基准时只提示。
“信号偏移校正…”采用左参数、右大图，默认只显示校正结果，可打开校正前对照。
手动修改基准后点“更新预览”，再点“应用修改并关闭”。
BG 与每份样品分别校正，支持“恢复此信号原值”，不修改原始文件。
恢复后不会因重绘或重复导入重新应用；重新读取该信号才重新评估。
估算值不能代替仪器校准；饱和截顶信号不能通过偏移恢复。
2.1.2 绿色包和安装包均包含此功能。
