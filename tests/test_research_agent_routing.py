import pytest

from scansci_html.agent_contract import compile_task_contract
from scansci_html.research_agent import ResearchAgentRuntime, _required_pi_tool_groups, _structured_recovery


def test_ollama_load_failure_keeps_its_specific_reason_in_chat_error() -> None:
    failure = _structured_recovery(RuntimeError("Ollama 加载 qwen3.5:4b 失败（HTTP 500）：模型目录不可用"))

    assert failure["code"] == "local_model_unavailable"
    assert "模型目录不可用" in failure["message"]
    assert failure["actions"][0]["id"] == "open_settings"


def test_selected_library_routes_evidence_request_to_local_knowledge() -> None:
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        "off",
        messages=[
            {
                "role": "user",
                "content": "基于当前选择的光伏生态文献，找出光伏电站与植物多样性的一条可靠证据。",
            }
        ],
        selected_knowledge=True,
    )

    assert "knowledge" in mode.split("+")
    assert "web" not in mode.split("+")


def test_selected_library_does_not_change_plain_greeting_route() -> None:
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        "off",
        messages=[{"role": "user", "content": "你好"}],
        selected_knowledge=True,
    )

    assert mode == "general"


@pytest.mark.parametrize("prompt", [
    "帮我生图：一张展示植物光合作用机制的科研插图",
    "Generate an image of a forest",
    "生成一张科研概念图",
])
def test_image_request_routes_to_the_provider_neutral_image_tool(prompt: str) -> None:
    mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        "off",
        messages=[{"role": "user", "content": prompt}],
    )

    assert mode == "image"
    assert _required_pi_tool_groups(mode, prompt) == [{"generate_image"}]


def test_explicit_agent_artifact_intent_uses_existing_reversible_lease() -> None:
    prompt = "生成一份 docx 文档，总结两种方法"
    routed = ResearchAgentRuntime._direct_pi_task_mode(
        "general", "off", messages=[{"role": "user", "content": prompt}],
    )
    mode = ResearchAgentRuntime._agent_task_mode(routed, requested=True)
    contract = compile_task_contract(task_mode=mode, user_text=prompt)

    assert mode == "slides+agent"
    assert contract["risk_level"] == "reversible"
    assert "create_document" in contract["allowed_tools"]
    assert contract["allow_external_write"] is False
