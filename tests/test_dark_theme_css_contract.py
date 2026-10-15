from __future__ import annotations

import re
from pathlib import Path


STYLES_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "scansci_html"
    / "web"
    / "styles.css"
)
AGENTS_PATH = Path(__file__).parents[1] / "AGENTS.md"
STYLES = STYLES_PATH.read_text(encoding="utf-8")
CSS_WITHOUT_COMMENTS = re.sub(r"/\*.*?\*/", "", STYLES, flags=re.DOTALL)
RULES = tuple(
    (tuple(part.strip() for part in selectors.split(",")), body)
    for selectors, body in re.findall(
        r"([^{}]+)\{([^{}]*)\}", CSS_WITHOUT_COMMENTS, flags=re.DOTALL
    )
)


def property_values(selector: str, property_name: str) -> tuple[str, ...]:
    pattern = re.compile(
        rf"(?:^|;)\s*{re.escape(property_name)}\s*:\s*([^;}}]+)",
        flags=re.IGNORECASE,
    )
    return tuple(
        match.group(1).strip()
        for selectors, body in RULES
        if selector in selectors
        for match in pattern.finditer(body)
    )


def assert_property(selector: str, property_name: str, expected: str) -> None:
    values = property_values(selector, property_name)
    assert expected in values, (
        f"Expected {selector!r} to set {property_name}: {expected}; "
        f"found {values or 'no matching declaration'}"
    )


def assert_last_property(selector: str, property_name: str, expected: str) -> None:
    values = property_values(selector, property_name)
    assert values and values[-1] == expected, (
        f"Expected the effective {selector!r} declaration to set "
        f"{property_name}: {expected}; found {values or 'no matching declaration'}"
    )


def test_repository_defines_and_documents_the_semantic_theme_contract() -> None:
    assert_last_property(":root", "--page-background", "var(--surface)")
    assert_last_property(":root", "--surface-elevated", "var(--raised)")
    assert_last_property(":root", "--control-background", "var(--raised)")
    assert_last_property(":root", "--control-background-hover", "var(--soft)")

    agent_rules = AGENTS_PATH.read_text(encoding="utf-8")
    assert "## 深色主题设计契约" in agent_rules
    assert "语义颜色变量" in agent_rules
    assert "同时验证 light/dark" in agent_rules
    assert "禁止" in agent_rules and "硬编码浅色背景" in agent_rules


def test_dark_assistant_reply_uses_light_text_and_keeps_error_semantics() -> None:
    assert_last_property(
        'html[data-theme="dark"] .answer-sentence',
        "color",
        "var(--ink)",
    )
    assert_last_property(
        'html[data-theme="dark"] .direct-answer .answer-sentence h2',
        "color",
        "var(--ink)",
    )
    assert_last_property(
        'html[data-theme="dark"] .evidence-grounded-article .answer-sentence h1',
        "color",
        "var(--ink)",
    )
    assert_last_property(
        'html[data-theme="dark"] .direct-answer .answer-sentence code',
        "background",
        "var(--surface-elevated)",
    )
    assert_last_property(
        'html[data-theme="dark"] .direct-answer .answer-sentence blockquote',
        "color",
        "var(--muted)",
    )
    assert_last_property(
        'html[data-theme="dark"] .assistant-table',
        "background",
        "var(--surface-elevated)",
    )

    # Light-theme copy and explicit error semantics must remain distinct.
    assert_property(".answer-sentence", "color", "#2e3035")
    assert_property(
        'html[data-theme="dark"] .stream-error-card', "color", "#e8caca"
    )
    assert_property(
        'html[data-theme="dark"] .stream-error-card', "background", "#2b2022"
    )


def test_dark_history_is_readable_without_flattening_status_meaning() -> None:
    assert_last_property(
        'html[data-theme="dark"] .history-collapse', "color", "var(--ink)"
    )
    assert_last_property(
        'html[data-theme="dark"] .task-item', "color", "var(--ink)"
    )
    assert_last_property(
        'html[data-theme="dark"] .task-item time', "color", "var(--muted)"
    )
    assert_last_property(".task-row:hover", "background", "var(--hover-background)")
    assert_last_property(
        ".task-row:has(.task-item.is-active)",
        "background",
        "var(--selection-background)",
    )
    assert_property('html[data-theme="dark"]', "--hover-background", "#2c2f30")
    assert_property('html[data-theme="dark"]', "--selection-background", "#373a3b")
    assert_last_property(
        'html[data-theme="dark"] .history-empty', "color", "var(--muted)"
    )

    # State meaning remains on the status dot; dark text rules must not erase it.
    assert_property(".task-status.completed::before", "background", "#3d8a59")
    assert_property(".task-status.failed::before", "background", "var(--danger)")
    assert_property(
        ".task-status.paused::before", "background", "#a68b62"
    )


def test_dark_model_selector_is_a_dark_control_with_light_text() -> None:
    selector = 'html[data-theme="dark"] .composer-model-trigger'
    assert_last_property(selector, "color", "var(--ink)")
    assert_last_property(selector, "background", "var(--control-background)")
    assert_last_property(selector, "border-color", "var(--rule-strong)")
    assert_last_property(
        'html[data-theme="dark"] .composer-model-trigger:hover',
        "background",
        "var(--control-background-hover)",
    )
    assert_last_property(
        'html[data-theme="dark"] .composer-model.is-open .composer-model-trigger',
        "background",
        "var(--control-background-hover)",
    )

    # The unthemed control remains the light-theme contract.
    assert_property(".composer-model-trigger", "color", "#4e5156")
    assert_property(".composer-model-trigger", "background", "#fff")


def test_settings_pages_consume_semantic_surfaces_and_text() -> None:
    assert_last_property(".settings-view", "background", "var(--canvas)")
    assert_last_property(".settings-sidebar", "border-right", "0")
    assert_last_property(
        'html:not([data-theme="dark"]) .settings-view',
        "background",
        "var(--canvas)",
    )
    assert_last_property(".settings-content", "background", "var(--page-background)")
    assert_last_property(".settings-surface", "background", "transparent")
    assert_last_property(".settings-surface", "border", "0")
    assert_last_property(".settings-page-heading h1", "color", "var(--ink)")
    assert_last_property(".settings-page-heading p", "color", "var(--muted)")
    assert_last_property(
        ".settings-select-trigger", "background", "var(--control-background)"
    )
    assert_last_property(".settings-select-trigger", "color", "var(--ink)")
    assert_last_property(".settings-select-model-meta", "color", "var(--muted)")


def test_settings_surface_is_flat_in_both_themes() -> None:
    assert_last_property(".settings-surface", "width", "100%")
    assert_last_property(".settings-surface", "min-height", "0")
    assert_last_property(".settings-surface", "margin", "0")
    assert_last_property(".settings-surface", "padding", "0")
    assert_last_property(".settings-surface", "border-radius", "0")
    assert_last_property(".settings-surface", "box-shadow", "none")
    assert_last_property(
        'html[data-theme="dark"] .settings-surface', "background", "transparent"
    )


def test_settings_content_keeps_the_top_left_corner_rounded_in_both_themes() -> None:
    assert_last_property(".settings-content", "border-top-left-radius", "var(--radius-2xl)")
    assert_last_property(
        ".settings-view.is-active > .settings-content",
        "border-top-left-radius",
        "var(--radius-2xl)",
    )
    assert_last_property(
        'html[data-theme="dark"] .settings-view', "background", "var(--canvas)"
    )


def test_active_settings_nav_uses_soft_neutral_selection() -> None:
    assert_last_property(".settings-nav.is-active", "border-radius", "var(--radius-md)")
    assert_last_property(".settings-nav.is-active", "color", "var(--ink)")
    assert_last_property(
        ".settings-nav.is-active", "background", "var(--selection-background)"
    )
    assert_last_property(".settings-nav.is-active", "box-shadow", "none")


def test_corner_radii_use_one_scale_and_square_artwork_masks() -> None:
    for token, value in {
        "xs": "4px", "sm": "8px", "md": "12px", "lg": "16px",
        "xl": "20px", "2xl": "24px", "logo": "23%",
        "circle": "50%", "pill": "999px",
    }.items():
        assert_last_property(":root", f"--radius-{token}", value)

    radius_values = re.findall(
        r"\bborder-(?:(?:top|bottom)-(?:left|right)-)?radius\s*:\s*([^;]+);",
        CSS_WITHOUT_COMMENTS,
    )
    assert radius_values
    assert all(
        not re.sub(r"var\(--radius-[\w-]+\)|\b(?:0|inherit)\b|\s+", "", value)
        for value in radius_values
    )
    assert_last_property(".ima-source-entry img", "border-radius", "var(--radius-logo)")
    assert_last_property(".ima-library-title > img", "border-radius", "var(--radius-logo)")
    assert_last_property(".settings-brand img", "border-radius", "var(--radius-logo)")
    assert_last_property(".provider-logo.is-large", "border-radius", "var(--radius-logo)")
    personal_icon = (STYLES_PATH.parent / "knowledge-personal.svg").read_text(encoding="utf-8")
    zotero_icon = (STYLES_PATH.parent / "zotero-logo.svg").read_text(encoding="utf-8")
    assert 'width="48" height="48" rx="11"' in personal_icon
    assert 'width="48" height="48" rx="11"' in zotero_icon


def test_library_search_can_shrink_beside_onboarding_back() -> None:
    assert_last_property(".ima-library-appbar-actions", "flex", "0 1 auto")
    assert_last_property(".ima-library-appbar-search-field", "min-width", "96px")


def test_dark_open_mode_keeps_keyboard_focus_glow() -> None:
    assert_last_property(
        'html[data-theme="dark"] .mode-picker.is-open .mode-trigger:focus-visible',
        "box-shadow",
        "var(--focus-glow)",
    )


def test_ocr_select_menu_can_float_above_adjacent_service_cards() -> None:
    assert_last_property(".settings-select-menu", "z-index", "130")
    assert_last_property(
        ".settings-content .document-service-card:has(.settings-select.is-open)",
        "z-index",
        "130",
    )
    assert_last_property(
        ".settings-content .document-service-card:has(.settings-select.is-open)",
        "isolation",
        "auto",
    )


def test_ocr_language_selector_uses_the_api_field_scale_and_next_line() -> None:
    selector = (
        ".settings-surface:has(.default-capabilities-page) "
        ".default-tools-section .document-language-row"
    )
    assert_last_property(selector, "display", "grid")
    assert_last_property(selector, "grid-template-columns", "1fr")
    assert_last_property(selector, "font-size", "calc(12px * var(--settings-font-scale))")
    assert_last_property(
        f"{selector} > .settings-select", "width", "100%"
    )


def test_local_model_sections_match_default_assistant_typography() -> None:
    assert_last_property(".settings-content .local-models-page", "font-family", "var(--ui)")
    for selector in (
        ".settings-content .local-installed-panel h2",
        ".settings-content .local-model-market-disclosure > summary",
    ):
        assert_last_property(
            selector, "font-size", "calc(16px * var(--settings-font-scale))"
        )
        assert_last_property(selector, "line-height", "21px")
    for selector in (
        ".settings-content .local-installed-panel .quiet-model-row strong",
        ".settings-content .local-model-market-disclosure .quiet-model-row strong",
    ):
        assert_last_property(
            selector, "font-size", "calc(12px * var(--settings-font-scale))"
        )
        assert_last_property(selector, "line-height", "17px")


def test_settings_visual_system_is_shared_and_interface_scale_reaches_the_app_shell() -> None:
    assert_last_property(":root", "--ui-font-scale", "1")
    assert_last_property('html[data-font-scale="small"]', "--ui-font-scale", ".92")
    assert_last_property('html[data-font-scale="large"]', "--ui-font-scale", "1.08")
    assert_last_property(".workbench", "zoom", "var(--ui-font-scale)")
    assert_last_property(".settings-content", "font-family", "var(--settings-font-family)")
    assert_last_property(".settings-content", "--settings-font-scale", "1")
    assert_last_property(".settings-content", "color", "var(--ink)")
    assert_last_property(".settings-content", "background", "var(--page-background)")
    assert_last_property(".settings-content .settings-page-heading h1", "font-size", "calc(var(--settings-page-title) * var(--settings-font-scale))")


def test_settings_pages_drop_decorative_horizontal_rules() -> None:
    assert_last_property(".settings-sidebar-bottom", "border-top", "0")
    for selector, property_name in (
        (".settings-content .settings-minimal-section", "border-top"),
        (".settings-content .settings-row", "border-bottom"),
        (".settings-content .default-capability-panel > header", "border-bottom"),
        (".settings-content .default-capability-row", "border-bottom"),
        (".settings-content .local-installed-panel > header", "border-bottom"),
        (".settings-content .local-model-disclosure", "border-bottom"),
        (".settings-content .about-card-heading", "border-bottom"),
        (".settings-content .about-row", "border-top"),
        (".software-update-card > header", "border-bottom"),
    ):
        assert_last_property(selector, property_name, "0")


def test_software_update_page_uses_the_settings_visual_system() -> None:
    assert_last_property(".software-update-page", "font-family", "var(--ui)")
    assert_last_property(".software-update-card", "border-color", "var(--rule)")
    assert_last_property(".software-update-card", "background", "var(--surface)")
    assert_last_property(".software-update-page h1", "color", "var(--ink)")
    assert_last_property(".software-update-page p", "color", "var(--muted)")


def test_provider_list_consumes_semantic_surfaces_and_text() -> None:
    assert_last_property(
        ".cherry-model-services", "background", "var(--page-background)"
    )
    assert_last_property(
        ".cherry-provider-catalog", "background", "var(--surface-elevated)"
    )
    assert_last_property(".cherry-provider-panel", "background", "var(--surface)")
    assert_last_property(".cherry-provider-button", "color", "var(--ink)")
    assert_last_property(
        ".cherry-provider-search", "background", "var(--control-background)"
    )
    assert_last_property(
        ".cherry-provider-item:hover",
        "background",
        "var(--control-background-hover)",
    )


def test_model_service_editor_consumes_semantic_text_and_controls() -> None:
    # Playwright visual QA exercises the provider editor, not only the catalog.
    assert_last_property(".cherry-field", "color", "var(--ink)")
    assert_last_property(".cherry-field > span", "color", "var(--ink)")
    assert_last_property(".cherry-field > span i", "color", "var(--muted)")
    assert_last_property(".cherry-field input", "color", "var(--ink)")
    assert_last_property(".cherry-field select", "color", "var(--ink)")
    assert_last_property(
        ".cherry-field input", "background", "var(--control-background)"
    )
    assert_last_property(
        ".cherry-field select", "background", "var(--control-background)"
    )

    assert_last_property(".cherry-provider-name h1", "color", "var(--ink)")
    assert_last_property(
        ".cherry-model-section-title h2", "color", "var(--ink)"
    )
    assert_last_property(".cherry-model-group > header", "color", "var(--ink)")

    assert_last_property(
        ".cherry-provider-search input::placeholder", "color", "var(--muted)"
    )
    assert_last_property(".cherry-model-search", "color", "var(--muted)")
    assert_last_property(".cherry-model-search input", "color", "var(--ink)")
    assert_last_property(
        ".cherry-model-search input::placeholder", "color", "var(--muted)"
    )

    assert_last_property(".cherry-model-copy > button", "color", "var(--ink)")
    assert_last_property(".cherry-model-copy small", "color", "var(--muted)")
    assert_last_property(".cherry-field-hint", "color", "var(--muted)")
    assert_last_property(".cherry-endpoint-preview", "color", "var(--muted)")
    assert_last_property(".cherry-provider-empty", "color", "var(--muted)")

    for selector in (
        ".cherry-detect-button",
        ".cherry-fetch-button",
        ".cherry-plus-button",
        ".cherry-add-provider",
    ):
        assert_last_property(selector, "color", "var(--ink)")
        assert_last_property(selector, "background", "var(--control-background)")

    for selector in (
        ".cherry-text-button",
        ".cherry-restore-default",
        ".cherry-mini-gear",
        ".cherry-icon-button",
        ".cherry-model-more summary",
        ".cherry-row-remove",
    ):
        assert_last_property(selector, "color", "var(--muted)")


def test_mcp_marketplace_consumes_semantic_surfaces_and_text() -> None:
    assert_last_property(".mcp-view", "background", "var(--page-background)")
    assert_last_property(
        ".settings-content:has(.mcp-marketplace)",
        "background",
        "var(--page-background)",
    )
    assert_last_property(".mcp-marketplace", "color", "var(--ink)")
    assert_last_property(".mcp-market-card", "background", "var(--surface)")
    assert_last_property(".mcp-market-card", "border-color", "var(--rule)")
    assert_last_property(".mcp-market-card h2", "color", "var(--ink)")
    assert_last_property(".mcp-market-card > p", "color", "var(--muted)")
    assert_last_property(
        ".mcp-market-hero", "background", "radial-gradient(circle at 82% 46%, var(--accent-ring), transparent 23%), var(--surface-elevated)"
    )
    assert_last_property(".mcp-market-tabs", "border-color", "var(--rule)")
    assert_last_property(
        ".mcp-market-tab span", "background", "var(--control-background)"
    )
    assert_last_property(
        ".mcp-sort-control", "background", "var(--surface-elevated)"
    )
    assert_last_property(
        ".mcp-sort-control button.is-active",
        "background",
        "var(--accent-surface)",
    )
    assert_last_property(
        ".mcp-market-search", "background", "var(--control-background)"
    )
    assert_last_property(".mcp-market-search input", "color", "var(--ink)")


def test_mcp_product_accents_follow_the_selected_theme() -> None:
    # MCP product chrome follows the user's accent. Third-party provider/logo
    # artwork is intentionally outside this selector contract.
    assert_last_property(".mcp-market-eyebrow", "color", "var(--accent-ink)")
    assert_last_property(
        ".mcp-market-orbit::before", "border-color", "var(--accent-border)"
    )
    assert_last_property(
        ".mcp-market-orbit::after", "border-color", "var(--accent-border)"
    )
    assert_last_property(
        ".mcp-market-orbit i",
        "background",
        "radial-gradient(circle at 35% 28%, var(--accent-surface-strong), var(--accent) 55%, var(--accent-strong))",
    )
    assert_last_property(
        ".mcp-market-orbit i", "box-shadow", "0 8px 24px var(--accent-shadow)"
    )
    assert_last_property(".mcp-market-orbit b", "background", "var(--accent)")
    assert_last_property(
        ".mcp-market-orbit b", "box-shadow", "0 0 0 6px var(--accent-ring)"
    )
    assert_last_property(
        ".mcp-market-orbit em", "background", "var(--accent-muted)"
    )

    assert_last_property(
        ".mcp-market-tab.is-active", "border-bottom-color", "var(--accent)"
    )
    assert_last_property(
        ".mcp-market-tab.is-active", "color", "var(--accent-ink)"
    )
    assert_last_property(
        ".mcp-market-tab.is-active span", "color", "var(--accent-ink)"
    )
    assert_last_property(
        ".mcp-market-tab.is-active span", "background", "var(--accent-surface)"
    )
    assert_last_property(
        ".mcp-sort-control button.is-active", "color", "var(--accent-ink)"
    )
    assert_last_property(
        ".mcp-sort-control button.is-active",
        "background",
        "var(--accent-surface)",
    )
    assert_last_property(
        ".mcp-market-search:focus-within",
        "border-color",
        "var(--accent)",
    )
    assert_last_property(
        ".mcp-market-search:focus-within",
        "box-shadow",
        "var(--focus-glow)",
    )

    for selector in (
        ".mcp-card-icon",
        ".mcp-card-tags .mcp-discipline-tag",
        ".mcp-install-button",
        ".mcp-owned-icon",
        ".mcp-owned-actions .mcp-update-button",
        ".mcp-manual-trigger",
    ):
        assert_last_property(selector, "color", "var(--accent-ink)")
        assert_last_property(selector, "background", "var(--accent-surface)")

    assert_last_property(".mcp-card-icon", "border-color", "var(--accent-border)")
    assert_last_property(
        ".mcp-install-button", "border-color", "var(--accent-border)"
    )
    assert_last_property(
        ".mcp-owned-actions .mcp-update-button",
        "border-color",
        "var(--accent-border)",
    )
    assert_last_property(
        ".mcp-manual-trigger", "border-color", "var(--accent-border)"
    )

    for selector in (".mcp-create-button", ".mcp-manual-form footer button"):
        assert_last_property(selector, "border-color", "var(--accent)")
        assert_last_property(selector, "color", "var(--accent-contrast)")
        assert_last_property(selector, "background", "var(--accent)")

    assert_last_property(
        ".mcp-install-button:hover", "background", "var(--accent)"
    )
    assert_last_property(
        ".mcp-install-button:hover", "color", "var(--accent-contrast)"
    )
    assert_last_property(
        ".mcp-market-loading .ui-icon", "color", "var(--accent)"
    )
    assert_last_property(
        ".mcp-market-empty > .ui-icon", "color", "var(--accent)"
    )
    assert_last_property(".mcp-market-note .ui-icon", "color", "var(--accent)")


def test_mcp_creation_flow_consumes_accent_and_semantic_tokens() -> None:
    assert_last_property(".mcp-create-dialog", "color", "var(--ink)")
    assert_last_property(".mcp-create-dialog", "background", "var(--surface)")
    assert_last_property(".mcp-create-dialog-top span", "color", "var(--accent-ink)")
    assert_last_property(
        ".mcp-create-steps .is-current", "color", "var(--accent-ink)"
    )
    assert_last_property(
        ".mcp-create-steps .is-current span", "color", "var(--accent-contrast)"
    )
    assert_last_property(
        ".mcp-create-steps .is-current span", "background", "var(--accent)"
    )

    for selector in (
        ".mcp-create-mark",
        ".mcp-create-option-icon",
        ".mcp-create-option-icon.is-remote",
    ):
        assert_last_property(selector, "color", "var(--accent-ink)")
        assert_last_property(selector, "background", "var(--accent-surface)")

    for selector in (
        ".mcp-create-intro p",
        ".mcp-create-form > header > div span",
        ".mcp-create-connection-label",
        ".mcp-create-review > span",
    ):
        assert_last_property(selector, "color", "var(--accent-ink)")

    assert_last_property(
        ".mcp-create-connection p .ui-icon", "color", "var(--accent)"
    )
    assert_last_property(
        ".mcp-create-form footer .mcp-create-save",
        "border-color",
        "var(--accent)",
    )
    assert_last_property(
        ".mcp-create-form footer .mcp-create-save",
        "color",
        "var(--accent-contrast)",
    )
    assert_last_property(
        ".mcp-create-form footer .mcp-create-save",
        "background",
        "var(--accent)",
    )


def test_knowledge_library_consumes_semantic_surfaces_and_text() -> None:
    assert_last_property(".library-root-toolbar", "background", "var(--surface)")
    assert_last_property(".library-source", "background", "var(--surface)")
    assert_last_property(".knowledge-library-hero h2", "color", "var(--ink)")
    assert_last_property(".knowledge-library-hero p", "color", "var(--muted)")
    assert_last_property(".knowledge-import-card", "background", "var(--surface)")
    assert_last_property(".knowledge-import-card", "color", "var(--ink)")
    assert_last_property(".knowledge-collection", "background", "var(--surface)")
    assert_last_property(".knowledge-collection", "color", "var(--ink)")
    assert_last_property(
        ".knowledge-shelf", "background", "var(--surface-elevated)"
    )


def test_connected_ima_library_consumes_semantic_surfaces_and_text() -> None:
    # The connected-library DOM maps shell/sidebar/main/inspector to these
    # actual IMA classes; keeping the concrete selectors here prevents a
    # legacy knowledge-library skin from masking regressions in this view.
    assert_last_property(
        ".ima-library-layout", "background", "var(--page-background)"
    )
    assert_last_property(".ima-library-appbar", "background", "var(--surface)")
    assert_last_property(
        ".ima-library-sources", "background", "var(--surface-elevated)"
    )
    assert_last_property(".ima-library-main", "background", "var(--surface)")
    assert_last_property(
        ".ima-library-preview", "background", "var(--surface-elevated)"
    )
    assert_last_property(".ima-library-appbar-context", "color", "var(--ink)")
    assert_last_property(
        ".ima-library-appbar-search-field",
        "background",
        "var(--control-background)",
    )
    assert_last_property(
        ".ima-library-appbar-search-field input", "color", "var(--ink)"
    )
    assert_last_property(
        ".ima-library-appbar-search-field:focus-within", "border-color", "var(--accent)"
    )
    assert_last_property(".ima-source-entry", "color", "var(--ink)")
    assert_last_property(
        ".ima-library-sources > header", "color", "var(--muted)"
    )
    assert_last_property(
        ".ima-source-entry:hover",
        "background",
        "var(--control-background-hover)",
    )
    assert_last_property(
        ".ima-source-entry.is-active", "background", "var(--accent-surface)"
    )
    assert_last_property(".ima-file-row", "color", "var(--ink)")
    assert_last_property(
        ".ima-file-row:hover", "background", "var(--control-background-hover)"
    )
    assert_last_property(
        ".ima-file-row.is-active",
        "background",
        "var(--control-background-hover)",
    )
    assert_last_property(".ima-preview-card", "background", "var(--surface)")
    assert_last_property(".ima-preview-card p", "color", "var(--muted)")
    assert_last_property(
        ".ima-library-suggestions button",
        "background",
        "var(--control-background)",
    )


def test_library_dialog_uses_a_thin_border_and_soft_focus_in_both_themes() -> None:
    assert_last_property(".library-path-dialog", "border", "1px solid var(--rule-strong)")
    assert_last_property(".library-path-dialog", "box-shadow", "none")
    assert_last_property(".library-path-dialog textarea", "border", "1px solid var(--rule-strong)")
    assert_last_property(".library-path-dialog textarea", "background", "var(--control-background)")
    assert_last_property(".library-path-dialog textarea:focus", "border-color", "var(--accent)")
    assert_last_property(".library-path-dialog textarea:focus", "outline", "0")
    assert_last_property("#libraryPathSubmit", "box-shadow", "none")


def test_interactive_focus_and_selection_have_no_thick_outer_rings() -> None:
    assert "--focus-glow: 0 0 9px color-mix(in srgb, var(--accent) 14%, transparent)" in STYLES
    for selectors, body in RULES:
        if not any(
            token in selector
            for selector in selectors
            for token in (":focus", ".is-selected", ":has(input:checked)")
        ):
            continue
        assert not re.search(r"(?:box-shadow\s*:[^;]*0\s+0\s+0\s+[2-9]px|outline\s*:\s*[2-9]px)", body), selectors


def test_extensions_market_consumes_semantic_surfaces_and_text() -> None:
    assert_last_property(
        ".extensions-view", "background", "var(--page-background)"
    )
    assert_last_property(".extensions-header", "background", "var(--surface)")
    assert_last_property(".extensions-header h1", "color", "var(--ink)")
    assert_last_property(".extension-tabs", "background", "var(--surface-elevated)")
    assert_last_property(".extension-record", "background", "var(--surface)")
    assert_last_property(".extension-record h3", "color", "var(--ink)")
    assert_last_property(".extension-record p", "color", "var(--muted)")
    assert_last_property(
        ".extension-detail-card", "background", "var(--surface-elevated)"
    )
    assert_last_property(
        ".extension-form", "background", "var(--surface-elevated)"
    )
    assert_last_property(
        ".extension-form input", "background", "var(--control-background)"
    )
    assert_last_property(
        ".extension-tab small", "background", "var(--control-background)"
    )
    assert_last_property(
        ".extension-record-title span",
        "background",
        "var(--control-background-hover)",
    )
    assert_last_property(
        ".extension-record code",
        "background",
        "var(--control-background-hover)",
    )
    assert_last_property(
        ".extension-empty", "background", "var(--surface-elevated)"
    )
