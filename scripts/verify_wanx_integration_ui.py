"""Opt-in real WanX generation from the UI; uses the workspace key and may incur charges.

Creates one test conversation in the real workspace. No model output is mocked.
Never run automatically in the default test suite.
"""

import argparse
import json
from pathlib import Path
import threading
import time
from uuid import uuid4
from urllib.parse import urljoin

from verify_scansci_frontend import _launch_chromium, create_notebook_server
from playwright.sync_api import expect, sync_playwright
from scansci_html import image_generation

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing", action="store_true", help="Only inspect the saved apple test; never submit generation")
    args = parser.parse_args()
    output = ROOT / ".scansci-diagnostics" / f"wanx-ui-{uuid4().hex}"
    output.mkdir(parents=True)
    report = {"status": "running", "generation_posts": 0, "task_queries": 0}
    print(f"Report: {output / 'report.json'}", flush=True)
    original_post, original_get = image_generation.requests.post, image_generation.requests.get

    def post(url, *args, **kwargs):
        if str(url).endswith("/p003/text2image"):
            report["generation_posts"] += 1
        return original_post(url, *args, **kwargs)

    def get(url, *args, **kwargs):
        if "/p003/tasks/" in str(url):
            report["task_queries"] += 1
        return original_get(url, *args, **kwargs)

    image_generation.requests.post, image_generation.requests.get = post, get
    server = None
    started = time.monotonic()
    try:
        server = create_notebook_server(workspace=ROOT / "workspace.sqlite",
                                        evidence_db=ROOT / "html-papers" / "evidence.sqlite")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as playwright:
            browser = _launch_chromium(playwright.chromium)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                skip = page.locator('[data-action="skip-resource-onboarding"]')
                if skip.is_visible():
                    skip.click()
                if args.existing:
                    page.locator('[data-action="open-direct-conversation"]').filter(has_text="生成一张白色桌面上红苹果").first.click()
                else:
                    page.locator('#homeAskForm [data-action="toggle-composer-model"]').click()
                    option = page.locator('#homeModelMenu [data-model-value="paratera::WanX2.1-T2I-Plus"]')
                    expect(option).to_be_enabled()
                    expect(option).to_contain_text("生图")
                    for theme in ("light", "dark"):
                        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                        option.screenshot(path=str(output / f"menu-{theme}.png"))
                    option.click()
                    page.locator("#homeQuestionInput").fill("生成一张白色桌面上红苹果的写实照片，柔和自然光")
                    page.locator("#homeAskForm button[type=submit]").click()
                card = page.locator(".generated-image-artifact").last
                card.wait_for(state="visible", timeout=240000)
                page.wait_for_function("() => [...document.querySelectorAll('.generated-image-artifact img')].some(img => img.naturalWidth === 1024)")
                page.locator('#chatAskForm button[data-composer-state="idle"]').wait_for()
                href = card.locator("a[download]").get_attribute("href")
                assert href and href.startswith("/api/generated-images/")
                response = page.request.get(urljoin(page.url, href))
                assert response.status == 200
                assert response.headers.get("content-type", "").startswith("image/")
                report.update(asset_url=href, history_url=page.url, dimensions=card.locator("img").evaluate("img => [img.naturalWidth, img.naturalHeight]"))
                assert report["dimensions"] == [1024, 1024]
                for theme in ("light", "dark"):
                    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                    card.screenshot(path=str(output / f"result-{theme}.png"))
                page.reload(wait_until="networkidle")
                expect(page.locator(f'.generated-image-artifact a[download][href="{href}"]')).to_be_visible()
                page.wait_for_function("() => [...document.querySelectorAll('.generated-image-artifact img')].some(img => img.naturalWidth === 1024)")
                assert report["generation_posts"] == (0 if args.existing else 1), report
                report.update(status="passed", history_retained=True)
            finally:
                browser.close()
    finally:
        image_generation.requests.post, image_generation.requests.get = original_post, original_get
        if server:
            server.shutdown()
            server.server_close()
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        if report["status"] == "running":
            report["status"] = "verification_failed"
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
