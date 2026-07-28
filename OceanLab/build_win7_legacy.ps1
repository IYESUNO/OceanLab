param(
    [string]$Version = "2.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LegacyRoot = Join-Path $ProjectRoot ".build-win7"
$PythonExe = Join-Path $LegacyRoot "python37\python.exe"
$DistRoot = Join-Path $LegacyRoot "dist"
$WorkRoot = Join-Path $LegacyRoot "work"
$SpecRoot = Join-Path $LegacyRoot "spec"
$BundleName = "OceanLab-$Version-win7-x64-legacy"
$BundleRoot = Join-Path $LegacyRoot $BundleName
$ReleaseRoot = Join-Path $PSScriptRoot "release"
$ZipPath = Join-Path $ReleaseRoot "$BundleName.zip"
$HashPath = Join-Path $ReleaseRoot "$BundleName.sha256.txt"
$LegacyRuntimeRoot = Join-Path $LegacyRoot "runtime-vc142"
$ExecutableName = "OceanLab-$Version.exe"

function Assert-PathInsideRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    $ResolvedPath = [IO.Path]::GetFullPath($Path)
    $ResolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar) +
        [IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedPath.StartsWith($ResolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside legacy build root: $ResolvedPath"
    }
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Windows 7 toolchain not found. Run .\setup_win7_toolchain.ps1 first."
}

$PythonVersion = & $PythonExe -c "import platform; print(platform.python_version())"
if ($PythonVersion -ne "3.7.9") {
    throw "Windows 7 build requires Python 3.7.9, found $PythonVersion"
}
$AppVersion = & $PythonExe (Join-Path $PSScriptRoot "launcher.py") --version
if ($AppVersion -ne "OceanLab $Version") {
    throw "Requested version $Version does not match application version: $AppVersion"
}
$PyInstallerVersion = & $PythonExe -m PyInstaller --version
if ($PyInstallerVersion -ne "5.13.2") {
    throw "Windows 7 build requires PyInstaller 5.13.2, found $PyInstallerVersion"
}

Write-Host "Running tests with Python $PythonVersion..."
& $PythonExe -m unittest discover -s (Join-Path $PSScriptRoot "tests") -v
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.7 test suite failed"
}

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot, $SpecRoot, $ReleaseRoot | Out-Null

$PreviousNoUserSite = $env:PYTHONNOUSERSITE
$PreviousAppData = $env:APPDATA
$PreviousPyInstallerConfig = $env:PYINSTALLER_CONFIG_DIR
try {
    # Keep the legacy build isolated from packages and paths in the user's
    # roaming Python installation.
    $LegacyAppData = Join-Path $LegacyRoot "appdata"
    $LegacyPyInstallerConfig = Join-Path $LegacyRoot "pyinstaller-cache"
    New-Item -ItemType Directory -Force -Path $LegacyAppData, $LegacyPyInstallerConfig | Out-Null
    $env:PYTHONNOUSERSITE = "1"
    $env:APPDATA = $LegacyAppData
    $env:PYINSTALLER_CONFIG_DIR = $LegacyPyInstallerConfig
    & $PythonExe -m PyInstaller `
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
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller Windows 7 build failed"
    }
}
finally {
    $env:PYTHONNOUSERSITE = $PreviousNoUserSite
    $env:APPDATA = $PreviousAppData
    $env:PYINSTALLER_CONFIG_DIR = $PreviousPyInstallerConfig
}

Assert-PathInsideRoot -Path $BundleRoot -Root $LegacyRoot
if (Test-Path -LiteralPath $BundleRoot) {
    Remove-Item -LiteralPath $BundleRoot -Recurse -Force
}
Move-Item -LiteralPath (Join-Path $DistRoot "OceanLab") -Destination $BundleRoot
$BuiltExecutable = Join-Path $BundleRoot "OceanLab.exe"
$VersionedExecutable = Join-Path $BundleRoot $ExecutableName
Move-Item -LiteralPath $BuiltExecutable -Destination $VersionedExecutable

# Windows Explorer caches executable icons aggressively by path and filename,
# especially on Windows 7. A versioned filename avoids reusing the stale
# OceanLab.exe cache entry when a new portable package is extracted.
Add-Type -AssemblyName System.Drawing
$NativeIconReader = Add-Type -MemberDefinition @"
[DllImport("user32.dll", CharSet = CharSet.Unicode)]
public static extern uint PrivateExtractIcons(
    string file,
    int index,
    int width,
    int height,
    IntPtr[] icons,
    uint[] ids,
    uint count,
    uint flags
);

[DllImport("user32.dll")]
public static extern bool DestroyIcon(IntPtr handle);
"@ -Name "NativeIconReader" -Namespace "OceanLab.Build" -PassThru
$IconHandles = New-Object IntPtr[] 1
$IconIds = New-Object UInt32[] 1
$ExtractedIconCount = $NativeIconReader::PrivateExtractIcons(
    $VersionedExecutable,
    0,
    32,
    32,
    $IconHandles,
    $IconIds,
    1,
    0
)
if ($ExtractedIconCount -ne 1 -or $IconHandles[0] -eq [IntPtr]::Zero) {
    throw "Could not extract the icon resource from the packaged executable."
}
$EmbeddedIcon = [System.Drawing.Icon]::FromHandle($IconHandles[0]).Clone()
try {
    $EmbeddedBitmap = $EmbeddedIcon.ToBitmap()
    try {
        if ($EmbeddedBitmap.Width -ne 32 -or $EmbeddedBitmap.Height -ne 32) {
            throw "Packaged executable does not contain the expected 32x32 icon resource."
        }
    }
    finally {
        $EmbeddedBitmap.Dispose()
    }
}
finally {
    $EmbeddedIcon.Dispose()
    $NativeIconReader::DestroyIcon($IconHandles[0]) | Out-Null
}

$RuntimeFiles = @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
foreach ($RuntimeFile in $RuntimeFiles) {
    $RuntimePath = Join-Path $LegacyRuntimeRoot $RuntimeFile
    if (-not (Test-Path -LiteralPath $RuntimePath)) {
        throw "Windows 7 VC++ runtime is incomplete. Run .\setup_win7_toolchain.ps1 again."
    }
    Copy-Item -LiteralPath $RuntimePath -Destination $BundleRoot -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination $BundleRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "Launch_OceanLab.cmd") -Destination $BundleRoot
Copy-Item `
    -LiteralPath (Join-Path $PSScriptRoot "packaging\PORTABLE_README_WIN7.txt") `
    -Destination (Join-Path $BundleRoot "README_WIN7.txt")

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -LiteralPath $BundleRoot -DestinationPath $ZipPath -CompressionLevel Optimal
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath
$HashLine = "$($Hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($ZipPath))"
Set-Content -LiteralPath $HashPath -Value $HashLine -Encoding ascii

Write-Host "Windows 7 Legacy package created:"
Get-Item -LiteralPath $ZipPath, $HashPath
