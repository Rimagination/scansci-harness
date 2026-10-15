from __future__ import annotations

from types import SimpleNamespace

import pytest

from scansci_html.agent_capabilities import builtin_capability_catalog
from scansci_html.agent_contract import compile_task_contract
from scansci_html.research_agent import (
    ResearchAgentRuntime,
    _pi_should_run,
    _should_route_through_pi,
)


_BILINGUAL_MODEL_TURNS = [
    ("general", "off", "解释一下光合作用为什么需要光。"),
    ("general", "off", "帮我把这段话写得更清楚。"),
    ("general", "off", "比较归纳法和演绎法。"),
    ("general", "off", "给我三个论文标题备选。"),
    ("general", "off", "把这个研究问题翻译成英文。"),
    ("general", "off", "分析这个论证有哪些薄弱点。"),
    ("general", "off", "写一段简短的项目说明。"),
    ("general", "off", "什么是贝叶斯先验？"),
    ("general", "off", "用通俗语言解释置信区间。"),
    ("general", "off", "继续完善刚才的提纲。"),
    ("general", "auto", "解释一下生态位。"),
    ("general", "auto", "帮我润色摘要。"),
    ("general", "auto", "这个结论是否自洽？"),
    ("general", "auto", "列出可能的混杂因素。"),
    ("general", "auto", "给出一个可检验的假设。"),
    ("general", "on", "查找今天的科技新闻。"),
    ("general", "on", "联网核验这个产品的当前规格。"),
    ("general", "off", "搜索近期的森林碳汇研究。"),
    ("knowledge", "off", "比较当前知识库里的两种方法。"),
    ("knowledge", "off", "总结已连接资料的主要发现。"),
    ("general", "off", "Explain why photosynthesis needs light."),
    ("general", "off", "Rewrite this paragraph for clarity."),
    ("general", "off", "Compare induction with deduction."),
    ("general", "off", "Suggest three possible paper titles."),
    ("general", "off", "Translate this research question into Chinese."),
    ("general", "off", "Analyze the weak points in this argument."),
    ("general", "off", "Draft a short project description."),
    ("general", "off", "What is a Bayesian prior?"),
    ("general", "off", "Explain confidence intervals in plain language."),
    ("general", "off", "Continue refining the previous outline."),
    ("general", "auto", "Explain ecological niches."),
    ("general", "auto", "Polish my abstract."),
    ("general", "auto", "Is this conclusion internally consistent?"),
    ("general", "auto", "List plausible confounders."),
    ("general", "auto", "Propose a testable hypothesis."),
    ("general", "on", "Find today's technology news."),
    ("general", "on", "Verify the current product specification online."),
    ("general", "off", "Search for recent forest carbon sink studies."),
    ("knowledge", "off", "Compare the methods in my linked library."),
    ("knowledge", "off", "Summarize the findings in the selected sources."),
    ("general", "off", "Create a concise methods checklist."),
    ("general", "off", "Evaluate whether the premise supports the claim."),
    ("general", "off", "请提出两个反例。"),
    ("general", "off", "把回答压缩成五句话。"),
]


def _ready_read_only_ids() -> set[str]:
    return {
        str(item["id"])
        for item in builtin_capability_catalog()
        if item.get("status") == "ready" and item.get("risk_level") == "read_only"
    }


@pytest.mark.parametrize("chat_mode,web_search,prompt", _BILINGUAL_MODEL_TURNS)
def test_bilingual_turns_use_pi_only_when_the_task_profile_requires_it(
    chat_mode: str,
    web_search: str,
    prompt: str,
) -> None:
    task_mode = ResearchAgentRuntime._direct_pi_task_mode(
        chat_mode,
        web_search,
        messages=[{"role": "user", "content": prompt}],
    )
    contract = compile_task_contract(task_mode=task_mode, user_text=prompt)

    expected_pi = contract["task_profile"]["route"] != "direct_chat"
    assert _pi_should_run(task_mode, prompt, contract["task_profile"]) is expected_pi
    assert _ready_read_only_ids() <= set(contract["allowed_tools"])
    assert set(contract["initial_tools"]) <= set(contract["allowed_tools"])


@pytest.mark.parametrize("prompt", ["你好", "您好！", "hello", "Hi!", "谢谢", "thank you"])
def test_social_turns_use_the_direct_chat_fast_lane(prompt: str) -> None:
    contract = compile_task_contract(task_mode="general", user_text=prompt)

    assert _pi_should_run("general", prompt, contract["task_profile"]) is False
    assert contract["allowed_tools"] == []
    assert contract["initial_tools"] == []


def test_context_bearing_turns_stay_on_pi_even_with_a_direct_task_profile() -> None:
    contract = compile_task_contract(task_mode="general", user_text="润色这段文字")
    profile = contract["task_profile"]

    assert profile["route"] == "direct_chat"
    assert _should_route_through_pi(
        "general",
        "润色这段文字",
        profile,
        selected_skills=[{"id": "nature-polishing", "status": "loaded"}],
    ) is True
    assert _should_route_through_pi(
        "general",
        "润色这段文字",
        profile,
        has_knowledge=True,
    ) is True
    assert _should_route_through_pi(
        "general",
        "润色这段文字",
        profile,
        has_attachments=True,
    ) is True


def test_auto_web_weather_question_requires_pi_search() -> None:
    prompt = "帮我看看今天北京的天气"
    task_mode = ResearchAgentRuntime._direct_pi_task_mode(
        "general",
        "auto",
        messages=[{"role": "user", "content": prompt}],
    )
    contract = compile_task_contract(task_mode=task_mode, user_text=prompt)

    assert task_mode == "web-auto"
    assert _pi_should_run(task_mode, prompt, contract["task_profile"]) is True


def test_regex_mode_only_hints_initial_tools_and_never_shrinks_read_authority() -> None:
    prompts = {
        "ordinary": "Explain the mechanism.",
        "web": "Search the web for current evidence.",
        "knowledge": "Compare papers in my knowledge base.",
        "task-documents": "Summarize the documents already registered by this task.",
    }
    envelopes = []
    for expected_hint, prompt in prompts.items():
        mode = ResearchAgentRuntime._direct_pi_task_mode(
            "general",
            "off",
            messages=[{"role": "user", "content": prompt}],
            run={"workflow_type": "paper_download"} if expected_hint == "task-documents" else None,
        )
        contract = compile_task_contract(task_mode=mode, user_text=prompt)
        envelopes.append(set(contract["allowed_tools"]) & _ready_read_only_ids())

    assert envelopes and all(envelope == _ready_read_only_ids() for envelope in envelopes)


def test_explicit_legacy_harness_cannot_bypass_pi_for_a_text_turn() -> None:
    request = SimpleNamespace(messages=[{"role": "user", "content": "Explain this."}])

    assert ResearchAgentRuntime._pi_eligible(request, {"agent_harness": "direct"}) is True


def test_general_turn_does_not_receive_reversible_authority_without_workflow_authorization() -> None:
    contract = compile_task_contract(task_mode="general", user_text="Explain how presentations are structured.")
    reversible = {
        str(item["id"])
        for item in builtin_capability_catalog()
        if item.get("risk_level") == "reversible"
    }

    assert reversible.isdisjoint(contract["allowed_tools"])


def test_explicit_agent_turn_uses_pi_discovery_with_read_only_authority() -> None:
    contract = compile_task_contract(task_mode="agent", user_text="比较两项研究并核查关键结论")

    assert contract["task_profile"]["route"] == "tool_agent"
    assert contract["task_profile"]["requires_tools"] is True
    assert contract["risk_level"] == "read_only"
    assert contract["initial_tools"] == []
    assert _ready_read_only_ids() <= set(contract["allowed_tools"])
    assert "create_document" not in contract["allowed_tools"]
    assert contract["max_tool_budget"] > compile_task_contract(task_mode="general", user_text="解释一个概念")["max_tool_budget"]


def test_explicit_agent_choice_is_honored_even_for_a_greeting() -> None:
    contract = compile_task_contract(task_mode="agent", user_text="你好")

    assert contract["task_profile"]["route"] == "tool_agent"
    assert contract["allowed_tools"]
    assert "greeting" not in contract["task_profile"]["reasons"]
