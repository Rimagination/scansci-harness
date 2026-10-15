from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_build_defaults_to_lightweight_core_and_reuses_dependency_keyed_cache() -> None:
    script = (ROOT / "scripts" / "build_desktop.ps1").read_text(encoding="utf-8-sig")

    assert '[string]$PackageProfile = "core"' in script
    assert '[string]$UpdateManifestUrl = "https://github.com/Rimagination/scansci-harness/releases/latest/download/stable.json"' in script
    assert 'https://github.com/Rimagination/scansci-portal/releases/download/local-runtime-v1.0.0/local-transformers.json' in script
    assert '[string]$NodeComponentManifestUrl' in script
    assert '[string]$TectonicComponentManifestUrl' in script
    assert '[string]$ReleasePublicKey = ""' in script
    assert 'Slim channel: do not embed node.exe or tectonic.exe in the bundle.' in script
    assert 'build\\desktop-cache\\$cacheName' in script
    assert '$ExcludeRuntimes|$ElectronBackend|$UpdateManifestUrl|$RuntimeManifestUrl|$NodeComponentManifestUrl|$TectonicComponentManifestUrl' in script
    assert '$sourceTreeHash' in script
    assert 'source_tree_sha256 = $sourceTreeHash' in script
    assert 'release_source_sha256 = $ReleaseSourceSha256.ToLowerInvariant()' in script
    assert 'exclude_runtimes = [bool]$ExcludeRuntimes' in script
    assert 'update_manifest_url = $UpdateManifestUrl' in script
    assert 'node_component_manifest_url = $NodeComponentManifestUrl' in script
    assert 'tectonic_component_manifest_url = $TectonicComponentManifestUrl' in script
    assert 'release_public_key = $ReleasePublicKey' in script
    assert '"--additional-hooks-dir", $pyInstallerHooks' in script
    assert 'scripts\\pyinstaller_hooks' in script
    assert '(Join-Path $pyInstallerHooks "hook-litellm.py")' in script
    assert '"--collect-data", "litellm"' not in script
    assert 'build\\desktop\\$resolvedBuildId' not in script
    assert 'if ($PackageProfile -eq "full")' in script
    assert 'Reusing stable PyInstaller spec' in script
    assert 'Copy-Item -LiteralPath $buildInfoPath' in script
    assert '"--collect-binaries", "torch"' in script
    assert '"--collect-data", "torch"' in script
    assert '"--collect-binaries", "bitsandbytes"' in script
    assert '"--collect-data", "sentence_transformers"' in script
    assert '"--collect-binaries", "sentencepiece"' in script
    assert '"--hidden-import", "sentence_transformers.sentence_transformer.model"' in script
    assert '"--hidden-import", "sentence_transformers.cross_encoder.model"' in script
    assert '"--hidden-import", "sentence_transformers.base.modules.transformer"' in script
    assert '"--hidden-import", "sentence_transformers.sentence_transformer.modules.pooling"' in script
    assert '"--hidden-import", "sentence_transformers.sentence_transformer.modules.normalize"' in script
    assert '"--hidden-import", "transformers.models.auto.modeling_auto"' in script
    assert '"--hidden-import", "transformers.generation.streamers"' in script
    assert '"--hidden-import", "transformers.models.qwen3.modeling_qwen3"' in script
    assert '"--hidden-import", "transformers.models.qwen3_asr.modeling_qwen3_asr"' in script
    assert '"--hidden-import", "transformers.models.hrm_text.modeling_hrm_text"' in script
    assert '"--hidden-import", "transformers.models.minicpmv4_6.modeling_minicpmv4_6"' in script
    assert '"--hidden-import", "transformers"' not in script
    assert '"--hidden-import", "sentence_transformers"' not in script
    assert '"--hidden-import", "torch.testing._internal.logging_tensor"' in script
    assert '"--hidden-import", "torch.testing._internal.common_dtype"' in script
    assert '"--collect-submodules", "torch._dynamo.polyfills"' in script
    assert '"torch.testing._internal",' not in script
    assert '"torch.testing",' not in script
    assert 'import torch, transformers, sentence_transformers, sentencepiece' in script
    core_excludes = script.split('if ($PackageProfile -eq "core")', 1)[1]
    assert '"torch"' in core_excludes
    assert '"pyarrow"' in core_excludes
    assert '"__main__"' in script
    assert 'LITELLM_LOCAL_MODEL_COST_MAP' in script
    assert 'freeze_pyinstaller_spec.py' in script


def test_local_runtime_has_an_independent_versioned_build() -> None:
    script = (ROOT / "scripts" / "build_local_runtime.ps1").read_text(encoding="utf-8-sig")

    assert '[string]$Version = "1.0.4"' in script
    assert 'ScanSciLocalRuntime' in script
    assert '"--additional-hooks-dir", $pyInstallerHooks' in script
    assert 'build\\component-cache\\$cacheName' in script
    assert 'local-transformers.json' in script
    assert '"--collect-binaries", "torch"' in script
    assert '"--collect-data", "torch"' not in script
    assert '"--collect-binaries", "bitsandbytes"' in script
    assert '"--collect-data", "sentence_transformers"' in script
    assert '"--collect-binaries", "sentencepiece"' in script
    assert '"--hidden-import", "sentence_transformers.sentence_transformer.model"' in script
    assert '"--hidden-import", "sentence_transformers.cross_encoder.model"' in script
    assert '"--hidden-import", "sentence_transformers.base.modules.transformer"' in script
    assert '"--hidden-import", "sentence_transformers.sentence_transformer.modules.pooling"' in script
    assert '"--hidden-import", "sentence_transformers.sentence_transformer.modules.normalize"' in script
    assert '"--hidden-import", "transformers.models.auto.modeling_auto"' in script
    assert '"--hidden-import", "transformers.generation.streamers"' in script
    assert '"--hidden-import", "transformers.pipelines"' not in script
    assert '"--hidden-import", "transformers.models.qwen3.modeling_qwen3"' in script
    assert '"--hidden-import", "transformers.models.qwen3_asr.modeling_qwen3_asr"' in script
    assert '"--hidden-import", "transformers.models.hrm_text.modeling_hrm_text"' in script
    assert '"--hidden-import", "transformers.models.minicpmv4_6.modeling_minicpmv4_6"' in script
    assert '"--hidden-import", "transformers"' not in script
    assert '"--hidden-import", "sentence_transformers"' not in script
    assert '"--hidden-import", "torch.testing._internal.logging_tensor"' in script
    assert '"--hidden-import", "torch.testing._internal.common_dtype"' in script
    assert '"--collect-submodules", "torch._dynamo.polyfills"' in script
    assert '"torch.testing._internal",' not in script
    assert '"scipy",' not in script
    assert '"sklearn",' not in script
    assert '"torch.testing",' not in script
    for optional_module in (
        '"datasets"',
        '"nltk"',
        '"pyarrow"',
        '"tkinter"',
        '"_tkinter"',
        '"transformers.pipelines"',
    ):
        assert optional_module in script
    assert 'torch.version.cuda' in script
    assert 'Reusing stable local-runtime spec' in script
    assert 'freeze_pyinstaller_spec.py' in script
    assert 'docs\\LOCAL_RUNTIME_THIRD_PARTY_NOTICES.md' in script
    assert 'b\\rt-$CacheKey' in script
    assert 'if ($PackageUrl)' in script
    assert 'local_runtime_server.py' in script
    assert 'local_runtime_contract.py' in script
    assert 'local_transformers_compat.py' in script
    assert 'local_runtime_component.py' not in script
    assert 'local_model_inference.py' in script
    assert '"--part-size-mb", $PartSizeMb' in script
    assert '$PythonExecutable -m PyInstaller' in script
    assert '"--diagnose-output"' in script
    assert "Packaged local runtime diagnostics passed" in script
    assert "$diagnosticProcess.WaitForExit(300000)" in script


def test_runtime_entry_defers_transformers_configuration_until_model_load() -> None:
    entry = (ROOT / "scripts" / "scansci_local_runtime_entry.py").read_text(encoding="utf-8")
    local_asr = (ROOT / "src" / "scansci_html" / "local_asr.py").read_text(encoding="utf-8")

    assert "configure_text_only_transformers" not in entry
    configure = local_asr.index("configure_text_only_transformers()")
    transformers_import = local_asr.index('importlib.import_module("transformers")')
    assert configure < transformers_import


def test_qwen_desktop_hook_does_not_recursively_collect_pytorch_test_modules() -> None:
    hook = (ROOT / "scripts" / "pyinstaller_hooks" / "hook-torch.py").read_text(encoding="utf-8")

    assert 'collect_submodules("torch")' not in hook
    assert '"**/testing/**"' in hook
    assert 'collect_dynamic_libs(' in hook


def test_transformers_hook_copies_only_shallow_runtime_metadata() -> None:
    hook = (ROOT / "scripts" / "pyinstaller_hooks" / "hook-transformers.py").read_text(encoding="utf-8")

    assert "copy_metadata" not in hook
    assert '"METADATA"' in hook
    assert "metadata_dir.iterdir()" in hook
    assert "rglob" not in hook


def test_litellm_hook_keeps_runtime_data_but_excludes_guardrail_benchmarks() -> None:
    hook = (ROOT / "scripts" / "pyinstaller_hooks" / "hook-litellm.py").read_text(encoding="utf-8")

    assert 'collect_data_files(' in hook
    assert '"litellm"' in hook
    assert '"**/guardrail_benchmarks/**"' in hook
