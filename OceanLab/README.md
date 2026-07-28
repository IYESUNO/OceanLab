# OceanLab Spectroscopy Software

OceanLab `2.0.2` is a lightweight spectroscopy acquisition and analysis application for 64-bit Windows 10/11. It supports simulation, OceanDirect hardware acquisition, live processing, PL fluorescence overlays, and AB absorption analysis.

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

## Notes

- OceanLab is a Windows Tkinter desktop application, not a GitHub Pages website.
- Do not run OceanLab together with OceanView or another application that controls the same spectrometer.
- OceanDirect and OceanView are product names or trademarks of their respective owners. This project is not an official vendor product.
- See [NOTICE.md](NOTICE.md) for third-party components, toolbar assets, and copyright boundaries.
