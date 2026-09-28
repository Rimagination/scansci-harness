"""Focused light/dark advisor projection check using the existing browser harness."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading

from playwright.sync_api import sync_playwright
from verify_scansci_frontend import create_notebook_server, _launch_chromium, load_settings, save_settings


def verify():
    with TemporaryDirectory(prefix="scansci-recovery-ui-") as directory:
        root = Path(directory)
        workspace = root / "workspace.sqlite"
        settings = load_settings(workspace)
        settings["onboarding"]["welcome_dismissed"] = True
        save_settings(workspace, settings)
        server = create_notebook_server(workspace=workspace, evidence_db=root / "evidence.sqlite")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = _launch_chromium(playwright.chromium)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 850})
                    def expose(route):
                        response = route.fetch()
                        route.fulfill(response=response, body=response.text() + "\nwindow.recoveryProbe = {runControlPlaneMarkup};")
                    page.route("**/app.js*", expose)
                    page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                    page.evaluate("""() => {
                        const render = window.recoveryProbe.runControlPlaneMarkup;
                        const old = {verdict:'needs_review', recommended_next_action:'evidence_safe_follow_up', findings:[]};
                        const run = {run_id:'fixture', events:[{type:'advisor.reviewed',payload:old}],
                            advisor_report:{verdict:'passed',recommended_next_action:'none'}};
                        if (render(run).includes('is-advisor')) throw Error('Stale advisor shown');
                        run.advisor_report = old;
                        const html = render(run);
                        if (!html.includes('研究任务核查') || html.includes('Research advisor') || html.includes('needs_review')) throw Error('Untranslated advisor');
                        document.querySelector('#settingsContent').innerHTML = html;
                        document.querySelector('#workbench').classList.add('is-settings');
                        document.querySelectorAll('.app-view').forEach(v => v.classList.toggle('is-active',v.dataset.view === 'settings'));
                    }""")
                    colors = []
                    output = Path(__file__).resolve().parents[1] / "output" / "playwright"
                    output.mkdir(parents=True, exist_ok=True)
                    for theme in ("light", "dark"):
                        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                        page.wait_for_timeout(200)
                        card = page.locator('.run-control-panel.is-advisor')
                        assert card.is_visible()
                        colors.append(card.evaluate("e => ({background:getComputedStyle(e).backgroundColor,text:getComputedStyle(e.querySelector('strong')).color,button:getComputedStyle(e.querySelector('button')).backgroundColor})"))
                        card.screenshot(path=str(output / f"run-advisor-{theme}.png"))
                    assert colors[0] != colors[1], colors
                    return {"stale_report_hidden": True, "chinese_labels": True, "themes": colors}
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False))
