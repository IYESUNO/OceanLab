OceanLab 2.0.2 Portable Edition
================================

Getting Started
---------------
1. Extract the complete ZIP archive. Do not run the application from an archive preview window.
2. Double-click OceanLab-2.0.2.exe.
3. Simulation mode requires no additional software.
4. For a physical instrument, separately install the official 64-bit OceanDirect SDK and USB driver.

OceanDirect
-----------
OceanDirect is not included. OceanLab checks:
- The last user-selected and validated SDK directory
- The OCEANDIRECT_HOME environment variable
- C:\OceanDirect
- Common Ocean Insight/Ocean Optics locations under Program Files

If OceanDirect is not found, use “Select OceanDirect...” in the lower-right corner and choose the SDK root directory.

Configuration and Logs
----------------------
User settings: %LOCALAPPDATA%\OceanLab\settings.json
Runtime log:   %TEMP%\OceanLab\OceanLab.log

Acquisition Presets
-------------------
Each of the three presets stores integration time, scan averaging, current variable, fixed filename prefix and suffix, optional trailing number, file format, destination, suffix mode, and sequence width. Fixed filename fields support {integration}, {averages}, and {number}. Advanced acquisition parameters are not currently bound to presets.

On a fresh installation, the fixed filename suffix defaults to {integration}-{averages}-{number}. The Default button restores only that suffix field. It does not modify the prefix, current variable, or trailing number.

Single-Acquisition History
--------------------------
During the current session, every preset independently retains its three most recent saved and verified single spectra. Repeated unsaved acquisitions replace only the pending curve. Continuous acquisition displays one live curve and does not enter single-acquisition history.

The Shared option beside Current Variable lets all three presets use one variable name while preserving their private values for later restoration.

Workspaces
----------
Acquisition controls the instrument and saves files. Processing operates on the current live spectrum. PL Fluorescence overlays multiple sample spectra with an optional BG file. AB Absorption accepts multiple sample signals, one BG reference, and an optional instrument dark spectrum.

OceanLab TXT exports use a GB18030 information header followed by wavelength and intensity columns in an OceanView-compatible layout. The importer also supports legacy OceanLab CSV/TXT and generic two-column files.

Uninstall
---------
Delete the complete OceanLab directory. To remove personal settings as well, delete:
%LOCALAPPDATA%\OceanLab

Important
---------
- Do not run OceanView or another application that controls the same spectrometer at the same time.
- A green Save button and a verified-success message indicate a successful save.
- OceanLab does not modify instrument firmware, EEPROM, or calibration coefficients.
