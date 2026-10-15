param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '2.1.2',
    [switch]$SkipPortableBuild
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$BuildRoot = Join-Path (Split-Path -Parent $PSScriptRoot) '.build-win7'
$Compiler = Join-Path $BuildRoot 'inno-setup-6.2.2\ISCC.exe'
if (-not (Test-Path -LiteralPath $Compiler)) {
    & (Join-Path $PSScriptRoot 'setup_win7_installer_toolchain.ps1')
}
if (-not $SkipPortableBuild) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build_win7_legacy.ps1') -Version $Version
    if ($LASTEXITCODE -ne 0) { throw 'Portable build failed.' }
}
$ReleaseRoot = Join-Path $PSScriptRoot 'release'
$BundleName = "OceanLab-$Version-win7-x64-legacy"
$Zip = Join-Path $ReleaseRoot "$BundleName.zip"
$ExpectedHash = ((Get-Content -LiteralPath (Join-Path $ReleaseRoot "$BundleName.sha256.txt") -Encoding UTF8) -split '\s+')[0]
if ((Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash -ne $ExpectedHash) {
    throw 'Portable ZIP checksum does not match.'
}
$Work = Join-Path $BuildRoot ("installer-" + $Version)
$Stage = Join-Path $Work 'stage'
# Resolve and verify the exact workspace build path before recursive cleanup.
$ResolvedBuild = [IO.Path]::GetFullPath($BuildRoot).TrimEnd('\') + '\'
if (-not [IO.Path]::GetFullPath($Stage).StartsWith($ResolvedBuild, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Installer stage is outside build root.'
}
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Expand-Archive -LiteralPath $Zip -DestinationPath $Stage
$Payload = Join-Path $Stage $BundleName
$Executable = Join-Path $Payload "OceanLab-$Version.exe"
if ((Get-Item -LiteralPath $Executable).VersionInfo.ProductVersion -ne $Version) {
    throw 'Packaged executable version does not match.'
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'packaging\INSTALLER_README_WIN7.txt') -Destination $Payload
Copy-Item -LiteralPath (Join-Path $BuildRoot 'inno-setup-6.2.2\license.txt') -Destination (Join-Path $Payload 'INNO_SETUP_LICENSE.txt')
$Files = @(Get-ChildItem -LiteralPath $Payload -Recurse -File | ForEach-Object { $_.FullName.Substring($Payload.Length + 1) })
$Files += @('.oceanlab-managed-files.txt', '.oceanlab-installation.txt')
Set-Content -LiteralPath (Join-Path $Payload '.oceanlab-managed-files.txt') -Value ($Files | Sort-Object) -Encoding UTF8
Set-Content -LiteralPath (Join-Path $Payload '.oceanlab-installation.txt') -Value 'OceanLab.Win7.Managed.v1' -Encoding ascii
# Inno 6.2 requires UTF-8 BOM for non-ASCII script text.
$Utf8Bom = New-Object System.Text.UTF8Encoding($true)
$Script = Join-Path $Work 'oceanlab_win7.iss'
[IO.File]::WriteAllText($Script, [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'packaging\oceanlab_win7.iss')), $Utf8Bom)
& $Compiler '/Q' "/DAppVersion=$Version" "/DPayloadDir=$Payload" "/DReleaseDir=$ReleaseRoot" "/DProjectDir=$PSScriptRoot" $Script
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
$Installer = Join-Path $ReleaseRoot "OceanLab-$Version-win7-x64-setup.exe"
$Hash = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $ReleaseRoot "OceanLab-$Version-win7-x64-setup.sha256.txt") -Value "$Hash  $([IO.Path]::GetFileName($Installer))" -Encoding ascii
Get-Item -LiteralPath $Installer
