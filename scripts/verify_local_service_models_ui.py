"""Verify ScanSci's local model grouping without changing user settings."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import threading

from playwright.sync_api import sync_playwright
from verify_scansci_frontend import create_notebook_server, _launch_chromium, load_settings, save_settings


def verify():
    with TemporaryDirectory(prefix="scansci-local-catalog-") as directory:
        root = Path(directory)
        workspace = root / "workspace.sqlite"
        settings = load_settings(workspace)
        settings["onboarding"]["welcome_dismissed"] = True
        save_settings(workspace, settings)
        server = create_notebook_server(workspace=workspace, evidence_db=root / "evidence.sqlite")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = _launch_chromium(playwright.chromium)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    def expose_catalog(route):
                        response = route.fetch()
                        route.fulfill(response=response, body=response.text() + "\nwindow.catalogProbe = {state, verifiedLocalServiceModels, renderModelsSettings, localServiceModelsMarkup};")
                    page.route("**/app.js*", expose_catalog)
                    page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                    result = page.evaluate("""() => {
                        const {state, verifiedLocalServiceModels, renderModelsSettings, localServiceModelsMarkup} = window.catalogProbe;
                        const managed = state.settings.providers.find(p => p.id === 'scansci-managed');
                        const original = JSON.stringify(managed);
                        state.settings.providers = [managed, {
                            id: 'local-runtime-test', name: 'Local runtime', auth_mode: 'local', enabled: true,
                            models: [{id: 'Qwen-local', name: 'Qwen 本地模型', capabilities: ['reasoning', 'tool', 'coding']}]
                        }];
                        state.settings.local_models = [{id: 'embed-role', model_id: 'BGE-embedding', capabilities: ['embedding'], runtime: 'local-huggingface'}];
                        state.localModelMarket.installed = [
                            {id: 'BGE-embedding', kind: 'embedding', ready: true},
                            {id: 'bad-model', ready: true, runtime_compatible: false},
                            {id: 'pending-model', ready: false}
                        ];
                        state.modelHealth = {models: {'local-runtime-test::Qwen-local': {status: 'ready'}}};
                        const models = verifiedLocalServiceModels();
                        if (models.length !== 2) throw Error('Invalid readiness filter or duplicate model');
                        state.selectedProviderId = managed.id;
                        state.providerQuery = ''; state.modelQuery = '';
                        const html = renderModelsSettings();
                        const host = document.createElement('div'); host.innerHTML = html;
                        const group = host.querySelector('[data-local-model-group]');
                        if (host.querySelector('[data-provider-id="local-runtime-test"]')) throw Error('Duplicate local provider in sidebar');
                        if (!group || group.querySelectorAll('.cherry-model-row').length !== 2) throw Error('Missing local group');
                        if (group.parentElement !== host.querySelector('.cherry-model-group').parentElement) throw Error('Group hierarchy differs');
                        if (group.querySelector('[data-action="toggle-model-capability"]')) throw Error('Local chip edits a cloud model');
                        if (!['推理','工具','代码','嵌入'].every(label => group.textContent.includes(label))) throw Error('Missing capability chips');
                        if (JSON.stringify(managed) !== original) throw Error('Managed provider was mutated');
                        if (localServiceModelsMarkup(models, 'BGE').includes('Qwen 本地模型')) throw Error('Model search failed');
                        state.settings.providers = [managed]; state.settings.local_models = []; state.localModelMarket.installed = [];
                        if (verifiedLocalServiceModels().length || renderModelsSettings().includes('data-local-model-group')) throw Error('Empty installation renders local models');
                        document.querySelector('#settingsContent').innerHTML = html;
                        document.querySelector('#workbench').classList.add('is-settings');
                        document.querySelectorAll('.app-view').forEach(v => v.classList.toggle('is-active', v.dataset.view === 'settings'));
                        return {models: models.map(m => m.id), managedUnchanged: true};
                    }""")
                    output = Path(__file__).resolve().parents[1] / "output" / "playwright"
                    output.mkdir(parents=True, exist_ok=True)
                    colors = []
                    for theme in ("light", "dark"):
                        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                        page.wait_for_timeout(200)  # Let existing theme color transitions finish.
                        colors.append(page.evaluate("""() => {
                            const row = document.querySelector('[data-local-model-group] .cherry-model-row');
                            return {text: getComputedStyle(row.querySelector('.cherry-model-copy button')).color,
                                chip: getComputedStyle(row.querySelector('.model-capability-chip')).backgroundColor};
                        }"""))
                        page.screenshot(path=str(output / f"local-service-models-{theme}.png"))
                    assert colors[0] != colors[1], colors
                    return {**result, "themes": colors}
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False))
