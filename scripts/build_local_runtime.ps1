[CmdletBinding()]
param(
    [string]$OutputDir = "",
    [string]$Version = "1.0.4",
    [string]$CacheKey = "",
    [string]$PackageUrl = "",
    [string]$PythonExecutable = "python",
    [int]$PartSizeMb = 512,
    [switch]$Clean,
    [switch]$Archive,
    [switch]$AllowCpuTorch
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputDir) { $OutputDir = Join-Path $projectRoot "dist-components\local-transformers\$Version" }
$entryPoint = Join-Path $projectRoot "scripts\scansci_local_runtime_entry.py"
$pyInstallerHooks = Join-Path $projectRoot "scripts\pyinstaller_hooks"
$pythonVersion = (& $PythonExecutable -c "import platform; print(platform.python_version())").Trim()
$pyInstallerVersion = (& $PythonExecutable -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
$torchVersion = (& $PythonExecutable -c "import torch; print(torch.__version__)").Trim()
$torchCudaVersion = (& $PythonExecutable -c "import torch; print(torch.version.cuda or '')").Trim()
$bitsAndBytesVersion = (& $PythonExecutable -c "import bitsandbytes; print(bitsandbytes.__version__)").Trim()
if (-not $torchCudaVersion -and -not $AllowCpuTorch) {
    throw "The local runtime requires a CUDA-enabled PyTorch build. Pass -AllowCpuTorch only for a deliberate CPU-only artifact."
}
$buildInputs = @(
    (Join-Path $projectRoot "pyproject.toml"),
    (Join-Path $projectRoot "scripts\freeze_pyinstaller_spec.py"),
    (Join-Path $projectRoot "scripts\archive_runtime_component.py"),
    (Join-Path $projectRoot "scripts\scansci_local_runtime_entry.py"),
    (Join-Path $projectRoot "scripts\pyinstaller_hooks\hook-transformers.py"),
    (Join-Path $projectRoot "src\scansci_html\local_runtime_server.py"),
    (Join-Path $projectRoot "src\scansci_html\local_runtime_daemon.py"),
    (Join-Path $projectRoot "src\scansci_html\local_runtime_compatibility.py"),
    (Join-Path $projectRoot "src\scansci_html\local_runtime_contract.py"),
    (Join-Path $projectRoot "src\scansci_html\local_transformers_compat.py"),
    (Join-Path $projectRoot "src\scansci_html\local_model_inference.py"),
    (Join-Path $projectRoot "src\scansci_html\local_model_market.py"),
    (Join-Path $projectRoot "docs\LOCAL_RUNTIME_THIRD_PARTY_NOTICES.md"),
    $PSCommandPath
)
$hashes = @($buildInputs | ForEach-Object {
    if (-not (Test-Path -LiteralPath $_ -PathType Leaf)) { throw "Local runtime build input is missing: $_" }
    (Get-FileHash -Algorithm SHA256 -LiteralPath $_).Hash
})
if (-not $CacheKey) {
    $material = "local-transformers|$Version|$pythonVersion|$pyInstallerVersion|$torchVersion|$torchCudaVersion|$bitsAndBytesVersion|$($hashes -join '|')"
    $digest = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($material))
    $CacheKey = ([BitConverter]::ToString($digest) -replace '-', '').Substring(0, 16).ToLowerInvariant()
}
if ($CacheKey -notmatch '^[A-Za-z0-9._-]+$') { throw "Invalid CacheKey." }

$cacheName = "local-transformers-$CacheKey"
$buildPath = Join-Path $projectRoot "build\component-cache\$cacheName"
$specPath = Join-Path $projectRoot "build\component-spec\$cacheName"
# Archive builds use a deliberately short staging path.  PyTorch wheels carry
# long internal names and Windows release builders may not have long-path
# support enabled.  The versioned ZIP and manifest still land in OutputDir.
$distPath = $OutputDir
if ($Archive) { $distPath = Join-Path $projectRoot "b\rt-$CacheKey" }
New-Item -ItemType Directory -Force -Path $OutputDir, $distPath, $buildPath, $specPath | Out-Null
$pyInstallerArgs = @(
    "--noconfirm",
    "--windowed",
    "--name", "ScanSciLocalRuntime",
    "--onedir",
    "--paths", (Join-Path $projectRoot "src"),
    "--additional-hooks-dir", $pyInstallerHooks,
    # Avoid --collect-all torch: it traverses torch.testing on Windows and can
    # hang a release build.  The local runtime supports Qwen models through
    # explicit dynamic imports instead of shipping PyTorch's test suite.
    "--collect-binaries", "torch",
    "--collect-binaries", "bitsandbytes",
    "--collect-data", "bitsandbytes",
    "--collect-data", "transformers",
    "--collect-all", "huggingface_hub",
    "--collect-data", "safetensors",
    "--collect-data", "sentence_transformers",
    "--collect-all", "torchvision",
    "--collect-binaries", "sentencepiece",
    "--hidden-import", "torch",
    "--hidden-import", "torch._C",
    "--hidden-import", "torch.cuda",
    "--hidden-import", "torch.nn",
    "--hidden-import", "torch.nn.functional",
    # torch._dynamo discovers its polyfill modules with pkgutil at runtime.
    # PyInstaller cannot see that dynamic import graph, so collect the small
    # module family explicitly instead of discovering missing files one by one.
    "--collect-submodules", "torch._dynamo.polyfills",
    # torch 2.13 imports this internal helper from torch.utils.checkpoint at
    # runtime.  Keep the one required module without recursively collecting
    # PyTorch's test suite.
    "--hidden-import", "torch.testing._internal.logging_tensor",
    # torch._refs uses this small helper during normal initialization.
    "--hidden-import", "torch.testing._internal.common_dtype",
    "--hidden-import", "bitsandbytes",
    "--hidden-import", "safetensors",
    "--hidden-import", "sentence_transformers.sentence_transformer.model",
    "--hidden-import", "sentence_transformers.cross_encoder.model",
    "--hidden-import", "sentence_transformers.base.modules.transformer",
    "--hidden-import", "sentence_transformers.sentence_transformer.modules.pooling",
    "--hidden-import", "sentence_transformers.sentence_transformer.modules.normalize",
    "--hidden-import", "sentencepiece",
    "--hidden-import", "transformers.models.auto.modeling_auto",
    "--hidden-import", "transformers.models.auto.tokenization_auto",
    "--hidden-import", "transformers.utils.quantization_config",
    "--hidden-import", "transformers.generation.stopping_criteria",
    "--hidden-import", "transformers.generation.streamers",
    "--hidden-import", "transformers.generation.utils",
    "--hidden-import", "transformers.modeling_utils",
    "--hidden-import", "transformers.integrations.bitsandbytes",
    "--hidden-import", "transformers.models.qwen3",
    "--hidden-import", "transformers.models.qwen3.configuration_qwen3",
    "--hidden-import", "transformers.models.qwen3.modeling_qwen3",
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
    "--hidden-import", "scansci_html.local_runtime_server",
    "--hidden-import", "scansci_html.local_runtime_daemon",
    "--hidden-import", "scansci_html.local_runtime_compatibility",
    "--distpath", $distPath,
    "--workpath", $buildPath,
    "--specpath", $specPath,
    $entryPoint
)
# These optional training, dataset, and desktop-UI dependencies are not used by
# the inference sidecar.  Excluding them also prevents PyInstaller runtime hooks
# (notably nltk/tkinter) from doing expensive work before the health server can
# start listening.
foreach ($module in @("__main__", "boto3", "botocore", "cv2", "datasets", "IPython", "jupyter", "keras", "matplotlib", "nltk", "pandas", "pyarrow", "pygame", "pytest", "tensorflow", "tkinter", "_tkinter", "torchaudio", "torch.utils.benchmark", "transformers.pipelines")) {
    $pyInstallerArgs += @("--exclude-module", $module)
}
if ($Clean) { $pyInstallerArgs = @("--clean") + $pyInstallerArgs }

$lockPath = Join-Path (Split-Path -Parent $buildPath) "$cacheName.lock"
$specFile = Join-Path $specPath "ScanSciLocalRuntime.spec"
$lock = $null
try {
    try { $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw "Another local-runtime build is using cache $CacheKey." }
    Write-Host "Reusable local-runtime cache: $buildPath"
    if (Test-Path -LiteralPath $specFile -PathType Leaf) {
        Write-Host "Reusing stable local-runtime spec: $specFile"
        $runArgs = @("--noconfirm", "--distpath", $distPath, "--workpath", $buildPath)
        if ($Clean) { $runArgs = @("--clean") + $runArgs }
        $runArgs += $specFile
        & $PythonExecutable -m PyInstaller @runArgs
    } else {
        & $PythonExecutable -m PyInstaller @pyInstallerArgs
    }
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        $analysisTocPath = Join-Path $buildPath "ScanSciLocalRuntime\Analysis-00.toc"
        if ((Test-Path -LiteralPath $specFile -PathType Leaf) -and (Test-Path -LiteralPath $analysisTocPath -PathType Leaf)) {
            & $PythonExecutable (Join-Path $projectRoot "scripts\freeze_pyinstaller_spec.py") --spec $specFile --analysis-toc $analysisTocPath
            if ($LASTEXITCODE -ne 0) { throw "Failed to freeze local-runtime PyInstaller inputs." }
        }
    }
} finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
if ($exitCode -ne 0) { exit $exitCode }

$packageDir = Join-Path $distPath "ScanSciLocalRuntime"
$runtimeExecutable = Join-Path $packageDir "ScanSciLocalRuntime.exe"
if (-not (Test-Path -LiteralPath $runtimeExecutable -PathType Leaf)) {
    throw "Local runtime executable is missing: $runtimeExecutable"
}
$diagnosticsPath = Join-Path $OutputDir "local-runtime-diagnostics.json"
$diagnosticArgs = @("--diagnose-output", ('"{0}"' -f $diagnosticsPath))
$diagnosticProcess = Start-Process `
    -FilePath $runtimeExecutable `
    -ArgumentList $diagnosticArgs `
    -WindowStyle Hidden `
    -PassThru
if (-not $diagnosticProcess.WaitForExit(300000)) {
    Stop-Process -Id $diagnosticProcess.Id -Force -ErrorAction SilentlyContinue
    throw "Packaged local runtime diagnostics timed out."
}
$diagnosticProcess.Refresh()
if (-not (Test-Path -LiteralPath $diagnosticsPath -PathType Leaf)) {
    throw "Packaged local runtime did not produce a diagnostics report."
}
$diagnostics = Get-Content -LiteralPath $diagnosticsPath -Raw | ConvertFrom-Json
if ($diagnosticProcess.ExitCode -ne 0 -or -not [bool]$diagnostics.ok) {
    throw "Packaged local runtime diagnostics failed: $($diagnostics.error)"
}
Write-Host "Packaged local runtime diagnostics passed: $diagnosticsPath"

if ($Archive) {
    $archiveArgs = @(
        (Join-Path $projectRoot "scripts\archive_runtime_component.py"),
        "--package-dir", $packageDir,
        "--output-dir", $OutputDir,
        "--version", $Version,
        "--notice-file", (Join-Path $projectRoot "docs\LOCAL_RUNTIME_THIRD_PARTY_NOTICES.md"),
        "--part-size-mb", $PartSizeMb
    )
    if ($PackageUrl) { $archiveArgs += @("--package-url", $PackageUrl) }
    & $PythonExecutable @archiveArgs
    if ($LASTEXITCODE -ne 0) { throw "Failed to archive local runtime component." }
    Write-Host "Component manifest: $(Join-Path $OutputDir 'local-transformers.json')"
}
