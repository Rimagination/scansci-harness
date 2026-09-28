"""Two real managed-model turns in an isolated workspace; never edits user history."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scansci_html.app_settings import load_settings
from scansci_html.agent_contract import compile_task_contract
from scansci_html.model_metadata import descriptor_from_model_record
from scansci_html.pi_agent import PiAgentClient


def main() -> int:
    provider = next(p for p in load_settings(ROOT / "workspace.sqlite")["providers"] if p["id"] == "scansci-managed")
    model = next(m for m in provider["models"] if m["id"] == "glm-4.7-flash")
    root = ROOT / ".scansci-diagnostics"
    root.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="glm-long-task-", dir=root))
    client = PiAgentClient(workspace=output / "workspace.sqlite", evidence_db=output / "evidence.sqlite")
    descriptor = descriptor_from_model_record(provider_id=provider["id"], provider_kind=provider["kind"], model_id=model["id"], model_record=model)
    report = {"model": model["id"], "protocol": 7, "sdk": "0.84.3", "status": "running", "turns": [], "fallback_count": 0}
    prompts = [
        "请实际检索2022年以来关于检索增强生成中的事实一致性评估的关键论文，优先方法论文与公开基准。筛选6至8篇，给出标题、年份、链接和相关性。请调用检索工具，以实际检索结果为依据。",
        "总结刚才的检索结果：按评估对象、评估方法、公开基准、局限性分组比较，引用检索中得到的论文链接。区分摘要支持的事实和需要全文核验的判断。不要重复检索。",
    ]
    messages = []
    print(f"Evidence directory: {output}", flush=True)
    try:
        for index, prompt in enumerate(prompts, 1):
            messages.append({"role": "user", "content": prompt})
            contract = compile_task_contract(task_mode="web", user_text=prompt, available_tool_ids=["discover_papers", "search_web"], required_tool_groups=[["discover_papers", "search_web"]] if index == 1 else [])
            contract.update(allowed_tools=["discover_papers", "search_web"], initial_tools=["discover_papers", "search_web"], initial_tool_budget=3, max_tool_budget=6)
            started = time.monotonic()
            turn = {"index": index, "events": [], "answer": "", "status": "running"}
            report["turns"].append(turn)
            for event in client.stream_chat(
                provider_kind=provider["kind"], provider_id=provider["id"], base_url=provider["base_url"],
                api_key="scansci-managed-gateway", model_id=model["id"], model_runtime=descriptor,
                messages=messages, task_mode="web", task_contract=contract, thinking_level="off",
                session_id="glm-long-task", timeout_seconds=900,
            ):
                kind = event.get("type")
                if kind == "delta":
                    turn["answer"] += str(event.get("content", ""))
                elif kind in {"tool.completed", "tool.failed", "status", "done", "session"}:
                    # Store only public status and tool identities; manifests retain full audit evidence.
                    row = {key: event[key] for key in ("type", "name", "status", "attempt", "delay_ms", "resumed") if key in event}
                    turn["events"].append(row)
                    print(f"Turn {index}: {json.dumps(row, ensure_ascii=False)}", flush=True)
                    if kind == "done":
                        turn["status"] = "completed"
            turn["seconds"] = round(time.monotonic() - started, 2)
            assert turn["status"] == "completed" and len(turn["answer"]) > 200, "Incomplete model delivery"
            if index == 1:
                assert any(e["type"] == "tool.completed" and e.get("name") in {"discover_papers", "search_web"} for e in turn["events"]), "No verified search tool completion"
            messages.append({"role": "assistant", "content": turn["answer"]})
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = {"type": type(error).__name__, "code": getattr(error, "failure", {}).get("code", "")}
        print(json.dumps(report["error"]), flush=True)
    finally:
        client.close()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Result: {report['status']}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
