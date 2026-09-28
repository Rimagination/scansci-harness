[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BuildDir,
    [Parameter(Mandatory)]
    [string]$Version,
    [Parameter(Mandatory)]
    [string]$PackageUrl,
    [string]$OutputDir = "",
    [string]$Channel = "stable",
    [string]$LocalRuntimeManifest = "",
    [string]$BlockmapUrl = "",
    [string]$ManifestPrivateKey = "",
    [string]$ManifestKeyId = "scansci-release-v1"
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$source = [System.IO.Path]::GetFullPath($BuildDir)
if (-not (Test-Path -LiteralPath (Join-Path $source "ScanSci.exe") -PathType Leaf)) {
    throw "ScanSci desktop build was not found: $source"
}
if (-not $OutputDir) {
    $OutputDir = Join-Path $projectRoot "release"
}
$releaseRoot = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$archive = Join-Path $releaseRoot "ScanSci-$Version-windows-x64.zip"
$manifest = Join-Path $releaseRoot "stable.json"
Compress-Archive -Path (Join-Path $source "*") -DestinationPath $archive -CompressionLevel Optimal -Force
function Get-Sha256Hex([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($hasher.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}
$sha256 = Get-Sha256Hex $archive
$blockSize = 64 * 1024
$blockmapPath = $archive + ".blockmap"
$blockHashes = [System.Collections.Generic.List[string]]::new()
$blockStream = [System.IO.File]::OpenRead($archive)
$blockHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $buffer = New-Object byte[] $blockSize
    while (($read = $blockStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $chunk = New-Object byte[] $read
        [System.Array]::Copy($buffer, $chunk, $read)
        $blockHash = $blockHasher.ComputeHash($chunk)
        $blockHashes.Add(([System.BitConverter]::ToString($blockHash).Replace("-", "").ToLowerInvariant()))
    }
}
finally {
    $blockHasher.Dispose()
    $blockStream.Dispose()
}
$blockmapPayload = [ordered]@{
    schema_version = 1
    algorithm = "sha256"
    block_size = $blockSize
    size = [long](Get-Item -LiteralPath $archive).Length
    sha256 = $sha256
    blocks = @($blockHashes.ToArray())
}
$blockmapPayload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $blockmapPath -Encoding utf8
$blockmapSha256 = Get-Sha256Hex $blockmapPath
$blockmapSize = [long](Get-Item -LiteralPath $blockmapPath).Length
$resolvedBlockmapUrl = [string]$BlockmapUrl
if (-not $resolvedBlockmapUrl) {
    $resolvedBlockmapUrl = $PackageUrl + ".blockmap"
}
$payload = [ordered]@{
    schema_version = 1
    version = $Version
    title = "ScanSci $Version"
    channel = $Channel
    published_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    notes = @(
        [ordered]@{ title = "Desktop"; items = @("Version status, release notes, and verified installation are available.") }
    )
    windows = [ordered]@{
        url = $PackageUrl
        sha256 = $sha256
        size = [long](Get-Item -LiteralPath $archive).Length
        archive = [System.IO.Path]::GetFileName($archive)
        blockmap = [ordered]@{
            url = $resolvedBlockmapUrl
            sha256 = $blockmapSha256
            size = $blockmapSize
            block_size = $blockSize
        }
    }
}
if ($LocalRuntimeManifest) {
    $componentPath = [System.IO.Path]::GetFullPath($LocalRuntimeManifest)
    if (-not (Test-Path -LiteralPath $componentPath -PathType Leaf)) {
        throw "Local runtime manifest was not found: $componentPath"
    }
    $component = Get-Content -Raw -LiteralPath $componentPath | ConvertFrom-Json
    $componentParts = @($component.windows.parts)
    $hasPackageSource = [bool]$component.windows.url -or $componentParts.Count -gt 0
    if ($component.id -ne "local-transformers" -or -not $component.version -or -not $hasPackageSource -or -not $component.windows.sha256) {
        throw "Local runtime manifest is incomplete."
    }
    $componentWindows = [ordered]@{
        sha256 = [string]$component.windows.sha256
        size = [long]$component.windows.size
    }
    if ($component.windows.url) {
        $componentWindows.url = [string]$component.windows.url
    }
    if ($componentParts.Count -gt 0) {
        $componentWindows.parts = @($componentParts | ForEach-Object {
            if (-not $_.url -or -not $_.sha256 -or -not $_.size) {
                throw "Local runtime multipart manifest is incomplete."
            }
            [ordered]@{
                url = [string]$_.url
                sha256 = [string]$_.sha256
                size = [long]$_.size
            }
        })
    }
    if ($component.windows.diagnostics) {
        $componentWindows.diagnostics = $component.windows.diagnostics
    }
    $payload.components = [ordered]@{
        "local-transformers" = [ordered]@{
            version = [string]$component.version
            windows = $componentWindows
        }
    }
}
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifest -Encoding utf8
if ($ManifestPrivateKey) {
    $privateKeyPath = [System.IO.Path]::GetFullPath($ManifestPrivateKey)
    if (-not (Test-Path -LiteralPath $privateKeyPath -PathType Leaf)) {
        throw "Manifest private key was not found: $privateKeyPath"
    }
    & python (Join-Path $PSScriptRoot "sign_release_manifest.py") $manifest `
        --private-key $privateKeyPath `
        --key-id $ManifestKeyId
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to sign the release manifest."
    }
}
Write-Output $archive
Write-Output $blockmapPath
Write-Output $manifest
