[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BuildDir,
    [Parameter(Mandatory)]
    [string]$Version,
    [Parameter(Mandatory)]
    [string]$BuildId,
    [Parameter(Mandatory)]
    [string]$OutputDir,
    [string]$UpdateManifestUrl = "",
    [switch]$RequireSignature
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$source = (Resolve-Path -LiteralPath $BuildDir).Path
$output = [System.IO.Path]::GetFullPath($OutputDir)
$scriptPath = Join-Path $projectRoot "installer\ScanSci.iss"
$exe = Join-Path $source "ScanSci.exe"
. (Join-Path $PSScriptRoot "windows_signing.ps1")

if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Packaged ScanSci executable was not found: $exe"
}
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
    throw "Inno Setup script was not found: $scriptPath"
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+([-.+][0-9A-Za-z.-]+)?$') {
    throw "Version must be SemVer-compatible: $Version"
}
if ($BuildId -notmatch '^[0-9A-Za-z._+-]+$') {
    throw "BuildId may only contain letters, numbers, dots, underscores, plus signs, and hyphens."
}
if ($UpdateManifestUrl) {
    try {
        $updateUri = [uri]$UpdateManifestUrl
    } catch {
        throw "UpdateManifestUrl must be an absolute HTTPS URL."
    }
    if (-not $updateUri.IsAbsoluteUri -or $updateUri.Scheme -ne "https") {
        throw "UpdateManifestUrl must be an absolute HTTPS URL."
    }
}

$signing = Get-ScanSciSigningConfiguration -Required:$RequireSignature
$sourceSignature = Get-AuthenticodeSignature -LiteralPath $exe
if ($signing) {
    # The release gate signs this file before package hashing and ZIP creation.
    # Installer construction verifies the exact signer and never mutates it.
    $sourceSignature = Assert-ScanSciArtifactSignature -Artifact $exe -ExpectedThumbprint $signing.Thumbprint
}

$isccCandidates = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
if (-not $isccCandidates) {
    throw "Inno Setup 6 (ISCC.exe) is required to build the Windows installer."
}

New-Item -ItemType Directory -Force -Path $output | Out-Null
$iscc = $isccCandidates | Select-Object -First 1
$arguments = @(
    "/DSourceDir=$source",
    "/DOutputDir=$output",
    "/DAppVersion=$Version",
    "/DBuildId=$BuildId",
    "/DUpdateManifestUrl=$UpdateManifestUrl",
    $scriptPath
)
& $iscc @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$installer = Join-Path $output "ScanSci-$Version-windows-x64-setup.exe"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Inno Setup completed without the expected installer: $installer"
}

$installerSignature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signing) {
    $installerSignature = Invoke-ScanSciArtifactSigning -Artifact $installer -Signing $signing
}
if ($RequireSignature -and ($sourceSignature.Status -ne "Valid" -or $installerSignature.Status -ne "Valid")) {
    throw "Formal release requires valid Authenticode signatures for both ScanSci.exe and the installer."
}

$manifest = [ordered]@{
    schema_version = 1
    product = "ScanSci"
    version = $Version
    build_id = $BuildId
    generated_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    source_directory = $source
    source_executable_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    source_executable_authenticode_status = [string]$sourceSignature.Status
    installer_path = $installer
    installer_sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    installer_bytes = (Get-Item -LiteralPath $installer).Length
    authenticode_status = [string]$installerSignature.Status
    signature_required = [bool]$RequireSignature
}
$manifestPath = Join-Path $output "installer-manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Output $installer
Write-Output $manifestPath
