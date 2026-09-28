"""Verify explicit image selection and submission without a paid provider call."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading

from playwright.sync_api import sync_playwright
from verify_scansci_frontend import ResearchAgentRuntime, _launch_chromium, create_notebook_server, load_settings, save_settings


def verify():
    received = []
    original = ResearchAgentRuntime.chat_stream
    def fail_once(_runtime, payload):
        received.append(payload)
        yield {"type": "RUN_ERROR", "code": "chat_failed", "message": "生图请求验证：模拟服务不可用"}
    ResearchAgentRuntime.chat_stream = fail_once
    try:
        with TemporaryDirectory(prefix="scansci-image-picker-") as directory:
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
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        def expose(route):
                            response = route.fetch()
                            route.fulfill(response=response, body=response.text() + "\nwindow.pickerProbe = {state,renderModelSelectors,directTurnConfiguration,persistedDirectTurn,setActiveComposerModel};")
                        page.route("**/app.js*", expose)
                        page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                        original_model = page.evaluate("""() => {
                            const {state,renderModelSelectors} = window.pickerProbe;
                            state.composerImageModel = '';
                            state.settings.providers.push({id:'image-fixture',name:'并行智算云',kind:'openai-compatible',
                                enabled:true,api_key_configured:true,models:[
                                    {id:'GLM-CogView3-Flash',name:'GLM-CogView3-Flash',capabilities:['image']},
                                    {id:'embedding-fixture',name:'Embedding',capabilities:['embedding']}
                                ]});
                            state.settings.providers.push({id:'image-unconfigured',name:'待配置图像服务',kind:'openai-compatible',
                                enabled:true,api_key_configured:false,models:[{id:'image-unconfigured',capabilities:['image']}]});
                            renderModelSelectors();
                            return state.settings.active_model;
                        }""")
                        page.locator('#homeAskForm [data-action="toggle-composer-model"]').click()
                        option = page.locator('#homeModelMenu [data-model-value="image-fixture::GLM-CogView3-Flash"]')
                        assert option.is_visible() and '生图' in option.inner_text()
                        assert page.locator('#homeModelMenu [data-model-value="image-unconfigured::image-unconfigured"]').is_disabled()
                        assert page.locator('#homeModelMenu [data-model-value="image-fixture::embedding-fixture"]').count() == 0
                        output = Path(__file__).resolve().parents[1] / 'output' / 'playwright'
                        output.mkdir(parents=True, exist_ok=True)
                        for theme in ('light', 'dark'):
                            page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
                            page.wait_for_timeout(200)
                            option.scroll_into_view_if_needed()
                            option.locator('..').screenshot(path=str(output / f'image-model-picker-{theme}.png'))
                        option.click()
                        assert 'GLM-CogView3-Flash' in page.locator('#homeModelLabel').inner_text()
                        assert '生图' in page.locator('#homeModelLabel').inner_text()
                        assert page.locator('#homeAskForm [data-context-usage]').is_hidden()
                        assert page.locator('#homeModelMenu [data-web-search-picker]').count() == 0
                        assert page.locator('#homeModelMenu [data-composer-thinking]').count() == 0
                        assert page.evaluate('window.pickerProbe.state.settings.active_model') == original_model
                        snapshot = page.evaluate("""async () => {
                            const p = window.pickerProbe;
                            const saved = p.persistedDirectTurn(p.directTurnConfiguration({question:'一只猫',key:'home',directChatMode:'general'}));
                            const active = p.state.settings.active_model;
                            await p.setActiveComposerModel(`${active.provider_id}::${active.model_id}`);
                            if (p.state.composerImageModel) throw Error('Image mode did not clear');
                            await p.setActiveComposerModel('image-fixture::GLM-CogView3-Flash');
                            return saved.imageModel;
                        }""")
                        assert snapshot == {'provider_id':'image-fixture','model_id':'GLM-CogView3-Flash'}
                        page.locator('#homeQuestionInput').fill('一只猫，水彩风格')
                        page.locator('#homeAskForm button[type=submit]').click()
                        page.get_by_text('生图请求验证：模拟服务不可用', exact=False).wait_for()
                        page.locator('#chatAskForm button[data-composer-state="idle"]').wait_for()
                        assert len(received) == 1, 'Image request was automatically retried'
                        assert received[0]['image_model'] == snapshot
                        assert received[0]['image_request_id']
                        assert received[0]['messages'][-1]['content'] == '一只猫，水彩风格'
                        return {'picker':True,'themes':['light','dark'],'chat_model_preserved':True,'queue_snapshot':True,'requests':len(received)}
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
    finally:
        ResearchAgentRuntime.chat_stream = original


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False))
