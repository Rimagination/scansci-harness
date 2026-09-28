"""Run repeatable end-to-end checks through ScanSci's own agent runtime.

The script uses an isolated workspace and never reads or prints provider keys.
It is intended for release verification, not unit testing.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import secrets
import sys
import time

from scansci_html.app_settings import load_settings, managed_probe_model_ids, save_settings
from scansci_html.research_agent import ResearchAgentRuntime


# Probe only production managed models. Standby routes remain visible for
# operator evaluation, but cannot make a release pass or fail until they have
# been explicitly promoted after passing this structured response contract.
MODELS = managed_probe_model_ids()


_NUMBERED_SECTION_PATTERN = re.compile(
    r"(?m)^\s*(?:#{1,4}\s*)?(?:第\s*)?(?:[一二三四五六]|[1-6])\s*(?:部分|[、.．:：])"
)
_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")


def _numbered_section_count(text: str) -> int:
    """Count the numbered sections required by the release verification prompt."""

    return len(_NUMBERED_SECTION_PATTERN.findall(text))


def _numbered_section_bullet_counts(text: str) -> list[int]:
    """Count executable checklist bullets inside each numbered top-level section.

    The release prompt asks for six numbered parts with three checks in each.
    This measures that explicit contract rather than applying a fragile global
    character threshold to otherwise valid concise Chinese writing.
    """

    headings = list(_NUMBERED_SECTION_PATTERN.finditer(text))
    counts: list[int] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section_body = text[heading.end():end]
        counts.append(
            len(re.findall(r"(?m)^\s*(?:[-*•]|[①②③④⑤⑥])\s+", section_body))
        )
    return counts


def _mostly_chinese(text: str) -> bool:
    chinese = len(_CJK_PATTERN.findall(text))
    latin = len(_LATIN_PATTERN.findall(text))
    return chinese >= 4 and chinese >= latin / 2


def _mostly_english(text: str) -> bool:
    english = len(_LATIN_PATTERN.findall(text))
    chinese = len(_CJK_PATTERN.findall(text))
    return english >= 12 and chinese <= max(2, english // 12)


def _run_stream(runtime: ResearchAgentRuntime, payload: dict[str, object]) -> dict[str, object]:
    started = time.monotonic()
    events = list(runtime.chat_stream(payload))
    terminal = events[-1] if events else {}
    if terminal.get("type") != "RUN_FINISHED":
        failure = dict(terminal.get("failure") or {})
        diagnostic = {
            "message": str(terminal.get("message") or "ScanSci did not emit RUN_FINISHED"),
            "code": str(terminal.get("code") or ""),
            "failure_stage": str(failure.get("stage_key") or ""),
            # The runtime already redacts provider credentials before it puts
            # detail on a terminal event.  Preserve that classification in
            # release reports: otherwise a real upstream failure is reduced
            # to the unhelpful generic "stage failed" message.
            "failure_reason": str(failure.get("reason") or failure.get("code") or ""),
            "failure_detail": str(failure.get("detail") or "")[:1000],
        }
        raise RuntimeError(json.dumps(diagnostic, ensure_ascii=False))
    result = dict(terminal.get("result") or {})
    message = dict(result.get("message") or {})
    return {
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "content": str(message.get("content", "")),
        "usage": dict(message.get("usage") or {}),
        "trace": list(message.get("trace") or []),
        "event_types": [str(event.get("type", "")) for event in events],
    }


def verify_model(runtime: ResearchAgentRuntime, workspace: Path, model_id: str) -> dict[str, object]:
    settings = load_settings(workspace)
    settings["active_model"] = {"provider_id": "scansci-managed", "model_id": model_id}
    save_settings(workspace, settings)

    memory_token = f"SCN-{secrets.token_hex(4).upper()}"
    first_user = {
        "role": "user",
        "content": f"你好，请记住校验码 {memory_token}，并用一句自然的中文确认你已经记住。",
    }
    greeting = _run_stream(runtime, {
        "chat_mode": "general",
        "messages": [first_user],
    })
    memory = _run_stream(runtime, {
        "chat_mode": "general",
        "messages": [
            first_user,
            {"role": "assistant", "content": str(greeting["content"])},
            {"role": "user", "content": "我刚才让你记住的校验码是什么？只输出完整校验码。"},
        ],
    })
    english = _run_stream(runtime, {
        "chat_mode": "general",
        "messages": [{
            "role": "user",
            "content": "Hello. Briefly describe one thing you can help a researcher do.",
        }],
    })
    identity = _run_stream(runtime, {
        "chat_mode": "general",
        "messages": [{"role": "user", "content": "你是谁？请说明当前底层模型、ScanSci 版本、可用模式和内置 Skill。"}],
    })
    long_answer = _run_stream(runtime, {
        "chat_mode": "writing",
        "messages": [{
            "role": "user",
                "content": (
                "请写一份结构完整的科研写作质量检查指南，共六个编号部分。"
                "每部分至少包含三条可执行检查；每条检查必须单独成行并以 '- ' 开头，"
                "不要省略结尾；最后一行必须单独写【回答完毕】。"
            ),
        }],
    })
    skill = _run_stream(runtime, {
        "chat_mode": "writing",
        "skills": ["good-question"],
        "messages": [{
            "role": "user",
            "content": (
                "$good-question 我有光伏场生态影响方面的本地文献，想研究植被与微气候响应。"
                "无需追问；请在信息不足处作出并标注合理假设，直接给出一张中文好问题卡，"
                "其中必须有‘核心研究问题’小节。"
            ),
        }],
    })
    identity_text = str(identity["content"])
    long_text = str(long_answer["content"])
    skill_text = str(skill["content"])
    greeting_text = str(greeting["content"])
    memory_text = str(memory["content"])
    english_text = str(english["content"])
    long_answer_sections = _numbered_section_count(long_text)
    long_answer_bullet_counts = _numbered_section_bullet_counts(long_text)
    checks = {
        "chinese_prompt_gets_chinese_answer": _mostly_chinese(greeting_text),
        "multi_turn_history_is_recalled": memory_token in memory_text,
        "english_prompt_gets_english_answer": _mostly_english(english_text),
        "identity_knows_scansci": "ScanSci" in identity_text,
        "identity_knows_model": model_id.casefold() in identity_text.casefold() or model_id.split("/")[-1].casefold() in identity_text.casefold(),
        "identity_knows_skills": "good-question" in identity_text,
        "long_answer_finished": long_text.rstrip().endswith("【回答完毕】"),
        "long_answer_has_six_sections": long_answer_sections >= 6,
        "long_answer_has_three_checks_per_section": (
            len(long_answer_bullet_counts) >= 6
            and all(count >= 3 for count in long_answer_bullet_counts[:6])
        ),
        "skill_followed": "核心研究问题" in skill_text and ("假设" in skill_text or "研究问题" in skill_text),
        # A plain writing answer deliberately has no artificial process card.
        # The identity request reads live runtime facts, so it is the correct
        # place to verify that meaningful execution traces reach the client.
        "trace_available": bool(identity["trace"]),
        # Structured answers are buffered until their required outline and end
        # marker pass validation, then emitted once as a clean final response.
        "delivered": long_answer["event_types"].count("TEXT_MESSAGE_CONTENT") >= 1,
    }
    return {
        "model_id": model_id,
        "passed": all(checks.values()),
        "checks": checks,
        "basic_chat": {
            "greeting": {
                **{key: value for key, value in greeting.items() if key != "content"},
                "characters": len(greeting_text),
            },
            "multi_turn": {
                **{key: value for key, value in memory.items() if key != "content"},
                "characters": len(memory_text),
            },
            "english": {
                **{key: value for key, value in english.items() if key != "content"},
                "characters": len(english_text),
            },
        },
        "identity": {key: value for key, value in identity.items() if key != "content"},
        "long_answer": {
            **{key: value for key, value in long_answer.items() if key != "content"},
            "characters": len(long_text),
            "numbered_sections": long_answer_sections,
            "checks_per_section": long_answer_bullet_counts,
            "ending": long_text[-80:],
        },
        "skill": {**{key: value for key, value in skill.items() if key != "content"}, "characters": len(skill_text), "head": skill_text[:120]},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".scansci-diagnostics/agent-e2e.json")
    parser.add_argument("--model", choices=MODELS, action="append", dest="models")
    args = parser.parse_args()
    root = Path(".codex-tmp") / f"agent-e2e-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    root.mkdir(parents=True)
    workspace = root / "workspace.sqlite"
    runtime = ResearchAgentRuntime(workspace=workspace, evidence_db=root / "evidence.sqlite")
    results = []
    for model_id in (tuple(args.models) if args.models else MODELS):
        try:
            results.append(verify_model(runtime, workspace, model_id))
        except Exception as error:  # release report needs both model results
            results.append({"model_id": model_id, "passed": False, "error": str(error)})
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "framework": "scansci_html.research_agent.ResearchAgentRuntime.chat_stream",
        "results": results,
        "passed": all(bool(result.get("passed")) for result in results),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
