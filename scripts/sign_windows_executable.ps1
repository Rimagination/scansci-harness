[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Artifact
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "windows_signing.ps1")

$resolvedArtifact = (Resolve-Path -LiteralPath $Artifact).Path
if (-not (Test-Path -LiteralPath $resolvedArtifact -PathType Leaf)) {
    throw "Packaged ScanSci executable was not found: $resolvedArtifact"
}

$signing = Get-ScanSciSigningConfiguration -Required
$signature = Invoke-ScanSciArtifactSigning -Artifact $resolvedArtifact -Signing $signing
Write-Output "$resolvedArtifact $($signature.Status)"
