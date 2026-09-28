function Get-ScanSciSigningConfiguration {
    param(
        [switch]$Required
    )

    if (-not $Required) {
        return $null
    }

    # Release credentials stay outside source control. The release account
    # selects a certificate from its CurrentUser\My store by thumbprint.
    $thumbprint = ([string]$env:SCANSCI_SIGNING_CERT_THUMBPRINT).Replace(" ", "").Trim()
    $timestampUrl = ([string]$env:SCANSCI_TIMESTAMP_URL).Trim()
    if (-not $thumbprint) {
        throw "Formal signing requires SCANSCI_SIGNING_CERT_THUMBPRINT to identify a CurrentUser\My code-signing certificate."
    }
    if ($thumbprint -notmatch '^[A-Fa-f0-9]{40}$') {
        throw "SCANSCI_SIGNING_CERT_THUMBPRINT must be a 40-character SHA-1 certificate thumbprint."
    }
    if (-not $timestampUrl) {
        throw "Formal signing requires SCANSCI_TIMESTAMP_URL (an HTTPS RFC 3161 timestamp service)."
    }
    try {
        $timestampUri = [uri]$timestampUrl
    } catch {
        throw "SCANSCI_TIMESTAMP_URL is not a valid absolute HTTPS URL."
    }
    if (-not $timestampUri.IsAbsoluteUri -or $timestampUri.Scheme -ne "https") {
        throw "SCANSCI_TIMESTAMP_URL must be an absolute HTTPS URL."
    }

    $certificate = Get-ChildItem -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue
    if (-not $certificate) {
        throw "The signing certificate $thumbprint was not found in Cert:\CurrentUser\My."
    }
    if (-not $certificate.HasPrivateKey) {
        throw "The signing certificate $thumbprint does not expose a private key to this release account."
    }
    if ($certificate.NotAfter -le (Get-Date)) {
        throw "The signing certificate $thumbprint is expired."
    }

    $kitRoots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }
    $kitCandidates = foreach ($kitRoot in $kitRoots) {
        Get-ChildItem -LiteralPath $kitRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" }
    }
    $signToolCandidates = @(
        (Get-Command signtool.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        $kitCandidates
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
    if (-not $signToolCandidates) {
        throw "Formal signing requires SignTool.exe from the Windows SDK or Visual Studio Developer PowerShell."
    }

    return [pscustomobject]@{
        Thumbprint = $thumbprint.ToUpperInvariant()
        TimestampUrl = $timestampUri.AbsoluteUri
        SignTool = ($signToolCandidates | Select-Object -First 1)
    }
}

function Assert-ScanSciArtifactSignature {
    param(
        [Parameter(Mandatory)]
        [string]$Artifact,
        [string]$ExpectedThumbprint = ""
    )

    $signature = Get-AuthenticodeSignature -LiteralPath $Artifact
    if ($signature.Status -ne "Valid") {
        throw "Authenticode verification failed for $Artifact; status: $($signature.Status)"
    }
    $actualThumbprint = ([string]$signature.SignerCertificate.Thumbprint).Replace(" ", "").ToUpperInvariant()
    if ($ExpectedThumbprint -and $actualThumbprint -ne $ExpectedThumbprint.ToUpperInvariant()) {
        throw "Authenticode signer mismatch for $Artifact."
    }
    return $signature
}

function Invoke-ScanSciArtifactSigning {
    param(
        [Parameter(Mandatory)]
        [string]$Artifact,
        [Parameter(Mandatory)]
        [object]$Signing
    )

    & $Signing.SignTool sign /fd SHA256 /sha $Signing.Thumbprint /tr $Signing.TimestampUrl /td SHA256 $Artifact |
        ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool failed with exit code $LASTEXITCODE for $Artifact"
    }
    return Assert-ScanSciArtifactSignature -Artifact $Artifact -ExpectedThumbprint $Signing.Thumbprint
}
