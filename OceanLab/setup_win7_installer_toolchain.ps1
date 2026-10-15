$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$BuildRoot = Join-Path (Split-Path -Parent $PSScriptRoot) '.build-win7'
$CompilerRoot = Join-Path $BuildRoot 'inno-setup-6.2.2'
$Download = Join-Path $BuildRoot 'downloads\innosetup-6.2.2.exe'
$ExpectedHash = '8117D10D00A2AD33A1390978EA3872861C330E087914410A6377B22C4C5B8563'
if (-not (Test-Path -LiteralPath $Download)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Download) | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/jrsoftware/issrc/releases/download/is-6_2_2/innosetup-6.2.2.exe' -OutFile $Download
}
if ((Get-FileHash -LiteralPath $Download -Algorithm SHA256).Hash -ne $ExpectedHash) {
    throw 'Inno Setup 6.2.2 download checksum does not match.'
}
$Signature = Get-AuthenticodeSignature -LiteralPath $Download
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Martijn Laan') {
    throw 'Inno Setup download signature is invalid.'
}
if (-not (Test-Path -LiteralPath (Join-Path $CompilerRoot 'ISCC.exe'))) {
    $Arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER', '/PORTABLE=1', '/NOICONS', '/TASKS=', ('/DIR="' + $CompilerRoot + '"'))
    $Process = Start-Process -FilePath $Download -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru
    if ($Process.ExitCode -ne 0) { throw "Compiler extraction failed: $($Process.ExitCode)" }
}
Write-Host "Installer compiler ready: $CompilerRoot"
