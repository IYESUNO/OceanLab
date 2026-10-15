param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '2.1.2',
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$PreviousVersion = '2.1.1'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$BuildRoot = Join-Path (Split-Path -Parent $PSScriptRoot) '.build-win7'
$Compiler = Join-Path $BuildRoot 'inno-setup-6.2.2\ISCC.exe'
$Identity = 'OceanLab.Win7.QA.' + [Guid]::NewGuid().ToString('N')
$Work = Join-Path $BuildRoot $Identity
$InstallDir = Join-Path $Work 'installed'
$Payload = Join-Path $BuildRoot "installer-$Version\stage\OceanLab-$Version-win7-x64-legacy"
$OldPayload = Join-Path $Work 'old-payload'
$OldOutput = Join-Path $Work 'old'
$NewOutput = Join-Path $Work 'new'
$RegistryPath = "Software\Microsoft\Windows\CurrentVersion\Uninstall\${Identity}_is1"
$Registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryView]::Registry64)
$UserSettings = Join-Path $env:LOCALAPPDATA 'OceanLab\settings.json'
$SettingsHash = if (Test-Path -LiteralPath $UserSettings) { (Get-FileHash -LiteralPath $UserSettings).Hash } else { '' }
New-Item -ItemType Directory -Force -Path $Work, $OldPayload, $OldOutput, $NewOutput | Out-Null
$Script = Join-Path $Work 'installer.iss'
[IO.File]::WriteAllText($Script, [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'packaging\oceanlab_win7.iss')), (New-Object System.Text.UTF8Encoding($true)))

function Assert-QA([bool]$Condition, [string]$Description) {
    if (-not $Condition) { throw "FAILED: $Description" }
    Write-Host "PASS: $Description"
}
function Run-Setup([string]$Executable, [string[]]$Extra, [string]$Label, [bool]$ExpectSuccess = $true) {
    $Arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/LOG="' + (Join-Path $Work "$Label.log") + '"')) + $Extra
    $Process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru
    Assert-QA (($Process.ExitCode -eq 0) -eq $ExpectSuccess) "$Label (exit $($Process.ExitCode))"
}
function Read-InstalledVersion {
    $Key = $Registry.OpenSubKey($RegistryPath)
    if (-not $Key) { return '' }
    try { return $Key.GetValue('DisplayVersion') } finally { $Key.Dispose() }
}

# A small old payload exercises file ownership; the new payload is the actual app.
# The old fixture needs only a file to replace, so no historical build is required.
Copy-Item -LiteralPath (Join-Path $Payload "OceanLab-$Version.exe") -Destination (Join-Path $OldPayload "OceanLab-$PreviousVersion.exe")
Set-Content -LiteralPath (Join-Path $OldPayload 'obsolete-runtime.dll') -Value 'old program file' -Encoding ascii
Set-Content -LiteralPath (Join-Path $OldPayload '.oceanlab-installation.txt') -Value 'OceanLab.Win7.Managed.v1' -Encoding ascii
Set-Content -LiteralPath (Join-Path $Work 'outside-spectrum.csv') -Value 'external data' -Encoding ascii
Set-Content -LiteralPath (Join-Path $OldPayload '.oceanlab-managed-files.txt') -Value @("OceanLab-$PreviousVersion.exe", 'obsolete-runtime.dll', '.oceanlab-managed-files.txt', '.oceanlab-installation.txt', '..\outside-spectrum.csv', '') -Encoding UTF8
& $Compiler '/Q' "/DAppVersion=$PreviousVersion" "/DAppIdOverride=$Identity" "/DRunningMutex=Local\$Identity.Running" '/DTestMode=1' "/DPayloadDir=$OldPayload" "/DReleaseDir=$OldOutput" "/DProjectDir=$PSScriptRoot" $Script
if ($LASTEXITCODE -ne 0) { throw 'Old QA installer compilation failed.' }
& $Compiler '/Q' "/DAppVersion=$Version" "/DAppIdOverride=$Identity" "/DRunningMutex=Local\$Identity.Running" '/DTestMode=1' "/DPayloadDir=$Payload" "/DReleaseDir=$NewOutput" "/DProjectDir=$PSScriptRoot" $Script
if ($LASTEXITCODE -ne 0) { throw 'New QA installer compilation failed.' }
$OldSetup = Join-Path $OldOutput "OceanLab-$PreviousVersion-win7-x64-setup.exe"
$NewSetup = Join-Path $NewOutput "OceanLab-$Version-win7-x64-setup.exe"
try {
    Run-Setup $OldSetup @(('/DIR="' + $InstallDir + '"')) 'first-install'
    Assert-QA ((Read-InstalledVersion) -eq $PreviousVersion) 'initial uninstall registration'
    Set-Content -LiteralPath (Join-Path $InstallDir 'my-spectrum.csv') -Value '450,123' -Encoding ascii
    Run-Setup $NewSetup @() 'upgrade'
    Assert-QA ((Read-InstalledVersion) -eq $Version) 'upgrade reuses previous directory and registration'
    Assert-QA (-not (Test-Path -LiteralPath (Join-Path $InstallDir "OceanLab-$PreviousVersion.exe"))) 'old version executable removed'
    Assert-QA (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'obsolete-runtime.dll'))) 'obsolete managed runtime removed'
    Assert-QA (Test-Path -LiteralPath (Join-Path $Work 'outside-spectrum.csv')) 'unsafe manifest paths cannot delete external files'
    Assert-QA (Test-Path -LiteralPath (Join-Path $InstallDir 'my-spectrum.csv')) 'unmanaged spectrum preserved during upgrade'
    $Shell = New-Object -ComObject WScript.Shell
    $Link = $Shell.CreateShortcut((Join-Path $InstallDir 'test-shortcuts\OceanLab (Win7).lnk'))
    $Exe = Join-Path $InstallDir "OceanLab-$Version.exe"
    Assert-QA ($Link.TargetPath -eq $Exe -and $Link.WorkingDirectory -eq $InstallDir) 'shortcut updated to current executable'
    $SourceHash = (Get-FileHash -LiteralPath (Join-Path $Payload "OceanLab-$Version.exe")).Hash
    Assert-QA ((Get-FileHash -LiteralPath $Exe).Hash -eq $SourceHash) 'installed program matches packaged payload'
    $VersionProcess = Start-Process -FilePath $Exe -ArgumentList '--version' -WindowStyle Hidden -Wait -PassThru
    Assert-QA ($VersionProcess.ExitCode -eq 0) 'installed executable launches version diagnostic'
    Set-Content -LiteralPath (Join-Path $InstallDir 'README_WIN7.txt') -Value 'damaged' -Encoding ascii
    Run-Setup $NewSetup @() 'repair'
    Assert-QA ((Get-FileHash -LiteralPath (Join-Path $InstallDir 'README_WIN7.txt')).Hash -eq (Get-FileHash -LiteralPath (Join-Path $Payload 'README_WIN7.txt')).Hash) 'same version repairs managed files'
    Run-Setup $OldSetup @() 'downgrade' $false
    Assert-QA ((Read-InstalledVersion) -eq $Version) 'downgrade leaves current version unchanged'
    $TaskMutex = New-Object System.Threading.Mutex($false, "Local\$Identity.Running")
    try { Run-Setup $NewSetup @() 'running-program' $false } finally { $TaskMutex.Dispose() }
    Run-Setup (Join-Path $InstallDir 'unins000.exe') @() 'uninstall'
    Assert-QA ((Read-InstalledVersion) -eq '') 'uninstall registration removed'
    Assert-QA (-not (Test-Path -LiteralPath $Exe)) 'program removed on uninstall'
    Assert-QA (Test-Path -LiteralPath (Join-Path $InstallDir 'my-spectrum.csv')) 'uninstall preserves unmanaged data'
    $AfterHash = if (Test-Path -LiteralPath $UserSettings) { (Get-FileHash -LiteralPath $UserSettings).Hash } else { '' }
    Assert-QA ($AfterHash -eq $SettingsHash) 'real user preferences unchanged'
    Write-Host "Installer QA complete. Logs: $Work"
}
finally {
    # Only this unique test registration/install is eligible for cleanup.
    if (Read-InstalledVersion) {
        $Key = $Registry.OpenSubKey($RegistryPath)
        try { $CleanupDir = $Key.GetValue('Inno Setup: App Path') } finally { $Key.Dispose() }
        if ($CleanupDir -and [IO.Path]::GetFullPath($CleanupDir).StartsWith([IO.Path]::GetFullPath($Work) + '\', [StringComparison]::OrdinalIgnoreCase)) {
            $Cleanup = Join-Path $CleanupDir 'unins000.exe'
            if (Test-Path -LiteralPath $Cleanup) { Start-Process -FilePath $Cleanup -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -WindowStyle Hidden -Wait | Out-Null }
        }
    }
    $Registry.Dispose()
}
