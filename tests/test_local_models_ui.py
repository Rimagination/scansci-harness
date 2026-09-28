from pathlib import Path


APP_JS = Path(__file__).parents[1] / "src" / "scansci_html" / "web" / "app.js"
STYLES_CSS = Path(__file__).parents[1] / "src" / "scansci_html" / "web" / "styles.css"


def test_local_models_page_does_not_duplicate_default_capability_routing():
    app_js = APP_JS.read_text(encoding="utf-8")

    assert 'state.activeSettings === "local-models") settingsMarkup = renderLocalModelsSettingsPage();' in app_js
    assert "function renderLocalModelsSettingsPage()" in app_js
    assert "stripLegacyLocalAgentRouting(page)" in app_js
    assert 'class="local-model-detection-note"' in app_js
    assert "readyConversationSnapshots" in app_js
    assert 'class="composer-model-note"' in app_js
    assert "function renderDefaultCapabilitiesSettings()" in app_js
    assert "function conversationModelAvailability(model, provider = null)" in app_js
    assert "applyModelSelectionCatalog(state.modelHealth);" in app_js
    assert 'disabled aria-disabled=\\"true\\"' in app_js
    assert "\\u6682\\u4e0d\\u53ef\\u7528\\u7684\\u5bf9\\u8bdd\\u6a21\\u578b\\u4f1a\\u663e\\u793a\\u539f\\u56e0" in app_js
    assert "unavailable_reason" in app_js


def test_unavailable_local_model_rows_use_semantic_theme_tokens():
    styles = STYLES_CSS.read_text(encoding="utf-8")

    assert ".composer-model-option:disabled" in styles
    assert "background: var(--control-background)" in styles
    assert ".composer-model-status" in styles
    assert "background: var(--surface-elevated)" in styles


def test_pagefile_errors_are_not_labeled_as_gpu_vram_errors():
    app_js = APP_JS.read_text(encoding="utf-8")
    status_logic = app_js.split("function localModelStatusLabel", 1)[1].split(
        "function conversationModelAvailability", 1
    )[0]

    assert status_logic.index("1455") < status_logic.index("out of memory")
    assert "\\u865a\\u62df\\u5185\\u5b58\\u4e0d\\u8db3" in status_logic
