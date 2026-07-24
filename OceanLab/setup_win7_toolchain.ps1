param(
    [string]$PythonVersion = "3.7.9",
    [string]$PyInstallerVersion = "5.13.2",
    [string]$PyInstallerHooksVersion = "2024.7"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LegacyRoot = Join-Path $ProjectRoot ".build-win7"
$PythonRoot = Join-Path $LegacyRoot "python37"
$PythonExe = Join-Path $PythonRoot "python.exe"
$DownloadsRoot = Join-Path $LegacyRoot "downloads"
$InstallerPath = Join-Path $DownloadsRoot "python-$PythonVersion-amd64.exe"
$InstallerUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$ExpectedInstallerSha256 = "e69ed52afb5a722e5c56f6c21d594e85c17cb29f12f18bb69751cf1714e0f987"
$LegacyRuntimeRoot = Join-Path $LegacyRoot "runtime-vc142"

New-Item -ItemType Directory -Force -Path $DownloadsRoot | Out-Null

if (-not (Test-Path -LiteralPath $PythonExe)) {
    if (-not (Test-Path -LiteralPath $InstallerPath)) {
        Write-Host "Downloading Python $PythonVersion from python.org..."
        Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath
    }

    $InstallerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerPath).Hash.ToLowerInvariant()
    if ($InstallerHash -ne $ExpectedInstallerSha256) {
        throw "Python installer SHA-256 mismatch: $InstallerHash"
    }
    $Signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
    if ($Signature.Status -ne "Valid" -or $Signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        throw "Python installer signature is not valid: $($Signature.Status)"
    }

    $InstallerArguments = @(
        "/quiet",
        "InstallAllUsers=0",
        "TargetDir=$PythonRoot",
        "Include_doc=0",
        "Include_dev=0",
        "Include_debug=0",
        "Include_launcher=0",
        "InstallLauncherAllUsers=0",
        "Include_pip=1",
        "Include_symbols=0",
        "Include_tcltk=1",
        "Include_test=0",
        "Include_tools=1",
        "AssociateFiles=0",
        "Shortcuts=0",
        "PrependPath=0",
        "AppendPath=0",
        "CompileAll=0"
    )
    $InstallerProcess = Start-Process `
        -FilePath $InstallerPath `
        -ArgumentList $InstallerArguments `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($InstallerProcess.ExitCode -ne 0) {
        throw "Python installer exited with code $($InstallerProcess.ExitCode)"
    }
}

$InstalledVersion = & $PythonExe -c "import platform; print(platform.python_version())"
if ($InstalledVersion -ne $PythonVersion) {
    throw "Expected Python $PythonVersion, found $InstalledVersion"
}
& $PythonExe -c "import tkinter; print('Tk', tkinter.TkVersion)"
& $PythonExe -m pip install `
    --disable-pip-version-check `
    --no-warn-script-location `
    "pyinstaller==$PyInstallerVersion" `
    "pyinstaller-hooks-contrib==$PyInstallerHooksVersion"

$InstalledPyInstaller = & $PythonExe -m PyInstaller --version
if ($InstalledPyInstaller -ne $PyInstallerVersion) {
    throw "Expected PyInstaller $PyInstallerVersion, found $InstalledPyInstaller"
}

$RuntimeFiles = @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
$VisualStudioRedistRoot = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\2019\BuildTools\VC\Redist\MSVC"
$RuntimeSource = $null
if (Test-Path -LiteralPath $VisualStudioRedistRoot) {
    $RuntimeSource = Get-ChildItem -LiteralPath $VisualStudioRedistRoot -Directory |
        Sort-Object Name -Descending |
        ForEach-Object {
            $Candidate = Join-Path $_.FullName "x64\Microsoft.VC142.CRT"
            $AllPresent = $true
            foreach ($RuntimeFile in $RuntimeFiles) {
                if (-not (Test-Path -LiteralPath (Join-Path $Candidate $RuntimeFile))) {
                    $AllPresent = $false
                    break
                }
            }
            if ($AllPresent) {
                $Candidate
            }
        } |
        Select-Object -First 1
}
if (-not $RuntimeSource) {
    throw "Visual Studio 2019 x64 VC142 runtime files were not found on the build computer."
}

New-Item -ItemType Directory -Force -Path $LegacyRuntimeRoot | Out-Null
foreach ($RuntimeFile in $RuntimeFiles) {
    $SourcePath = Join-Path $RuntimeSource $RuntimeFile
    $Version = (Get-Item -LiteralPath $SourcePath).VersionInfo.FileVersion
    $Signature = Get-AuthenticodeSignature -LiteralPath $SourcePath
    if ($Version -notlike "14.29.*") {
        throw "$RuntimeFile must be the Windows 7-compatible VC142 14.29 runtime, found $Version"
    }
    if ($Signature.Status -ne "Valid" -or $Signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
        throw "$RuntimeFile does not have a valid Microsoft signature"
    }
    Copy-Item -LiteralPath $SourcePath -Destination $LegacyRuntimeRoot -Force
}

Write-Host "Windows 7 toolchain ready:"
Write-Host "  Python:      $PythonExe ($InstalledVersion)"
Write-Host "  PyInstaller: $InstalledPyInstaller"
Write-Host "  VC++ runtime: VC142 14.29 ($LegacyRuntimeRoot)"
