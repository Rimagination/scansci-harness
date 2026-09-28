"""Opt-in one-image WanX API check. Uses saved credentials; may incur charges."""
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote
from uuid import uuid4

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from scansci_html.app_settings import get_provider_api_key, load_settings
from scansci_html.image_generation import _bounded_json_response, _download_image_url, _image_format


def main():
    workspace = ROOT / "workspace.sqlite"
    provider = next(p for p in load_settings(workspace)["providers"] if p["id"] == "paratera")
    key = next(k.strip() for k in get_provider_api_key(workspace, "paratera").split(",") if k.strip())
    base = provider["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    output = ROOT / ".scansci-diagnostics" / f"paratera-wanx-real-{uuid4().hex}"
    output.mkdir(parents=True)
    report = {"model": "WanX2.1-T2I-Plus", "status": "running", "generation_requests": 1, "polls": 0}
    started = time.monotonic()
    print(f"Report: {output / 'report.json'}", flush=True)

    def read(response):
        report["http_status"] = response.status_code
        try:
            data = _bounded_json_response(response, max_bytes=65536)
        finally:
            response.close()
        if response.status_code >= 400:
            error = data.get("error", data) if isinstance(data, dict) else {}
            if isinstance(error, dict):
                # Do not reflect upstream prose, request headers or signed URLs.
                code = str(error.get("code", ""))
                report["error_code"] = code if code.isdecimal() or code in {"InvalidApiKey", "AccessDenied", "Arrearage"} else "unrecognized"
                message = str(error.get("message", ""))
                report["upstream_authentication_error"] = "AuthenticationError" in message or "InvalidApiKey" in message
            raise RuntimeError(f"WanX API returned HTTP {response.status_code}")
        return data

    try:
        data = read(requests.post(base + "/p003/text2image", headers=headers,
            json={"model": report["model"], "input": {"prompt": "一只红苹果放在白色桌面上，柔和自然光，写实摄影"},
                  "parameters": {"size": "1024*1024", "n": 1}}, timeout=(15, 60), stream=True, allow_redirects=False))
        task = data.get("output", {})
        task_id = task.get("task_id")
        if not task_id:
            raise RuntimeError("WanX did not return a task ID")
        report["task_id"] = task_id
        while time.monotonic() - started < 180:
            report["polls"] += 1
            data = read(requests.get(base + "/p003/tasks/" + quote(task_id, safe=""), headers=headers,
                                     timeout=(15, 30), stream=True, allow_redirects=False))
            task = data.get("output", {})
            status = task.get("task_status")
            if report.get("task_status") != status:
                report["task_status"] = status
                print(f"Task status: {status}", flush=True)
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                raise RuntimeError(f"WanX task {status}")
            if status == "SUCCEEDED":
                content = _download_image_url(task["results"][0]["url"], timeout=60)
                suffix, _ = _image_format(content)
                image_path = output / f"wanx-test{suffix}"
                image_path.write_bytes(content)
                with Image.open(image_path) as image:
                    image.load()
                    report.update(status="passed", image_path=str(image_path), dimensions=list(image.size), format=image.format)
                break
            time.sleep(4)
        else:
            raise RuntimeError("WanX task still pending; no new generation submitted")
    except Exception as error:
        report.update(status="failed", error=str(error).replace(key, "[redacted]")[:300])
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
