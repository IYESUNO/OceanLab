# OceanLab Spectroscopy Software — Windows 7 Branch

OceanLab `2.0.0` on this branch is the dedicated 64-bit Windows 7 compatibility edition. It preserves the complete acquisition, processing, PL fluorescence, and AB absorption feature set while using an isolated legacy Python toolchain. The standard Windows 10/11 source and release remain on the `win64` branch.

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

For the portable edition, extract the complete ZIP archive and run `OceanLab.exe`. Python is bundled, while OceanDirect is not.

## OceanDirect Driver

For physical instruments, obtain and install the official 64-bit OceanDirect SDK and USB driver from Ocean Insight/Ocean Optics and comply with their license terms. OceanLab does not bundle, copy, or modify the SDK, drivers, firmware, EEPROM, or calibration data.

OceanLab checks `OCEANDIRECT_HOME`, `C:\OceanDirect`, and common installation locations. You can also select the SDK root from the lower-right corner of the application. Simulation mode remains available without OceanDirect.

## Main Features

- Four workspaces: Acquisition, Processing, PL Fluorescence, and AB Absorption.
- Single and continuous acquisition, three acquisition presets, electric-dark correction, and parameter status feedback.
- Raw intensity, dark subtraction, peak normalization, transmittance, and absorbance processing.
- Multi-file PL overlays, optional BG subtraction, AB BG reference, and optional instrument dark spectrum.
- Import support for OceanView GB18030 TXT, OceanLab CSV/TXT, and generic two-column spectra.
- Save-time reopen verification for CSV/TXT; TXT exports use an OceanView-compatible two-column data format.

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
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_win7_legacy.ps1 -Version "2.0.0"
```

The setup script installs Python 3.7.9 and PyInstaller 5.13.2 only under the repository's ignored `.build-win7` directory. It does not add Python to the system `PATH`. Python 3.7 avoids Python 3.8's Windows 7 dependency on KB2533623. The output is written to `release\OceanLab-2.0.0-win7-x64-legacy.zip`.

Python 3.7 is end-of-life, so the Legacy package is intended only for a required offline or controlled instrument computer. Physical OceanDirect compatibility must be validated on the actual Windows 7 system.

## Notes

- OceanLab is a Windows Tkinter desktop application, not a GitHub Pages website.
- Do not run OceanLab together with OceanView or another application that controls the same spectrometer.
- OceanDirect and OceanView are product names or trademarks of their respective owners. This project is not an official vendor product.
- See [NOTICE.md](NOTICE.md) for third-party components, toolbar assets, and copyright boundaries.
