"""Explicit real-provider smoke test. May incur charges; never run in the default suite."""
import argparse
import json
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image
from scansci_html import image_generation
from scansci_html.research_agent import ResearchAgentRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="GLM-CogView3-Flash")
    parser.add_argument("--prompt", default="生成一张哈利波特的证件照")
    parser.add_argument("--task-id", default="", help="WanX: query an existing task only; never submit generation")
    args = parser.parse_args()
    if args.task_id and (args.model != "WanX2.1-T2I-Plus" or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", args.task_id)):
        parser.error("--task-id requires a canonical UUID and --model WanX2.1-T2I-Plus")
    workspace = ROOT / "workspace.sqlite"
    selection = {"provider_id": "paratera", "model_id": args.model}
    route = image_generation.select_image_generation_route(workspace, image_model=selection)
    prompt = args.prompt
    report = {"provider": selection, "status": "running", "events": [], "image_requests": 0}
    output = ROOT / ".scansci-diagnostics" / f"paratera-image-real-{uuid4().hex}"
    output.mkdir(parents=True)
    print(f"Report: {output / 'report.json'}", flush=True)
    original_post = image_generation.requests.post
    original_task_info = image_generation._wanx_task_info

    def task_info(*args, **kwargs):
        info = original_task_info(*args, **kwargs)
        report["provider_task"] = info
        return info

    def post(*args, **kwargs):
        report["image_requests"] += 1
        response = original_post(*args, **kwargs)
        report["http_status"] = response.status_code
        return response

    image_generation.requests.post = post
    image_generation._wanx_task_info = task_info
    started = time.monotonic()
    try:
        if args.task_id:
            response = image_generation.requests.get(
                route["base_url"].rstrip("/") + "/p003/tasks/" + args.task_id,
                headers={"Authorization": "Bearer " + route["api_key"]},
                timeout=(10, 30), stream=True, allow_redirects=False,
            )
            try:
                if response.status_code != 200:
                    raise image_generation._image_service_error(response, api_key=route["api_key"])
                payload = image_generation._bounded_json_response(response, max_bytes=65536)
                task_info(payload.get("output", {}), args.task_id, api_key=route["api_key"])
                report.update(status="queried", task_queries=1, http_status=response.status_code)
                return 0
            finally:
                response.close()
        runtime = ResearchAgentRuntime(workspace=workspace, evidence_db=ROOT / "html-papers" / "evidence.sqlite")
        payload = {"image_model": selection, "conversation_id": output.name,
                   "image_request_id": uuid4().hex, "messages": [{"role": "user", "content": prompt}]}
        for event in runtime.chat_stream(payload):
            report["events"].append(event["type"])
            if event["type"] == "RUN_ERROR":
                report.update(status="failed", error=event.get("message"))
            if event["type"] == "RUN_FINISHED":
                artifact = event["result"]["message"]["artifacts"][0]
                path = Path(artifact["file_path"])
                with Image.open(path) as img:
                    img.load()
                    report.update(status="passed", image_path=str(path), dimensions=list(img.size), format=img.format)
                report["asset_url"] = artifact["asset_url"]
        assert report["status"] != "running", "No terminal event"
    finally:
        image_generation.requests.post = original_post
        image_generation._wanx_task_info = original_task_info
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
