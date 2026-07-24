# OceanLab Spectroscopy Software

OceanLab was developed end-to-end by successive GPT Sol coding agents, guided by the user's requirements and real-world validation. Their work covers the architecture, implementation, testing, documentation, and release process behind the project.

The standard OceanLab `1.0.1` Windows 10/11 source remains on the `win64` branch. This `win7` branch contains the isolated OceanLab `2.0.0` Windows 7 x64 compatibility edition.

- User guide: [OceanLab/README.md](OceanLab/README.md)
- Third-party and copyright notice: [OceanLab/NOTICE.md](OceanLab/NOTICE.md)
- Driver note: OceanDirect SDK and USB drivers are not bundled. Obtain them from the official vendor when using a physical spectrometer.
- Legacy note: the standard package targets Windows 10/11; an isolated Windows 7 x64 build workflow is documented in the user guide.

Quick start:

```powershell
cd OceanLab
python launcher.py
```

This repository contains a Windows desktop application, not a GitHub Pages website. Review the third-party and copyright notice before redistribution.
