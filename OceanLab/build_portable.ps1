param(
    [string]$Version = "2.0.2"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildRoot = Join-Path $ProjectRoot ".build"
$VenvPython = Join-Path $BuildRoot "venv\Scripts\python.exe"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$SpecRoot = Join-Path $BuildRoot "spec"
$ReleaseRoot = Join-Path $PSScriptRoot "release"
$BundleName = "OceanLab-$Version-win64-portable"
$ExecutableName = "OceanLab-$Version.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $BuildRoot "venv")
    & $VenvPython -m pip install --disable-pip-version-check "pyinstaller==6.21.0"
}

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot, $SpecRoot, $ReleaseRoot | Out-Null

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name OceanLab `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    --version-file (Join-Path $PSScriptRoot "packaging\version_info.txt") `
    --icon (Join-Path $PSScriptRoot "assets\app_icons\oceanlab-icon-spectrum.ico") `
    --add-data "$(Join-Path $PSScriptRoot 'assets');assets" `
    --hidden-import ctypes `
    --hidden-import getpass `
    --hidden-import platform `
    (Join-Path $PSScriptRoot "launcher.py")

$BundleRoot = Join-Path $BuildRoot $BundleName
if (Test-Path -LiteralPath $BundleRoot) {
    Remove-Item -LiteralPath $BundleRoot -Recurse -Force
}
Move-Item -LiteralPath (Join-Path $DistRoot "OceanLab") -Destination $BundleRoot
Move-Item `
    -LiteralPath (Join-Path $BundleRoot "OceanLab.exe") `
    -Destination (Join-Path $BundleRoot $ExecutableName)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination $BundleRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Launch_OceanLab.cmd") -Destination $BundleRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "packaging\PORTABLE_README.txt") -Destination $BundleRoot

$ZipPath = Join-Path $ReleaseRoot "$BundleName.zip"
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $BundleRoot -DestinationPath $ZipPath -CompressionLevel Optimal
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath
$HashLine = "$($Hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($ZipPath))"
Set-Content -LiteralPath (Join-Path $ReleaseRoot "$BundleName.sha256.txt") -Value $HashLine -Encoding ascii

Get-Item -LiteralPath $ZipPath
