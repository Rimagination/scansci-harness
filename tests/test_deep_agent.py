from pathlib import Path

import pytest

from scansci_html.agent_reasoning import evidence_budget_for_thinking, managed_glm_thinking_mode, native_reasoning_options, normalize_thinking_level
from scansci_html.app_settings import save_settings
from scansci_html.deep_agent import DeepAgentsConfigurationError, ScanSciDeepAgent, build_deep_agent_model
from scansci_html.evidence_store import index_evidence_library
from scansci_html.research_agent import ResearchAgentRuntime
import scansci_html.research_agent as research_agent_module


def _indexed_evidence(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.html").write_text(
        """
        <article class="paper" data-doi="10.1234/deep-agent">
          <h1>Deep Agent Paper</h1>
          <h2>Results</h2>
          <p id="results-p1">A verified model explained cortical activity in language regions.</p>
        </article>
        """,
        encoding="utf-8",
    )
    db_path = tmp_path / "evidence.sqlite"
    index_evidence_library(library, db_path=db_path, min_sentence_length=10)
    return db_path


def test_deep_agent_finalizes_with_existing_citation_verifier(tmp_path: Path):
    evidence_db = _indexed_evidence(tmp_path)
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def invoke(self, payload, *, config):
            captured["payload"] = payload
            captured["config"] = config
            finalize = next(tool for tool in self.tools if tool.__name__ == "build_verified_answer")
            finalize(payload["messages"][0]["content"])
            return {"messages": [{"content": "Used local evidence and finalized the cited answer."}]}

    def factory(**kwargs):
        captured["model"] = kwargs["model"]
        captured["system_prompt"] = kwargs["system_prompt"]
        return FakeAgent(kwargs["tools"])

    result = ScanSciDeepAgent(evidence_db=evidence_db, model="test-model", agent_factory=factory).answer(
        "What evidence links models to cortical activity?",
        thread_id="thread-1",
    )

    assert captured["model"] == "test-model"
    assert captured["config"] == {
        "configurable": {"thread_id": "thread-1"},
        "recursion_limit": 12,
    }
    assert result["deep_agent"]["harness"] == "deepagents"
    assert result["deep_agent"]["finalization"] == "tool"
    assert result["deep_agent"]["tool_calls"][0]["name"] == "build_verified_answer"
    assert result["citation_verification"]["passed"] is True
    assert result["reader_answer"]["citation_count"] == 1


def test_deep_agent_refuses_to_deliver_uncited_model_text(tmp_path: Path):
    evidence_db = _indexed_evidence(tmp_path)

    class FakeAgent:
        def invoke(self, payload, *, config):
            return {"messages": [{"content": "An uncited free-form model response."}]}

    result = ScanSciDeepAgent(evidence_db=evidence_db, model="test-model", agent_factory=lambda **kwargs: FakeAgent()).answer(
        "What evidence links models to cortical activity?"
    )

    assert result["deep_agent"]["finalization"] == "safety_fallback"
    assert result["deep_agent"]["model_output"] == "An uncited free-form model response."
    assert result["citation_verification"]["passed"] is True


def test_deep_agent_can_deliver_a_non_evidence_research_artifact_without_a_library(tmp_path: Path):
    evidence_db = tmp_path / "missing-evidence.sqlite"
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def invoke(self, payload, *, config):
            captured["tool_names"] = [tool.__name__ for tool in self.tools]
            inspect = next(tool for tool in self.tools if tool.__name__ == "inspect_available_tools")
            inspect()
            deliver = next(tool for tool in self.tools if tool.__name__ == "deliver_research_result")
            deliver(
                "tool_status",
                "Paper Atlas and Journal Scout are available for the next research step.",
                ["Search for candidate papers."],
            )
            return {"messages": [{"content": "I checked the available research tools."}]}

    result = ScanSciDeepAgent(
        evidence_db=evidence_db,
        workspace=tmp_path / "workspace.sqlite",
        model="test-model",
        agent_factory=lambda **kwargs: FakeAgent(kwargs["tools"]),
    ).answer("What research tools can I use?")

    assert "inspect_workspace" in captured["tool_names"]
    assert "build_presentation_outline" in captured["tool_names"]
    assert "deliver_research_result" in captured["tool_names"]
    assert result["deep_agent"]["finalization"] == "research_delivery"
    assert result["citation_verification"] == {
        "passed": True,
        "required": False,
        "status": "not_required",
        "reason": "This is a non-evidence research artefact, not a scientific conclusion.",
    }
    assert result["agent_delivery"]["delivery_type"] == "tool_status"
    assert result["reader_answer"]["citation_count"] == 0


def test_deep_agent_evidence_mode_keeps_unverified_deliveries_out_of_the_toolset(tmp_path: Path):
    evidence_db = _indexed_evidence(tmp_path)
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def invoke(self, payload, *, config):
            captured["tool_names"] = [tool.__name__ for tool in self.tools]
            finalize = next(tool for tool in self.tools if tool.__name__ == "build_verified_answer")
            finalize(payload["messages"][0]["content"])
            return {"messages": [{"content": "Verified answer delivered."}]}

    result = ScanSciDeepAgent(
        evidence_db=evidence_db,
        model="test-model",
        agent_factory=lambda **kwargs: FakeAgent(kwargs["tools"]),
    ).answer("What evidence links models to cortical activity?", task_mode="evidence")

    assert "deliver_research_result" not in captured["tool_names"]
    assert result["deep_agent"]["task_mode"] == "evidence"
    assert result["citation_verification"]["passed"] is True


def test_deep_agent_model_rejects_unknown_provider_without_optional_imports():
    with pytest.raises(DeepAgentsConfigurationError, match="Unsupported"):
        build_deep_agent_model(
            provider_kind="unsupported",
            base_url="https://example.invalid",
            api_key="key",
            model="model",
        )


def test_deep_agent_rejects_oversized_question_before_agent_creation(tmp_path: Path):
    called = False

    def factory(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("agent factory must not run")

    agent = ScanSciDeepAgent(
        evidence_db=tmp_path / "missing.sqlite",
        model="test-model",
        agent_factory=factory,
    )

    with pytest.raises(ValueError, match="provider-input budget"):
        agent.answer("研" * 48_001)

    assert called is False


def test_deep_agent_caps_tool_calls_and_large_payloads(tmp_path: Path):
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def invoke(self, payload, *, config):
            inspect = next(tool for tool in self.tools if tool.__name__ == "inspect_available_tools")
            first = inspect()
            captured["payload_bytes"] = len(str(first).encode("utf-8"))
            for _ in range(7):
                inspect()
            with pytest.raises(RuntimeError, match="tool-call budget exhausted"):
                inspect()
            return {"messages": [{"content": "Budget enforced."}]}

    result = ScanSciDeepAgent(
        evidence_db=tmp_path / "missing.sqlite",
        workspace=None,
        model="test-model",
        agent_factory=lambda **kwargs: FakeAgent(kwargs["tools"]),
    ).answer("Inspect tools", task_mode="benchmark")

    assert int(captured["payload_bytes"]) < 16_000
    assert result["deep_agent"]["finalization"] == "model_after_tool"


def test_deep_agent_model_normalizes_the_public_anthropic_v1_base_url():
    model = build_deep_agent_model(
        provider_kind="anthropic-compatible",
        base_url="https://api.anthropic.com/v1",
        api_key="test-key",
        model="claude-test",
    )

    assert model.model_dump()["anthropic_api_url"] == "https://api.anthropic.com"


def test_thinking_level_uses_agent_budget_and_direct_provider_api_options():
    assert normalize_thinking_level("HIGH") == "high"
    assert normalize_thinking_level("unsupported") == "auto"
    assert evidence_budget_for_thinking("low") < evidence_budget_for_thinking("high")
    assert native_reasoning_options(
        provider_id="openai", provider_kind="openai-compatible", thinking_level="high"
    ) == {"reasoning": {"effort": "high"}, "use_responses_api": True}
    assert native_reasoning_options(
        provider_id="anthropic", provider_kind="anthropic-compatible", thinking_level="medium"
    ) == {
        "thinking": {"type": "adaptive", "display": "omitted"},
        "output_config": {"effort": "medium"},
    }
    assert native_reasoning_options(
        provider_id="custom-gateway", provider_kind="openai-compatible", thinking_level="high"
    ) == {}


def test_managed_glm_direct_chat_turns_off_thinking_for_lightweight_auto_messages():
    greeting = [{"role": "user", "content": "hello"}]
    research_question = [{"role": "user", "content": "Analyse the research limitations and propose an experimental plan."}]

    assert managed_glm_thinking_mode(thinking_level="auto", messages=greeting) == "disabled"
    assert managed_glm_thinking_mode(thinking_level="auto", messages=research_question) == "enabled"
    assert managed_glm_thinking_mode(thinking_level="low", messages=research_question) == "disabled"
    assert managed_glm_thinking_mode(thinking_level="high", messages=greeting) == "enabled"


def test_deep_agent_model_maps_direct_openai_and_anthropic_thinking_options():
    openai = build_deep_agent_model(
        provider_id="openai",
        provider_kind="openai-compatible",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-5.2",
        thinking_level="high",
    )
    anthropic = build_deep_agent_model(
        provider_id="anthropic",
        provider_kind="anthropic-compatible",
        base_url="https://api.anthropic.com/v1",
        api_key="test-key",
        model="claude-sonnet-4-6",
        thinking_level="medium",
    )

    assert openai.model_dump()["reasoning"] == {"effort": "high"}
    assert openai.model_dump()["use_responses_api"] is True
    assert anthropic.model_dump()["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert anthropic.model_dump()["output_config"] == {"effort": "medium"}


def test_research_runtime_uses_deep_agents_for_configured_provider(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace.sqlite"
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"placeholder")
    save_settings(
        workspace,
        {
            "active_model": {"provider_id": "provider", "model_id": "research-model"},
            "providers": [
                {
                    "id": "provider",
                    "name": "Provider",
                    "kind": "openai-compatible",
                    "base_url": "https://example.invalid/v1",
                    "models": [{"id": "research-model", "name": "Research model"}],
                }
            ],
        },
    )
    monkeypatch.setattr(research_agent_module, "get_provider_api_key", lambda *_args: "secret")
    def build_model(**kwargs):
        calls["factory"] = kwargs
        return {"model": kwargs["model"]}

    monkeypatch.setattr(research_agent_module, "build_deep_agent_model", build_model)
    calls: dict[str, object] = {}

    class FakeDeepAgent:
        def __init__(self, *, evidence_db, workspace, model):
            calls["evidence_db"] = evidence_db
            calls["workspace"] = workspace
            calls["model"] = model

        def answer(self, question, *, limit, thread_id, task_mode):
            calls["answer"] = {
                "question": question,
                "limit": limit,
                "thread_id": thread_id,
                "task_mode": task_mode,
            }
            return {
                "reader_answer": {
                    "text": "A grounded answer [1].",
                    "citations": [{"doc_id": "paper", "html_anchor": "results-p1"}],
                },
                "citation_verification": {"passed": True},
            }

    monkeypatch.setattr(research_agent_module, "ScanSciDeepAgent", FakeDeepAgent)

    result = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db).answer_sync(
        {
            "question": "What changed?",
            "limit": 6,
            "thread_id": "run-7",
            "thinking_level": "high",
            "agent_harness": "deep",
        }
    )

    assert calls["model"] == {"model": "research-model"}
    assert calls["factory"]["provider_id"] == "provider"
    assert calls["factory"]["thinking_level"] == "high"
    assert calls["workspace"] == workspace.resolve()
    assert calls["answer"] == {
        "question": "What changed?",
        "limit": 6,
        "thread_id": "run-7",
        "task_mode": "auto",
    }
    assert result["reader_answer"]["citations"][0]["reader_url"] == "/api/sources/paper/reader#results-p1"
    assert result["agent_runtime"] == {"thinking_level": "high", "evidence_budget": 6}


def test_evidence_answer_keeps_product_owned_retrieval_but_routes_generation_through_pi(tmp_path: Path, monkeypatch):
    """Host retrieval stays fixed while its model-mediated JSON pass uses Pi."""

    workspace = tmp_path / "workspace.sqlite"
    evidence_db = _indexed_evidence(tmp_path)
    save_settings(
        workspace,
        {
            "active_model": {"provider_id": "provider", "model_id": "research-model"},
            "providers": [
                {
                    "id": "provider",
                    "name": "Provider",
                    "kind": "openai-compatible",
                    "base_url": "https://example.invalid/v1",
                    "models": [{"id": "research-model", "name": "Research model"}],
                }
            ],
        },
    )
    monkeypatch.setattr(research_agent_module, "get_provider_api_key", lambda *_args: "secret")

    observed: dict[str, object] = {}

    def fake_pi_stream(self, **kwargs):
        del self
        observed["pi_request"] = dict(kwargs)
        yield {"type": "delta", "content": '{"answer":"Grounded answer"}'}
        yield {"type": "done", "stats": {}}

    def fake_answer_question(_db, question, **kwargs):
        observed["question"] = question
        observed["options"] = kwargs
        observed["generated"] = kwargs["chat_client"].complete_json(
            [{"role": "user", "content": "Use only the verified evidence."}],
            schema_name="evidence_workflow_fixture",
        )
        return {"reader_answer": {"text": "Grounded answer [1].", "citations": []}}

    monkeypatch.setattr(research_agent_module.PiAgentClient, "stream_chat", fake_pi_stream)
    monkeypatch.setattr(
        research_agent_module,
        "build_chat_json_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("evidence JSON generation must not bypass Pi")
        ),
    )
    monkeypatch.setattr(research_agent_module, "answer_question", fake_answer_question)

    result = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db).answer_sync(
        {"question": "What did the field study report?", "task_mode": "evidence", "retrieval_quality": "balanced"}
    )

    assert observed["question"] == "What did the field study report?"
    assert observed["generated"] == {"answer": "Grounded answer"}
    assert observed["pi_request"]["session_id"] is None
    assert observed["pi_request"]["task_contract"]["allowed_tools"] == []
    assert observed["pi_request"]["model_runtime"]["context_window_tokens"] == 32 * 1024
    assert observed["pi_request"]["model_runtime"]["degraded"] is True
    assert result["pi_agent"]["harness"] == "pi-fixed-workflow"


def test_research_runtime_uses_pi_with_the_selected_vision_model_for_image_inputs(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace.sqlite"
    evidence_db = tmp_path / "evidence.sqlite"
    evidence_db.write_bytes(b"placeholder")
    save_settings(
        workspace,
        {
            "active_model": {"provider_id": "provider", "model_id": "vision-model"},
            "providers": [{"id": "provider", "name": "Provider", "kind": "openai-compatible", "base_url": "https://example.invalid/v1", "models": [{"id": "vision-model", "name": "Vision model", "capabilities": ["vision"]}]}],
        },
    )
    monkeypatch.setattr(research_agent_module, "get_provider_api_key", lambda *_args: "secret")
    monkeypatch.setattr(research_agent_module, "vision_image_blocks", lambda *_args: [{"mime_type": "image/png", "data": "aGVsbG8="}])
    monkeypatch.setattr(research_agent_module, "analyze_vision_images", lambda *_args, **kwargs: f"视觉：{kwargs['question']}")
    monkeypatch.setattr(research_agent_module, "answer_question", lambda *_args, **_kwargs: {"reader_answer": {"citations": []}})
    expected_analysis = research_agent_module.analyze_vision_images
    monkeypatch.setattr(
        research_agent_module,
        "analyze_vision_images",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("evidence image analysis must not bypass Pi")
        ),
    )
    monkeypatch.setattr(
        research_agent_module,
        "pi_image_blocks",
        lambda *_args: [{"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}],
    )
    observed_pi: dict[str, object] = {}

    def fake_pi_stream(self, **kwargs):
        del self
        observed_pi.update(kwargs)
        assert kwargs["images"] == [{"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}]
        assert kwargs["model_runtime"]["input_modalities"] == ["text", "image"]
        assert kwargs["task_contract"]["allowed_tools"] == []
        yield {
            "type": "delta",
            "content": expected_analysis(question=str(kwargs["messages"][-1]["content"])),
        }
        yield {"type": "done", "stats": {}}

    monkeypatch.setattr(research_agent_module.PiAgentClient, "stream_chat", fake_pi_stream)

    result = ResearchAgentRuntime(workspace=workspace, evidence_db=evidence_db).answer_sync(
        {"question": "解释这张图", "images": [{"id": "image-0123456789abcdef0123456789abcdef", "mime_type": "image/png"}]}
    )

    assert result["image_analysis"] == {"text": "视觉：解释这张图", "image_count": 1, "model": "vision-model"}
