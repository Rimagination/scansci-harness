from __future__ import annotations

import base64
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from scansci_html import image_generation, pi_agent
from scansci_html.pi_agent import PiAgentClient


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZnwAAAABJRU5ErkJggg=="
)


class _ImageResponse:
    def __init__(self, payload=None, *, status=200, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = body if body is not None else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


@pytest.fixture
def wanx_transport(tmp_path, monkeypatch):
    route = {"provider_id": "paratera", "provider_name": "Paratera",
             "model_id": "WanX2.1-T2I-Plus", "base_url": "https://example.org/v1/",
             "api_key": "test-secret"}
    state = {
        "route": route, "workspace": tmp_path / "workspace.sqlite", "calls": [], "responses": [],
        "post": _ImageResponse({"output": {"task_id": "task-123"}}),
        "polls": [_ImageResponse({"output": {"task_status": status}}) for status in ("PENDING", "RUNNING")]
        + [_ImageResponse({"output": {"task_status": "SUCCEEDED", "results": [{"url": "https://93.184.216.34/image.png"}]}})],
        "clock": 0.0, "cancel": threading.Event(),
    }
    def respond(response):
        if isinstance(response, Exception):
            raise response
        state["responses"].append(response)
        return response
    def post(url, **kwargs):
        state["calls"].append(("POST", url, kwargs))
        if state.get("cancel_after_submit"):
            state["cancel"].set()
        return respond(state["post"])
    def get(url, **kwargs):
        state["calls"].append(("GET", url, kwargs))
        if "/p003/tasks/" in url:
            return respond(state["polls"].pop(0))
        return respond(_ImageResponse(body=_ONE_PIXEL_PNG))
    def sleep(seconds):
        state["clock"] += seconds
    monkeypatch.setattr(image_generation, "time", SimpleNamespace(monotonic=lambda: state["clock"], sleep=sleep))
    monkeypatch.setattr(image_generation, "select_image_generation_route", lambda *_args, **_kwargs: route)
    monkeypatch.setattr(image_generation.requests, "post", post)
    monkeypatch.setattr(image_generation.requests, "get", get)
    return state


@pytest.mark.parametrize("cancel_after_submit", [False, True])
def test_wanx_protocol_download_save_and_receipt_replay(wanx_transport, cancel_after_submit):
    state = wanx_transport
    state["cancel_after_submit"] = cancel_after_submit
    kwargs = {"workspace": state["workspace"], "request_id": "one-paid-turn"}
    result = image_generation.generate_image_artifact(
        {"prompt": "private-prompt"}, cancel_event=state["cancel"], **kwargs)
    replay = image_generation.generate_image_artifact({"prompt": "private-prompt"}, **kwargs)
    assert replay == result and Path(result["file_path"]).read_bytes() == _ONE_PIXEL_PNG
    assert result["model_id"] == "WanX2.1-T2I-Plus" and result["size"] == "1024x1024"
    calls = state["calls"]
    assert [call[0] for call in calls] == ["POST", "GET", "GET", "GET", "GET"]
    assert calls[0][1] == "https://example.org/v1/p003/text2image"
    assert calls[0][2]["json"] == {"model": "WanX2.1-T2I-Plus", "input": {"prompt": "private-prompt"},
                                    "parameters": {"size": "1024*1024", "n": 1}}
    for _, url, options in calls[:-1]:
        assert options["headers"]["Authorization"] == "Bearer test-secret"
        assert options["stream"] and not options["allow_redirects"]
    assert all(call[1] == "https://example.org/v1/p003/tasks/task-123" for call in calls[1:-1])
    assert "Authorization" not in calls[-1][2]["headers"]
    assert all(response.closed for response in state["responses"])
    assert "test-secret" not in json.dumps(result)


@pytest.mark.parametrize("task_id", [None, "", "../escape", "a/b", "a?key=test-secret", "a%2Fb", "a" * 129, 123])
def test_wanx_task_id_is_one_safe_path_segment(wanx_transport, task_id):
    state = wanx_transport
    state["post"] = _ImageResponse({"output": {"task_id": task_id}})
    with pytest.raises(RuntimeError, match="无效任务 ID"):
        image_generation.generate_image_artifact({"prompt": "p"}, workspace=state["workspace"])
    assert len(state["calls"]) == 1 and state["post"].closed


@pytest.mark.parametrize("code,reason", [("DataInspectionFailed", "内容审核"), ("IPInfringementSuspect", "知识产权"), ("InvalidParameter", "生成参数"), ("InternalError", "内部执行")])
def test_wanx_failure_reason_and_task_are_persisted_without_private_prose(wanx_transport, code, reason):
    state = wanx_transport
    task_id = "598792a3-bfbd-430f-bb4a-8c77131a7ded"
    state["post"] = _ImageResponse({"output": {"task_id": task_id}})
    state["polls"] = [_ImageResponse({"output": {"task_status": "FAILED", "code": code, "message": "test-secret private-prompt"}})]
    kwargs = {"workspace": state["workspace"], "request_id": "failure-diagnostic"}
    with pytest.raises(RuntimeError, match=reason) as error:
        image_generation.generate_image_artifact({"prompt": "private-prompt"}, **kwargs)
    assert code in str(error.value) and task_id in str(error.value)
    record = next((state["workspace"].parent / "artifacts/images/.requests").glob("*.json"))
    saved = record.read_text(encoding="utf-8")
    assert "test-secret" not in saved and "private-prompt" not in saved
    assert json.loads(saved)["provider_task"]["error_code"] == code
    with pytest.raises(RuntimeError, match=reason):
        image_generation.generate_image_artifact({"prompt": "private-prompt"}, **kwargs)
    assert len(state["calls"]) == 2


def test_wanx_task_saved_before_query_connection_failure(wanx_transport):
    state = wanx_transport
    task_id = "598792a3-bfbd-430f-bb4a-8c77131a7ded"
    state["post"] = _ImageResponse({"output": {"task_id": task_id}})
    state["polls"] = [image_generation.requests.ConnectionError("private-prompt")]
    with pytest.raises(RuntimeError, match="结果尚未确定"):
        image_generation.generate_image_artifact({"prompt": "p"}, workspace=state["workspace"], request_id="query-failed")
    record = next((state["workspace"].parent / "artifacts/images/.requests").glob("*.json"))
    assert json.loads(record.read_text())["provider_task"] == {"status": "PENDING", "task_id": task_id}


def test_wanx_checkpoint_io_failure_does_not_abandon_paid_generation(wanx_transport, monkeypatch):
    state = wanx_transport
    original_write = image_generation._write_image_receipt
    writes = []
    def flaky_write(path, payload):
        writes.append(payload["status"])
        if len(writes) == 1:
            raise OSError("temporary disk error")
        original_write(path, payload)
    monkeypatch.setattr(image_generation, "_write_image_receipt", flaky_write)
    kwargs = {"workspace": state["workspace"], "request_id": "checkpoint-error"}
    result = image_generation.generate_image_artifact({"prompt": "p"}, **kwargs)
    assert Path(result["file_path"]).is_file()
    assert image_generation.generate_image_artifact({"prompt": "p"}, **kwargs) == result
    assert sum(call[0] == "POST" for call in state["calls"]) == 1


def test_wanx_nested_result_failure_explains_content_policy(wanx_transport):
    state = wanx_transport
    state["polls"] = [_ImageResponse({"output": {"task_status": "SUCCEEDED", "results": [{"code": "DataInspectionFailed", "message": "private-prompt"}]}})]
    with pytest.raises(RuntimeError, match="内容审核"):
        image_generation.generate_image_artifact({"prompt": "p"}, workspace=state["workspace"])
    assert len(state["calls"]) == 2


@pytest.mark.parametrize("task_id,code", [("test-secret", "private-prompt"), ("private-prompt", "DataInspectionFailed"), ("598792a3-bfbd-430f-bb4a-8c77131a7ded", "test-secret")])
def test_wanx_diagnostics_do_not_echo_unknown_fields(task_id, code):
    info = image_generation._wanx_task_info({"task_status": "FAILED", "code": code, "message": "private-prompt"}, task_id, api_key="test-secret")
    assert "private-prompt" not in json.dumps(info) and "test-secret" not in json.dumps(info)


@pytest.mark.parametrize("output", [
    {"task_status": "FAILED", "message": "test-secret private-prompt"},
    {"task_status": "CANCELED"}, {"task_status": "UNKNOWN"},
    {"task_status": "test-secret private-prompt"}, {"task_status": []}, {},
    {"task_status": "SUCCEEDED", "results": []},
    {"task_status": "SUCCEEDED", "results": [{"url": "https://127.0.0.1/secret"}]},
])
def test_wanx_bad_status_or_result_never_reposts(wanx_transport, output):
    state = wanx_transport
    state["polls"] = [_ImageResponse({"output": output})]
    kwargs = {"workspace": state["workspace"], "request_id": "failed-turn"}
    with pytest.raises(RuntimeError) as error:
        image_generation.generate_image_artifact({"prompt": "private-prompt"}, **kwargs)
    assert "test-secret" not in str(error.value) and "private-prompt" not in str(error.value)
    with pytest.raises(RuntimeError, match="阻止重复"):
        image_generation.generate_image_artifact({"prompt": "private-prompt"}, **kwargs)
    assert len(state["calls"]) == 2 and all(r.closed for r in state["responses"])
    assert not list(state["workspace"].parent.rglob("*.png"))


@pytest.mark.parametrize("phase", ["post", "poll"])
@pytest.mark.parametrize("failure", ["401", "403", "200-error", "malformed", "oversize", "declared-oversize", "connection"])
def test_wanx_failed_response_is_safe_closed_and_not_reposted(wanx_transport, phase, failure):
    state = wanx_transport
    response = _ImageResponse({"message": "test-secret private-prompt"}, status=int(failure) if failure.isdecimal() else 200)
    if failure == "200-error":
        response = _ImageResponse({"code": "private-prompt", "message": "test-secret"})
    elif failure == "malformed":
        response = _ImageResponse(body=b"test-secret private-prompt")
    elif failure == "oversize":
        response = _ImageResponse(body=b"x" * (65536 + 1))
    elif failure == "declared-oversize":
        response = _ImageResponse(headers={"Content-Length": "65537"})
    elif failure == "connection":
        response = image_generation.requests.ConnectionError("test-secret private-prompt")
    if phase == "post":
        state["post"] = response
    else:
        state["polls"] = [response]
    kwargs = {"workspace": state["workspace"], "request_id": "uncertain-turn"}
    with pytest.raises(RuntimeError) as error:
        image_generation.generate_image_artifact({"prompt": "p"}, **kwargs)
    assert "test-secret" not in str(error.value) and "private-prompt" not in str(error.value)
    if failure == "401":
        assert "密钥未通过验证" in str(error.value)
    if failure == "403":
        assert "服务商拒绝访问" in str(error.value)
    with pytest.raises(RuntimeError, match="阻止重复"):
        image_generation.generate_image_artifact({"prompt": "p"}, **kwargs)
    assert sum(call[0] == "POST" for call in state["calls"]) == 1
    assert all(r.closed for r in state["responses"])


def test_wanx_polling_deadline_does_not_resubmit(wanx_transport):
    state = wanx_transport
    with pytest.raises(RuntimeError, match="查询超时"):
        image_generation.generate_image_artifact({"prompt": "p"}, workspace=state["workspace"], timeout=5, request_id="timeout")
    assert state["clock"] == 5 and len(state["calls"]) == 3
    with pytest.raises(RuntimeError, match="阻止重复"):
        image_generation.generate_image_artifact({"prompt": "p"}, workspace=state["workspace"], request_id="timeout")
    assert len(state["calls"]) == 3


def test_wanx_unsupported_size_and_pre_cancel_never_submit(wanx_transport):
    state = wanx_transport
    with pytest.raises(ValueError, match="仅支持"):
        image_generation.generate_image_artifact({"prompt": "p", "size": "768x1344"}, workspace=state["workspace"])
    state["cancel"].set()
    with pytest.raises(RuntimeError, match="未向服务商提交"):
        image_generation.generate_image_artifact({"prompt": "p"}, workspace=state["workspace"], cancel_event=state["cancel"])
    assert not state["calls"]


@pytest.mark.parametrize("provider,model", [("other", "WanX2.1-T2I-Plus"), ("paratera", "GLM-CogView3-Flash")])
def test_wanx_transport_requires_exact_provider_and_model(wanx_transport, provider, model):
    state = wanx_transport
    state["route"].update(provider_id=provider, model_id=model)
    state["post"] = _ImageResponse({"data": [{"b64_json": base64.b64encode(_ONE_PIXEL_PNG).decode()}]})
    result = image_generation.generate_image_artifact({"prompt": "p"}, workspace=state["workspace"])
    assert len(state["calls"]) == 1 and state["calls"][0][1].endswith("/images/generations")
    assert Path(result["file_path"]).read_bytes() == _ONE_PIXEL_PNG


@pytest.mark.parametrize("status,code,expected", [
    (401, "", "密钥未通过验证"),
    (403, "", "服务商拒绝访问"),
    (403, "permission_denied", "没有调用权限"),
    (403, "content_policy_violation", "内容策略"),
    (429, "insufficient_quota", "配额不足"),
    (402, "insufficient_balance", "余额不足"),
    (404, "model_not_found", "模型不存在"),
    (429, "rate_limit_exceeded", "触发限流"),
    (400, "", "请求参数"),
    (502, "", "暂时无法完成"),
    (200, "content_policy_violation", "内容策略"),
])
def test_image_service_error_preserves_safe_reason(tmp_path, monkeypatch, status, code, expected):
    calls = []
    class Response:
        status_code = status
        headers = {"x-request-id": "request-12345678"}
        closed = False
        def iter_content(self, chunk_size):
            yield json.dumps({"error": {"code": code, "message": "Bearer test-secret private-prompt"}}).encode()
        def close(self):
            self.closed = True
    response = Response()
    monkeypatch.setattr(image_generation, "select_image_generation_route", lambda *_args: {
        "base_url": "https://example.org/v1", "model_id": "image-model", "api_key": "test-secret",
    })
    def post(*args, **kwargs):
        calls.append(1)
        return response
    monkeypatch.setattr(image_generation.requests, "post", post)
    with pytest.raises(RuntimeError) as error:
        image_generation.generate_image_artifact({"prompt": "private-prompt"}, workspace=tmp_path / "workspace.sqlite")
    message = str(error.value)
    assert expected in message and f"HTTP {status}" in message
    assert "request-12345678" not in message
    assert "test-secret" not in message and "private-prompt" not in message
    assert response.closed and len(calls) == 1
    assert not list(tmp_path.rglob("*.png"))


@pytest.mark.parametrize("request_id", ["test-secret", "private-prompt"])
@pytest.mark.parametrize("body", [b'<html>private-prompt test-secret</html>', b'x' * 17000,
    b'{"error":{"code":"test-secret","message":"private-prompt"}}', b'[]'])
def test_image_service_error_handles_untrusted_bodies(body, request_id):
    class Response:
        status_code = 403
        headers = {"x-request-id": request_id}
        def iter_content(self, chunk_size):
            yield body
    error = image_generation._image_service_error(Response(), api_key="test-secret")
    assert "HTTP 403" in str(error) and "服务商拒绝访问" in str(error)
    assert "test-secret" not in str(error) and "private-prompt" not in str(error)


def test_paratera_route_uses_the_explicit_image_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace.sqlite"
    settings = {
        "model_roles": {"image": "provider:paratera:GLM-CogView3-Flash"},
        "providers": [{
            "id": "paratera",
            "name": "并行智算云（Paratera）",
            "kind": "openai-compatible",
            "base_url": "https://llmapi.paratera.com/v1",
            "enabled": True,
            "models": [{"id": "GLM-CogView3-Flash", "capabilities": ["image"]}],
        }],
    }
    monkeypatch.setattr(image_generation, "get_provider_api_key", lambda *_args: "test-secret")

    route = image_generation.select_image_generation_route(workspace, settings)

    assert route == {
        "provider_id": "paratera",
        "provider_name": "并行智算云（Paratera）",
        "base_url": "https://llmapi.paratera.com/v1",
        "api_key": "test-secret",
        "model_id": "GLM-CogView3-Flash",
    }


@pytest.mark.parametrize(
    "auth_mode,kind,secret,expected",
    [
        ("managed", "openai-compatible", "", "scansci-managed-gateway"),
        ("local", "openai-compatible", "", "scansci-local-runtime"),
        ("", "local", "", "scansci-local-runtime"),
        ("api_key", "openai-compatible", " paratera-key , spare-key ", "paratera-key"),
        ("api_key", "openai-compatible", "", None),
    ],
)
def test_explicit_image_route_reuses_provider_auth(tmp_path, monkeypatch, auth_mode, kind, secret, expected):
    key_reads = []
    def provider_key(_workspace, provider_id):
        key_reads.append(provider_id)
        return secret
    monkeypatch.setattr(image_generation, "get_provider_api_key", provider_key)
    settings = {"providers": [{
        "id": "selected", "auth_mode": auth_mode, "kind": kind, "enabled": True,
        "base_url": "https://example.org/v1",
        "models": [{"id": "image-model", "capabilities": ["image"]}],
    }]}
    selection = {"provider_id": "selected", "model_id": "image-model"}
    if expected is None:
        with pytest.raises(ValueError, match="认证要求"):
            image_generation.select_image_generation_route(tmp_path / "workspace.sqlite", settings, image_model=selection)
    else:
        route = image_generation.select_image_generation_route(tmp_path / "workspace.sqlite", settings, image_model=selection)
        assert route["api_key"] == expected
        assert route["model_id"] == "image-model"
    assert key_reads == ([] if auth_mode in {"managed", "local"} or kind == "local" else ["selected"])


@pytest.mark.parametrize("mode", ["explicit", "role", "auto"])
@pytest.mark.parametrize(
    "model_state,allowed",
    [
        ({"available": False}, False),
        ({"readiness": "preview"}, False),
        ({"readiness": "missing"}, False),
        ({"readiness": "Production"}, False),
        ({"available": True, "readiness": "production"}, True),
        ({"readiness": None}, True),
        ({"readiness": ""}, True),
    ],
)
def test_image_route_applies_shared_model_availability(tmp_path, monkeypatch, mode, model_state, allowed):
    monkeypatch.setattr(image_generation, "get_provider_api_key", lambda *_args: "test-key")
    settings = {
        "model_roles": {"image": "provider:selected:image-model" if mode == "role" else "auto"},
        "providers": [{
            "id": "selected", "enabled": True, "base_url": "https://example.org/v1",
            "models": [{"id": "image-model", "capabilities": ["image"], **model_state}],
        }],
    }
    kwargs = {"image_model": {"provider_id": "selected", "model_id": "image-model"}} if mode == "explicit" else {}
    if allowed:
        route = image_generation.select_image_generation_route(tmp_path / "workspace.sqlite", settings, **kwargs)
        assert route["model_id"] == "image-model"
    else:
        with pytest.raises(ValueError):
            image_generation.select_image_generation_route(tmp_path / "workspace.sqlite", settings, **kwargs)


def test_generate_image_posts_openai_compatible_payload_and_saves_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace.sqlite"
    captured: dict[str, object] = {}

    class Response:
        status_code = 200
        headers: dict[str, str] = {}
        closed = False

        def iter_content(self, chunk_size):
            del chunk_size
            yield json.dumps({
                "data": [{"b64_json": base64.b64encode(_ONE_PIXEL_PNG).decode("ascii")}],
            }).encode("utf-8")

        def close(self):
            self.closed = True

    response = Response()

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return response

    monkeypatch.setattr(
        image_generation,
        "select_image_generation_route",
        lambda _workspace: {
            "provider_id": "paratera",
            "provider_name": "并行智算云（Paratera）",
            "base_url": "https://llmapi.paratera.com/v1",
            "api_key": "test-secret",
            "model_id": "GLM-CogView3-Flash",
        },
    )
    monkeypatch.setattr(image_generation.requests, "post", post)

    result = image_generation.generate_image_artifact(
        {"prompt": "一张简洁的科研示意图", "size": "1024x1024", "output_name": "cogview-test"},
        workspace=workspace,
    )

    saved = Path(result["file_path"])
    assert captured["url"] == "https://llmapi.paratera.com/v1/images/generations"
    assert captured["json"] == {
        "model": "GLM-CogView3-Flash",
        "prompt": "一张简洁的科研示意图",
        "size": "1024x1024",
    }
    assert captured["headers"]["Authorization"] == "Bearer test-secret"
    assert response.closed is True
    assert saved.read_bytes() == _ONE_PIXEL_PNG
    assert saved.parent == tmp_path / "artifacts" / "images"
    assert result["asset_url"] == "/api/generated-images/cogview-test.png"
    assert "test-secret" not in json.dumps(result, ensure_ascii=False)


def test_generated_image_asset_rejects_path_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.sqlite"
    image_dir = tmp_path / "artifacts" / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "safe.png").write_bytes(_ONE_PIXEL_PNG)

    path, mime_type = image_generation.generated_image_asset(workspace, "safe.png")

    assert path == (image_dir / "safe.png").resolve()
    assert mime_type == "image/png"
    with pytest.raises(FileNotFoundError):
        image_generation.generated_image_asset(workspace, "../safe.png")
    with pytest.raises(RuntimeError, match="本地或保留网络"):
        image_generation._public_https_url("https://127.0.0.1/generated.png")


def test_pi_dispatches_generate_image_through_the_host_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def generate(arguments, *, workspace):
        observed.update({"arguments": arguments, "workspace": workspace})
        return {"ok": True, "artifact_type": "image", "asset_url": "/api/generated-images/result.png"}

    monkeypatch.setattr(pi_agent, "generate_image_artifact", generate)
    client = PiAgentClient(
        workspace=tmp_path / "workspace.sqlite",
        evidence_db=tmp_path / "evidence.sqlite",
    )
    try:
        result = client._execute_tool("generate_image", {"prompt": "科研插图"})
    finally:
        client.close()

    assert result["artifact_type"] == "image"
    assert observed["arguments"] == {"prompt": "科研插图"}
    assert observed["workspace"] == (tmp_path / "workspace.sqlite").resolve()


@pytest.mark.parametrize("entrypoint", ["chat", "chat_stream"])
def test_pi_image_result_reaches_the_chat_message(tmp_path, monkeypatch, entrypoint):
    from scansci_html import research_agent

    artifact = {
        "ok": True, "artifact_type": "image", "title": "科研插图",
        "asset_url": "/api/generated-images/result.png",
    }

    def events(*_args, **_kwargs):
        yield {"type": "tool.completed", "name": "generate_image", "result": artifact}
        yield {"type": "delta", "content": "图片已生成并保存。"}
        yield {"type": "done", "usage": {"total_tokens": 8}}

    monkeypatch.setattr(research_agent, "select_image_generation_route", lambda *_args: {})
    monkeypatch.setattr(research_agent.ResearchAgentRuntime, "_pi_model_events", events)
    runtime = research_agent.ResearchAgentRuntime(
        workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite",
    )
    payload = {"web_search": "off", "messages": [{"role": "user", "content": "生成一张科研概念图"}]}
    if entrypoint == "chat":
        result = runtime.chat(payload)
    else:
        stream = list(runtime.chat_stream(payload))
        assert stream[-1]["type"] == "RUN_FINISHED", stream[-1]
        assert any(event.get("name") == "generated_image" for event in stream)
        result = stream[-1]["result"]
    assert result["message"]["artifacts"][0]["asset_url"] == artifact["asset_url"]


@pytest.fixture
def explicit_image(tmp_path, monkeypatch):
    from scansci_html import research_agent

    settings = {
        "active_model": {"provider_id": "unavailable-chat", "model_id": "offline"},
        "model_roles": {"image": "provider:other:do-not-use"},
        "providers": [{
            "id": "paratera", "name": "Paratera", "kind": "openai-compatible",
            "base_url": "https://example.org/v1", "enabled": True,
            "models": [{"id": "GLM-CogView3-Flash", "capabilities": ["image"], "enabled": True}],
        }],
    }
    monkeypatch.setattr(image_generation, "load_settings", lambda *_args: settings)
    monkeypatch.setattr(image_generation, "get_provider_api_key", lambda *_args: "fixture-key")
    calls = []

    class Response:
        status_code = 200
        headers = {}
        def iter_content(self, **_kwargs):
            yield json.dumps({"data": [{"b64_json": base64.b64encode(_ONE_PIXEL_PNG).decode()}]}).encode()
        def close(self):
            pass

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr(image_generation.requests, "post", post)
    runtime = research_agent.ResearchAgentRuntime(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    def no_chat(*_args, **_kwargs):
        raise AssertionError("Explicit image selection must not resolve or call a chat model")
    monkeypatch.setattr(runtime, "_direct_chat_request", no_chat)
    monkeypatch.setattr(runtime, "_pi_model_events", no_chat)
    payload = {
        "image_model": {"provider_id": "paratera", "model_id": "GLM-CogView3-Flash"},
        "messages": [{"role": "user", "content": "科研示意图", "message_id": "image-turn-1"}],
        "conversation_id": "image-conversation", "chat_mode": "general",
        "image_request_id": "image-queue-1",
    }
    return runtime, payload, calls, settings, post


@pytest.mark.parametrize("entrypoint", ["chat", "chat_stream"])
def test_explicit_image_uses_selected_model_once_and_keeps_history(explicit_image, entrypoint):
    from copy import deepcopy
    from scansci_html.direct_chat_history import DirectChatHistoryStore

    runtime, payload, calls, settings, _ = explicit_image
    original = deepcopy(settings)
    if entrypoint == "chat":
        result = runtime.chat(payload)
    else:
        events = list(runtime.chat_stream(payload))
        assert events[-1]["type"] == "RUN_FINISHED", events
        assert any(e.get("name") == "generated_image" for e in events)
        assert any(e["type"] == "TEXT_MESSAGE_CONTENT" for e in events)
        result = events[-1]["result"]
    replay = runtime.chat(payload)
    assert len(calls) == 1
    assert calls[0][1]["json"]["model"] == "GLM-CogView3-Flash"
    assert result["model"]["provider_id"] == "paratera"
    assert result["agent_runtime"]["harness"] == "host-explicit-image"
    assert result["agent_runtime"]["tool_calls"] == []
    assert replay["message"]["artifacts"] == result["message"]["artifacts"]
    assert settings == original
    history = DirectChatHistoryStore(runtime.workspace)
    history.upsert({"conversation_id": "images", "messages": [payload["messages"][-1], result["message"]]})
    saved = history.get("images")["messages"][-1]
    assert saved["artifacts"] == result["message"]["artifacts"]
    assert saved["model"] == result["model"]


def test_explicit_image_resume_id_survives_changed_message_metadata(explicit_image):
    runtime, payload, calls, _, _ = explicit_image
    first = runtime.chat(payload)
    payload["messages"][-1]["message_id"] = "restored-message-id"
    second = runtime.chat(payload)
    assert first["message"]["artifacts"] == second["message"]["artifacts"]
    assert len(calls) == 1
    payload["messages"][-1]["content"] = "different prompt"
    with pytest.raises(RuntimeError, match="不同内容"):
        runtime.chat(payload)
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", ["provider", "model", "disabled_provider", "disabled_model", "capability", "key", "selection", "images", "audio", "source_files", "content_blocks"])
def test_explicit_image_rejects_invalid_selection_and_attachments(explicit_image, monkeypatch, invalid):
    runtime, payload, calls, settings, _ = explicit_image
    provider = settings["providers"][0]
    if invalid == "provider":
        payload["image_model"]["provider_id"] = "missing"
    elif invalid == "model":
        payload["image_model"]["model_id"] = "missing"
    elif invalid == "disabled_provider":
        provider["enabled"] = False
    elif invalid == "disabled_model":
        provider["models"][0]["enabled"] = False
    elif invalid == "capability":
        provider["models"][0]["capabilities"] = ["text"]
    elif invalid == "key":
        monkeypatch.setattr(image_generation, "get_provider_api_key", lambda *_args: "")
    elif invalid == "selection":
        payload["image_model"] = None
    elif invalid == "content_blocks":
        payload["messages"][-1]["content"] = [{"type": "image_url", "image_url": "ignored"}]
    else:
        payload[invalid] = [{"id": "attachment"}]
    events = list(runtime.chat_stream(payload))
    assert events[-1]["type"] == "RUN_ERROR"
    assert not events[-1]["failure"]["retryable"]
    assert not calls


def test_explicit_image_uncertain_paid_failure_is_not_replayed(explicit_image, monkeypatch):
    runtime, payload, calls, _, _ = explicit_image
    def failed(*args, **kwargs):
        calls.append((args, kwargs))
        raise image_generation.requests.ConnectionError("connection reset")
    monkeypatch.setattr(image_generation.requests, "post", failed)
    with pytest.raises(RuntimeError, match="连接失败"):
        runtime.chat(payload)
    with pytest.raises(RuntimeError, match="阻止重复"):
        runtime.chat(payload)
    assert len(calls) == 1


@pytest.mark.parametrize("control", ["cancel_chat", "pause_chat"])
def test_explicit_image_cancel_before_dispatch_does_not_charge(explicit_image, control):
    runtime, payload, calls, _, _ = explicit_image
    stream = runtime.chat_stream(payload)
    started = next(stream)
    assert getattr(runtime, control)({"run_id": started["runId"]})
    events = list(stream)
    assert events[-1]["code"] == "image_cancelled"
    assert not calls


@pytest.mark.parametrize("control", ["cancel_chat", "pause_chat"])
def test_explicit_image_cancel_during_paid_call_saves_result_without_replay(explicit_image, monkeypatch, control):
    runtime, payload, calls, _, post = explicit_image
    submitted, release, saved = threading.Event(), threading.Event(), threading.Event()
    events = []
    def slow_post(*args, **kwargs):
        submitted.set()
        assert release.wait(5)
        return post(*args, **kwargs)
    original = runtime._explicit_image_result
    def generate(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            saved.set()
    monkeypatch.setattr(image_generation.requests, "post", slow_post)
    monkeypatch.setattr(runtime, "_explicit_image_result", generate)
    reader = threading.Thread(target=lambda: events.extend(runtime.chat_stream(payload)))
    reader.start()
    try:
        assert submitted.wait(3)
        assert getattr(runtime, control)()
        reader.join(2)
        assert not reader.is_alive()
        assert events[-1]["code"] == "image_cancelled"
    finally:
        release.set()
        reader.join(3)
    assert saved.wait(3)
    result = runtime.chat(payload)
    assert len(calls) == 1
    assert Path(result["message"]["artifacts"][0]["file_path"]).is_file()


def test_explicit_image_task_followup_keeps_original_run_and_artifact(explicit_image):
    from scansci_html.research_runs import StageSpec

    runtime, payload, calls, _, _ = explicit_image
    run = runtime.store.create_run(notebook_id="", workflow_type="ask", title="Existing task", input_payload={}, stages=[StageSpec("deliver", "Deliver", "delivery")])
    original = runtime.store.create_artifact(run["run_id"], artifact_type="evidence_answer", title="Original", summary="Original", payload={"answer": "Original"})
    runtime.store.complete_run(run["run_id"], output_artifact_id=original["artifact_id"])
    runtime.store.append_message(run["run_id"], role="assistant", content="Original conversation")
    result = runtime.continue_run_conversation(run["run_id"], {"content": "科研示意图", "image_model": payload["image_model"], "idempotency_key": "task-image-1"})
    saved = runtime.store.get_run(run["run_id"])
    assert len(calls) == 1
    assert saved["status"] == "completed"
    assert saved["output_artifact_id"] == original["artifact_id"]
    assert saved["messages"][0]["content"] == "Original conversation"
    assert saved["messages"][-1]["usage"]["image_model"] == result["model"]
    assert "/api/generated-images/" in saved["messages"][-1]["content"]
    image = next(a for a in saved["artifacts"] if a["artifact_type"] == "image")
    assert image["payload"]["message_id"] == saved["messages"][-1]["message_id"]
