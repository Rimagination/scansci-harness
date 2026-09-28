"""Check the generated-image card in both themes using an isolated workspace.

Only the model stream is substituted; the app, server and history use real code.
No provider credentials or paid image generation are needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading

from verify_scansci_frontend import (
    ResearchAgentRuntime,
    _fake_chat_stream,
    _launch_chromium,
    create_notebook_server,
    load_settings,
    save_settings,
)
from playwright.sync_api import expect, sync_playwright
from PIL import Image


def verify() -> list[dict[str, str]]:
    original = ResearchAgentRuntime.chat_stream
    original_title = ResearchAgentRuntime.generate_conversation_title
    calls = []

    def image_stream(runtime, payload):
        calls.append(payload)
        artifact = {
            "artifact_type": "image", "asset_url": "/api/generated-images/probe.png",
            "title": "科研插图 · 界面验证", "provider_name": "并行智算云（Paratera）",
            "model_id": "GLM-CogView3-Flash", "size": "1024x1024",
        }
        yield {"type": "CUSTOM", "name": "generated_image", "value": artifact}
        if str(payload.get("messages", [{}])[-1].get("content", "")).startswith("fail:"):
            yield {"type": "RUN_ERROR", "message": "模拟图片生成后的模型断线", "code": "chat_failed"}
            return
        for event in _fake_chat_stream(runtime, payload):
            if event.get("type") == "RUN_FINISHED":
                event["result"]["message"]["artifacts"] = [artifact]
            yield event

    ResearchAgentRuntime.chat_stream = image_stream
    ResearchAgentRuntime.generate_conversation_title = lambda *_args: {
        "ok": True, "title": "图片界面验证", "source": "fixture", "fallback": False,
    }
    server = None
    try:
        with TemporaryDirectory(prefix="scansci-image-ui-") as directory:
            root = Path(directory)
            workspace = root / "workspace.sqlite"
            settings = load_settings(workspace)
            settings["onboarding"]["welcome_dismissed"] = True
            save_settings(workspace, settings)
            image_dir = root / "artifacts" / "images"
            image_dir.mkdir(parents=True)
            Image.new("RGB", (1024, 1024), (100, 145, 115)).save(image_dir / "probe.png")
            server = create_notebook_server(workspace=workspace, evidence_db=root / "evidence.sqlite")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            output = Path(__file__).resolve().parents[1] / "output" / "playwright"
            output.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as playwright:
                browser = _launch_chromium(playwright.chromium)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    def expose(route):
                        response = route.fetch()
                        route.fulfill(response=response, body=response.text() + "\nwindow.imageUiProbe = {state,renderModelSelectors};")
                    page.route("**/app.js*", expose)
                    page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                    skip = page.locator('[data-action="skip-resource-onboarding"]')
                    if skip.is_visible():
                        skip.click()
                    page.evaluate("""() => {
                        const {state,renderModelSelectors} = window.imageUiProbe;
                        state.composerImageModel = 'fixture::test-image';
                        state.settings.providers.push({id:'fixture',name:'测试生图',enabled:true,api_key_configured:true,
                            models:[{id:'test-image',name:'测试生图',capabilities:['image']}]});
                        renderModelSelectors();
                    }""")
                    expect(page.locator('#homeAskForm button[type=submit]')).to_have_text("生成")
                    expect(page.locator('#homeAskForm .composer-knowledge-button')).to_be_hidden()
                    page.evaluate("() => { imageUiProbe.state.composerImageModel = ''; imageUiProbe.renderModelSelectors(); }")
                    expect(page.locator('#homeAskForm .composer-knowledge-button')).to_be_visible()
                    page.evaluate("() => { imageUiProbe.state.composerImageModel = 'fixture::test-image'; imageUiProbe.renderModelSelectors(); }")
                    page.locator("#homeQuestionInput").fill("Generate an image of a forest")
                    page.locator("#homeAskForm button[type=submit]").click()
                    page.locator('#chatAskForm button[data-composer-state="idle"]').wait_for()
                    card = page.locator(".generated-image-artifact")
                    card.wait_for(state="visible")
                    page.wait_for_function("() => document.querySelector('.generated-image-artifact img')?.naturalWidth > 0")
                    assert card.locator("a[download]").get_attribute("href") == "/api/generated-images/probe.png"
                    assert abs(card.bounding_box()["width"] - card.locator("img").bounding_box()["width"]) <= 3
                    expect(card.locator(".generated-image-details small")).to_be_hidden()
                    card.locator(".generated-image-details summary").click()
                    expect(card.locator(".generated-image-details small")).to_contain_text("1024x1024")
                    card.locator(".generated-image-details summary").click()
                    card.locator('[data-action="open-image-preview"]').click()
                    expect(page.locator('#imagePreviewDialog')).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(page.locator('#imagePreviewDialog')).to_be_hidden()
                    card.locator('[data-action="reuse-image-prompt"]').click()
                    expect(page.locator('#chatQuestionInput')).to_have_value("Generate an image of a forest")
                    assert len(calls) == 1, "Reuse must not submit a paid request"
                    page.locator('#chatQuestionInput').fill("My existing draft")
                    card.locator('[data-action="reuse-image-prompt"]').click()
                    expect(page.locator('#chatQuestionInput')).to_have_value("My existing draft")
                    page.locator('#chatQuestionInput').fill("")
                    compact_height = page.locator('#chatQuestionInput').bounding_box()["height"]
                    page.locator('#chatQuestionInput').fill("long description\n" * 12)
                    assert page.locator('#chatQuestionInput').bounding_box()["height"] > compact_height
                    page.locator('#chatQuestionInput').fill("")
                    page.locator('#renameConversationButton').click()
                    page.locator('#conversationTitleInput').fill("图片生成验证")
                    page.locator('#conversationTitleInput').press("Enter")
                    expect(page.locator('#conversationTitle')).to_have_text("图片生成验证")
                    assert len(calls) == 1
                    results = []
                    for theme in ("light", "dark"):
                        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                        colors = page.evaluate("""() => {
                            const el = document.querySelector('.generated-image-artifact');
                            el.scrollIntoView({block: 'center'});
                            return {surface: getComputedStyle(el).backgroundColor,
                                text: getComputedStyle(el.querySelector('strong')).color};
                        }""")
                        assert colors["surface"] != colors["text"], colors
                        assert page.locator('#chatAskForm').evaluate("el => getComputedStyle(el).boxShadow") == "none"
                        screenshot = output / f"generated-image-{theme}.png"
                        page.screenshot(path=str(screenshot), clip=card.bounding_box())
                        results.append({"theme": theme, **colors, "screenshot": str(screenshot)})
                    assert results[0]["surface"] != results[1]["surface"]
                    page.locator("#chatQuestionInput").fill("fail: Generate an image")
                    page.locator("#chatAskForm button[type=submit]").click()
                    page.get_by_text("模拟图片生成后的模型断线", exact=False).wait_for()
                    page.locator('#chatAskForm button[data-composer-state="idle"]').wait_for()
                    expect(page.locator(".generated-image-artifact")).to_have_count(2, timeout=8000)
                    page.reload(wait_until="networkidle")
                    expect(page.locator(".generated-image-artifact")).to_have_count(2, timeout=8000)
                    expect(page.locator('#conversationTitle')).to_have_text("图片生成验证")
                    page.set_viewport_size({"width": 820, "height": 900})
                    last_card = page.locator(".generated-image-artifact").last
                    assert last_card.bounding_box()["x"] + last_card.bounding_box()["width"] <= 821
                    page.screenshot(path=str(output / "generated-image-narrow.png"))
                    return results
                finally:
                    browser.close()
    finally:
        ResearchAgentRuntime.chat_stream = original
        ResearchAgentRuntime.generate_conversation_title = original_title
        if server is not None:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
