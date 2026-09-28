import pytest

from scansci_html.research_agent import ResearchAgentRuntime, _required_pi_tool_groups


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
