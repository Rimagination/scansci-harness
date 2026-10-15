[CmdletBinding()]
param(
    [string]$BuildId = "electron-preview",
    [string]$ReleasePublicKey = "",
    [switch]$Clean
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ReleasePublicKey) {
    throw "Electron packages require the official Ed25519 ReleasePublicKey. Use npm run desktop:electron for unsigned source previews."
}
$backendOutput = Join-Path $projectRoot "dist-electron-backend"
$backendParams = @{
    ElectronBackend = $true
    PackageProfile = "core"
    ExcludeRuntimes = $true
    OutputDir = $backendOutput
    BuildId = $BuildId
    ReleasePublicKey = $ReleasePublicKey
    Clean = [bool]$Clean
}
& (Join-Path $PSScriptRoot "build_desktop.ps1") @backendParams
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$backendExe = Join-Path $backendOutput "ScanSciBackend\ScanSciBackend.exe"
if (-not (Test-Path -LiteralPath $backendExe -PathType Leaf)) {
    throw "Electron Python backend was not built: $backendExe"
}

& npm --prefix $projectRoot run package:electron
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$releaseRoot = Join-Path $projectRoot "dist-electron\win-unpacked"
$desktopExe = Join-Path $releaseRoot "ScanSci.exe"
$packagedBackendExe = Join-Path $releaseRoot "resources\backend\ScanSciBackend.exe"
if (-not (Test-Path -LiteralPath $desktopExe -PathType Leaf) -or -not (Test-Path -LiteralPath $packagedBackendExe -PathType Leaf)) {
    throw "Electron package is missing ScanSci.exe or its Python backend."
}
Write-Output $desktopExe
Write-Output $packagedBackendExe
