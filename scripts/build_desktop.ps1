[CmdletBinding()]
param(
    [ValidateSet("onedir", "onefile")]
    [string]$Mode = "onedir",
    [ValidateSet("core", "full")]
    # The public desktop is deliberately light.  Local inference is installed
    # later as a verified, versioned component or configured by the user.
    [string]$PackageProfile = "core",
    [string]$OutputDir = "",
    [string]$Name = "ScanSci",
    [string]$Version = "0.4.2",
    [string]$BuildId = "",
    # Populated only by scripts/release_gate.py. This binds an auditable
    # release candidate to the exact source fingerprint that passed its gate.
    [string]$ReleaseSourceSha256 = "",
    [string]$CacheKey = "",
    [string]$UpdateManifestUrl = "https://github.com/Rimagination/scansci-pi/releases/latest/download/stable.json",
    # Keep direct/manual builds aligned with the public release contract. An
    # empty value here makes a core package advertise model downloads without
    # providing the runtime that executes them.
    [string]$RuntimeManifestUrl = "https://github.com/Rimagination/scansci-portal/releases/download/local-runtime-v1.0.0/local-transformers.json",
    [string]$NodeComponentManifestUrl = "https://github.com/Rimagination/scansci-portal/releases/download/runtime-components-v1/node.json",
    [string]$TectonicComponentManifestUrl = "https://github.com/Rimagination/scansci-portal/releases/download/runtime-components-v1/tectonic.json",
    # Base64-encoded raw 32-byte Ed25519 public key. The matching private key
    # remains offline and is used only while packaging release manifests.
    [string]$ReleasePublicKey = "",
    # Slim channel: do not embed node.exe or tectonic.exe in the bundle.
    # The Pi sidecar and slide LaTeX engine then resolve them through the
    # managed runtime components (see runtime_components.py) after one
    # user-confirmed install.
    [switch]$ExcludeRuntimes,
    [switch]$Clean
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputDir) {
    $OutputDir = Join-Path $projectRoot "dist"
}
$runtimeUri = [Uri]$RuntimeManifestUrl
if (-not $runtimeUri.IsAbsoluteUri -or $runtimeUri.Scheme -ne "https") {
    throw "RuntimeManifestUrl must be an absolute HTTPS URL."
}
$updateUri = [Uri]$UpdateManifestUrl
if (-not $updateUri.IsAbsoluteUri -or $updateUri.Scheme -ne "https") {
    throw "UpdateManifestUrl must be an absolute HTTPS URL."
}
foreach ($componentManifest in @(
    @{ Name = "NodeComponentManifestUrl"; Value = $NodeComponentManifestUrl },
    @{ Name = "TectonicComponentManifestUrl"; Value = $TectonicComponentManifestUrl }
)) {
    $componentUri = [Uri]$componentManifest.Value
    if (-not $componentUri.IsAbsoluteUri -or $componentUri.Scheme -ne "https") {
        throw "$($componentManifest.Name) must be an absolute HTTPS URL."
    }
}
if ($ReleasePublicKey) {
    try {
        $releasePublicKeyBytes = [Convert]::FromBase64String($ReleasePublicKey)
    } catch {
        throw "ReleasePublicKey must be a Base64-encoded Ed25519 public key."
    }
    if ($releasePublicKeyBytes.Length -ne 32) {
        throw "ReleasePublicKey must decode to exactly 32 bytes."
    }
}
$entryPoint = Join-Path $projectRoot "scripts\scansci_desktop_entry.py"
$pyInstallerHooks = Join-Path $projectRoot "scripts\pyinstaller_hooks"
$assetSource = Join-Path $projectRoot "src\scansci_html\web"
$skillAssetSource = Join-Path $projectRoot "src\scansci_html\builtin_skill_assets"
$slideTemplateSource = Join-Path $projectRoot "src\scansci_html\builtin_slide_templates"
$packagedTectonicSource = Join-Path $projectRoot "src\scansci_html\runtime\latex\tectonic.exe"
$codexTectonicSource = Get-ChildItem -Path (Join-Path $HOME ".codex\plugins\cache\openai-bundled\latex\*\bin\tectonic.exe") -File -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
$tectonicSource = if (Test-Path -LiteralPath $packagedTectonicSource -PathType Leaf) { $packagedTectonicSource } elseif ($codexTectonicSource) { $codexTectonicSource } else { "" }
$iconPath = Join-Path $assetSource "scansci.ico"
$piBundle = Join-Path $projectRoot "pi-runtime\dist\main.mjs"
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    throw "Node.js 22 or newer is required to build the ScanSci Harness Pi runtime."
}
$nodeExe = $nodeCommand.Source

if ($PackageProfile -eq "full") {
    & python -c "import torch, transformers, sentence_transformers, sentencepiece"
    if ($LASTEXITCODE -ne 0) {
        throw 'The full desktop profile requires local inference dependencies. Install them with: python -m pip install -e ".[desktop,local-gpu,rerank]"'
    }
}

& npm --prefix $projectRoot run build:pi-runtime
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if (-not (Test-Path -LiteralPath $piBundle -PathType Leaf)) {
    throw "ScanSci Harness Pi sidecar bundle was not generated: $piBundle"
}
$resolvedBuildId = if ($BuildId) { $BuildId } else { "$(Get-Date -Format 'yyyyMMdd-HHmmss')" }
if ($resolvedBuildId -notmatch '^[A-Za-z0-9._-]+$') {
    throw "BuildId may contain only letters, numbers, dots, underscores, and hyphens."
}
if ($ReleaseSourceSha256 -and $ReleaseSourceSha256 -notmatch '^[A-Fa-f0-9]{64}$') {
    throw "ReleaseSourceSha256 must be an SHA-256 hex digest when supplied."
}
$pythonVersion = (& python -c "import platform; print(platform.python_version())").Trim()
$pyInstallerVersion = (& python -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
$sourceRoot = Join-Path $projectRoot "src\scansci_html"
$sourceTreeMaterial = Get-ChildItem -LiteralPath $sourceRoot -Recurse -File |
    Where-Object { $_.FullName -notmatch "(__pycache__|\.pyc$)" } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName.Substring($projectRoot.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
        "$relative|$((Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash)"
    }
$sourceTreeHash = if ($sourceTreeMaterial) {
    $sourceTreeBytes = [Text.Encoding]::UTF8.GetBytes(($sourceTreeMaterial -join "`n"))
    ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($sourceTreeBytes)) -replace '-', '').ToLowerInvariant()
} else {
    "missing"
}
$dependencyFiles = @(
    (Join-Path $projectRoot "pyproject.toml"),
    (Join-Path $projectRoot "requirements.txt"),
    (Join-Path $projectRoot "package-lock.json"),
    (Join-Path $projectRoot "pi-runtime\package.json"),
    (Join-Path $projectRoot "pi-runtime\src\main.ts"),
    $piBundle,
    $entryPoint,
    (Join-Path $projectRoot "scripts\freeze_pyinstaller_spec.py"),
    (Join-Path $pyInstallerHooks "hook-torch.py"),
    (Join-Path $pyInstallerHooks "hook-litellm.py"),
    $PSCommandPath
)
$dependencyHashes = $dependencyFiles | ForEach-Object {
    if (Test-Path -LiteralPath $_ -PathType Leaf) { (Get-FileHash -Algorithm SHA256 -LiteralPath $_).Hash } else { "missing" }
}
$cacheMaterial = "$PackageProfile|$Mode|$Name|$ExcludeRuntimes|$UpdateManifestUrl|$RuntimeManifestUrl|$NodeComponentManifestUrl|$TectonicComponentManifestUrl|$ReleasePublicKey|$pythonVersion|$pyInstallerVersion|$sourceTreeHash|$($dependencyHashes -join '|')"
if (-not $CacheKey) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($cacheMaterial)
    $digest = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    $CacheKey = ([BitConverter]::ToString($digest) -replace '-', '').Substring(0, 16).ToLowerInvariant()
}
if ($CacheKey -notmatch '^[A-Za-z0-9._-]+$') {
    throw "CacheKey may contain only letters, numbers, dots, underscores, and hyphens."
}
$cacheName = "$PackageProfile-$CacheKey"
$buildPath = Join-Path $projectRoot "build\desktop-cache\$cacheName"
$specPath = Join-Path $projectRoot "build\desktop-spec\$cacheName"
$metadataPath = Join-Path $projectRoot "build\release-metadata\$resolvedBuildId"
$stableMetadataPath = Join-Path $projectRoot "build\desktop-metadata\$cacheName"
$buildInfoPath = Join-Path $stableMetadataPath "build-info.json"
$auditBuildInfoPath = Join-Path $metadataPath "build-info.json"

New-Item -ItemType Directory -Force -Path $metadataPath, $stableMetadataPath, $buildPath, $specPath | Out-Null
$commit = ""
try {
    $commit = (& git -C $projectRoot rev-parse --short HEAD 2>$null).Trim()
} catch {
    $commit = ""
}
$buildInfo = [ordered]@{
    version = $Version
    build_id = $resolvedBuildId
    built_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    commit = $commit
    package_profile = $PackageProfile
    exclude_runtimes = [bool]$ExcludeRuntimes
    update_manifest_url = $UpdateManifestUrl
    runtime_manifest_url = $RuntimeManifestUrl
    node_component_manifest_url = $NodeComponentManifestUrl
    tectonic_component_manifest_url = $TectonicComponentManifestUrl
    release_public_key = $ReleasePublicKey
    cache_key = $CacheKey
    source_tree_sha256 = $sourceTreeHash
    release_source_sha256 = $ReleaseSourceSha256.ToLowerInvariant()
    pi_bundle_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $piBundle).Hash.ToLowerInvariant()
}
$buildInfo | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $auditBuildInfoPath -Encoding utf8

if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
    throw "ScanSci Windows icon was not found: $iconPath. Run scripts\build_windows_icon.py first."
}

$pyInstallerArgs = @(
    "--noconfirm",
    "--windowed",
    "--name", $Name,
    "--icon", $iconPath,
    "--$Mode",
    "--paths", (Join-Path $projectRoot "src"),
    "--additional-hooks-dir", $pyInstallerHooks,
    "--add-data", "$assetSource;scansci_html\web",
    "--add-data", "$skillAssetSource;scansci_html\builtin_skill_assets",
    "--add-data", "$slideTemplateSource;scansci_html\builtin_slide_templates",
    "--add-data", "$piBundle;pi_runtime",
    $(if (-not $ExcludeRuntimes) { @("--add-binary", "$nodeExe;pi_runtime") } else { @() }),
    # python-pptx loads its default blank deck from package data at runtime;
    # include it explicitly so a packaged ScanSci can create PPTX files.
    "--collect-data", "pptx",
    "--collect-data", "docx",
    "--collect-data", "openpyxl",
    "--collect-data", "reportlab",
    # LiteLLM includes guardrail benchmark fixtures whose deeply nested file
    # names exceed Windows path limits in a clean installer build.  The local
    # hook collects LiteLLM's runtime data while explicitly excluding those
    # non-runtime benchmarks.
    "--collect-data", "markitdown",
    "--collect-data", "tiktoken",
    "--collect-submodules", "markitdown",
    "--collect-submodules", "litellm.llms.openai",
    "--collect-submodules", "litellm.llms.anthropic",
    "--collect-submodules", "tiktoken_ext",
    "--collect-all", "sqlite_vec",
    # Model discovery and downloads stay in the desktop core.  Heavy inference
    # libraries are collected only by the legacy/full profile or the separate
    # local-runtime component.
    "--collect-all", "huggingface_hub",
    "--hidden-import", "litellm",
    "--hidden-import", "tiktoken_ext.openai_public",
    "--hidden-import", "opentelemetry.sdk",
    "--hidden-import", "opentelemetry.sdk.trace",
    "--hidden-import", "pyzotero.zotero",
    "--hidden-import", "openpyxl",
    "--hidden-import", "reportlab.pdfgen.canvas",
    "--distpath", $OutputDir,
    "--workpath", $buildPath,
    "--specpath", $specPath,
    $entryPoint
)
if ($ExcludeRuntimes) {
    # Slide LaTeX resolves tectonic.exe through the managed runtime component.
    Write-Warning "Tectonic is not embedded (slim channel); slide LaTeX uses the managed Tectonic component."
} elseif ($tectonicSource) {
    $pyInstallerArgs = @("--add-binary", "$tectonicSource;scansci_html\runtime\latex") + $pyInstallerArgs
} else {
    Write-Warning "Tectonic was not found. The packaged LaTeX plugin will require an existing TeX Live runtime."
}
if ($Mode -eq "onefile") {
    # onefile has no writable external _internal directory; keep the build
    # identity embedded. Componentized release builds use onedir.
    $pyInstallerArgs = @("--add-data", "$buildInfoPath;scansci_html") + $pyInstallerArgs
}
if ($PackageProfile -eq "full") {
    # Do not use --collect-all for the local inference stack.  In particular,
    # `--collect-all torch` walks PyTorch's internal test tree on Windows and
    # can leave a clean release build stuck indefinitely in
    # torch.testing._internal.  The desktop only needs the Qwen runtime, so we
    # collect native libraries/data and declare the dynamic Qwen entry points
    # explicitly.  This keeps the complete retrieval/chat capability without
    # bundling test, benchmark, audio, or vision packages.
    $pyInstallerArgs += @(
        "--collect-binaries", "torch",
        "--collect-data", "torch",
        "--collect-binaries", "bitsandbytes",
        "--collect-data", "bitsandbytes",
        "--collect-data", "transformers",
        "--collect-data", "safetensors",
        "--collect-data", "sentence_transformers",
        "--collect-all", "torchvision",
        "--collect-binaries", "sentencepiece",
        "--hidden-import", "torch",
        "--hidden-import", "torch._C",
        "--hidden-import", "torch.cuda",
        "--hidden-import", "torch.nn",
        "--hidden-import", "torch.nn.functional",
        "--collect-submodules", "torch._dynamo.polyfills",
        "--hidden-import", "scansci_html.local_transformers_compat",
        # Required by torch.utils.checkpoint in torch 2.13.  Declaring the
        # precise helper avoids recursively collecting PyTorch's test suite.
        "--hidden-import", "torch.testing._internal.logging_tensor",
        "--hidden-import", "torch.testing._internal.common_dtype",
        "--hidden-import", "bitsandbytes",
        "--hidden-import", "safetensors",
        "--hidden-import", "sentence_transformers.sentence_transformer.model",
        "--hidden-import", "sentence_transformers.cross_encoder.model",
        # sentence-transformers 5 keeps compatibility aliases under
        # ``sentence_transformers.models``.  PyInstaller cannot resolve those
        # aliases reliably; collect the real implementation modules instead.
        "--hidden-import", "sentence_transformers.base.modules.transformer",
        "--hidden-import", "sentence_transformers.sentence_transformer.modules.pooling",
        "--hidden-import", "sentence_transformers.sentence_transformer.modules.normalize",
        "--hidden-import", "sentencepiece",
        "--hidden-import", "transformers.models.auto.modeling_auto",
        "--hidden-import", "transformers.models.auto.tokenization_auto",
        "--hidden-import", "transformers.utils.quantization_config",
        "--hidden-import", "transformers.generation.stopping_criteria",
        "--hidden-import", "transformers.generation.streamers",
        "--hidden-import", "transformers.pipelines",
        "--hidden-import", "transformers.generation.utils",
        "--hidden-import", "transformers.modeling_utils",
        "--hidden-import", "transformers.integrations.bitsandbytes",
        "--hidden-import", "transformers.models.qwen3",
        "--hidden-import", "transformers.models.qwen3.configuration_qwen3",
        "--hidden-import", "transformers.models.qwen3.modeling_qwen3",
        # Qwen3-ASR is loaded lazily by scansci_html.local_asr when the user
        # sends an audio attachment.  PyInstaller cannot infer this dynamic
        # AutoModelForMultimodalLM architecture from the string-based model
        # registry, so keep the complete ASR family in the full profile.
        "--hidden-import", "transformers.models.qwen3_asr",
        "--hidden-import", "transformers.models.qwen3_asr.configuration_qwen3_asr",
        "--hidden-import", "transformers.models.qwen3_asr.modeling_qwen3_asr",
        "--hidden-import", "transformers.models.qwen3_asr.processing_qwen3_asr",
        "--hidden-import", "transformers.models.hrm_text",
        "--hidden-import", "transformers.models.hrm_text.configuration_hrm_text",
        "--hidden-import", "transformers.models.hrm_text.modeling_hrm_text",
        "--hidden-import", "transformers.models.minicpmv4_6",
        "--hidden-import", "transformers.models.minicpmv4_6.configuration_minicpmv4_6",
        "--hidden-import", "transformers.models.minicpmv4_6.modeling_minicpmv4_6",
        "--hidden-import", "transformers.models.minicpmv4_6.processing_minicpmv4_6",
        "--hidden-import", "scansci_html.local_asr",
        "--hidden-import", "transformers.models.qwen2.tokenization_qwen2_fast",
        "--hidden-import", "scansci_html.local_runtime_server"
    )
}
if ($Clean) {
    # Release gates use a clean build. Direct development builds intentionally
    # keep PyInstaller's per-build-id cache so a retry does not repeat analysis.
    $pyInstallerArgs = @("--clean") + $pyInstallerArgs
}

# Training, benchmarking, and unrelated media stacks are not required by the
# desktop.  The full profile retains the complete Qwen retrieval runtime;
# the core profile excludes it only for explicitly componentized channels.
$excludedModules = @(
    # PyInstaller injects __main__ into Analysis.excludes. Supplying it up
    # front keeps the persisted TOC identical on the next invocation instead
    # of forcing a complete re-analysis with "excludes changed".
    "__main__",
    "boto3",
    "botocore",
    "cv2",
    "IPython",
    "jupyter",
    "keras",
    "matplotlib",
    "pandas",
    "pygame",
    "pytest",
    "tensorflow",
    "torchaudio",
    # Never collect the benchmark tree into an end-user runtime. PyTorch's
    # single runtime dependency under torch.testing._internal is declared
    # explicitly above; the custom hook still excludes the rest as data.
    "torch.utils.benchmark",
    # Scientific stack pulled in only through optional import chains
    # (litellm's nvidia_riva audio module, pydub's effects, pymupdf layout).
    # ScanSci core never imports scipy; keep the ~50 MB out of the bundle.
    "scipy",
    "sklearn",
    # markitdown[all]'s audio transcription stack (pocketsphinx acoustic
    # data + flac binaries for three platforms).  Voice input goes through
    # ScanSci's own local ASR, never through markitdown.
    "speech_recognition",
    "pydub"
)
if ($PackageProfile -eq "core") {
    $excludedModules += @(
        "accelerate",
        "encodec",
        "librosa",
        "llvmlite",
        "numba",
        "openai_whisper",
        # fsspec exposes an optional Arrow filesystem integration. When
        # pyarrow happens to be installed on the build machine, PyInstaller's
        # hook otherwise collects the entire Arrow SDK (including development
        # headers) even though ScanSci core never imports or uses it.
        "pyarrow",
        "safetensors",
        "sentence_transformers",
        "sentencepiece",
        "sudachidict_core",
        "sudachipy",
        "torch",
        "transformers",
        "TTS",
        "whisper"
    )
}
foreach ($module in $excludedModules) {
    $pyInstallerArgs += "--exclude-module"
    $pyInstallerArgs += $module
}

$lockPath = Join-Path (Split-Path -Parent $buildPath) "$cacheName.lock"
$specFile = Join-Path $specPath "$Name.spec"
$lockStream = $null
$previousLiteLlmLocalCostMap = $env:LITELLM_LOCAL_MODEL_COST_MAP
try {
    try {
        $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw "Another ScanSci $PackageProfile build is using cache $CacheKey. Wait for it to finish instead of creating a competing cold build."
    }
    # The stable metadata path is shared by every build using this dependency
    # cache.  Update it only after taking the cache lock so concurrent release
    # jobs cannot embed each other's build identity.
    Copy-Item -LiteralPath $auditBuildInfoPath -Destination $buildInfoPath -Force
    # PyInstaller imports LiteLLM while resolving hooks. Use LiteLLM's
    # bundled cost map so packaging never waits for its optional GitHub map.
    $env:LITELLM_LOCAL_MODEL_COST_MAP = "true"
    Write-Host "ScanSci package profile: $PackageProfile"
    Write-Host "Reusable PyInstaller cache: $buildPath"
    if (Test-Path -LiteralPath $specFile -PathType Leaf) {
        Write-Host "Reusing stable PyInstaller spec: $specFile"
        $runArgs = @(
            "--noconfirm",
            "--distpath", $OutputDir,
            "--workpath", $buildPath
        )
        if ($Clean) { $runArgs = @("--clean") + $runArgs }
        $runArgs += $specFile
        & python -m PyInstaller @runArgs
    } else {
        & python -m PyInstaller @pyInstallerArgs
    }
    $buildExitCode = $LASTEXITCODE
    if ($buildExitCode -eq 0 -and $Mode -eq "onedir") {
        $analysisTocPath = Join-Path $buildPath "$Name\Analysis-00.toc"
        if ((Test-Path -LiteralPath $specFile -PathType Leaf) -and (Test-Path -LiteralPath $analysisTocPath -PathType Leaf)) {
            & python (Join-Path $projectRoot "scripts\freeze_pyinstaller_spec.py") --spec $specFile --analysis-toc $analysisTocPath
            if ($LASTEXITCODE -ne 0) { throw "Failed to freeze PyInstaller collection inputs." }
        }
    }
} finally {
    if ($null -eq $previousLiteLlmLocalCostMap) {
        Remove-Item Env:LITELLM_LOCAL_MODEL_COST_MAP -ErrorAction SilentlyContinue
    } else {
        $env:LITELLM_LOCAL_MODEL_COST_MAP = $previousLiteLlmLocalCostMap
    }
    if ($null -ne $lockStream) { $lockStream.Dispose() }
}
if ($buildExitCode -ne 0) {
    exit $buildExitCode
}
if ($Mode -eq "onedir") {
    $packagedMetadataPath = Join-Path $OutputDir "$Name\_internal\scansci_html"
    if (-not (Test-Path -LiteralPath $packagedMetadataPath -PathType Container)) {
        throw "Packaged ScanSci metadata directory is missing: $packagedMetadataPath"
    }
    Copy-Item -LiteralPath $buildInfoPath -Destination (Join-Path $packagedMetadataPath "build-info.json") -Force
}
