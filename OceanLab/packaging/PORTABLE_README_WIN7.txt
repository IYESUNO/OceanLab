OceanLab 2.0.1 Windows 7 x64 Legacy Edition
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

Getting Started
---------------
1. Fully extract the ZIP archive. Do not run it from an archive preview window.
2. Keep OceanLab-2.0.1.exe and all adjacent files and directories together.
3. Double-click OceanLab-2.0.1.exe.
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
Application: OceanLab 2.0.1
Runtime:     CPython 3.7.9 x64
Packager:    PyInstaller 5.13.2
Target:      Windows 7 x64 Legacy
