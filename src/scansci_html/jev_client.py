"""Small Host-owned transport for the TypeSafe JEV evaluation API.

JEV is a typed decision service, not an OpenAI-compatible chat endpoint.  The
client therefore keeps its ``/v1/systemone`` request shape separate from the
ordinary model transport and never includes response bodies in errors.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urlsplit


DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_RETRIES = 2


class JEVError(RuntimeError):
    """Safe, user-facing JEV failure without upstream response content."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _validate_endpoint(endpoint: str) -> str:
    value = str(endpoint or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise JEVError("JEV API 地址必须使用 HTTPS")
    return value


def _http_error_message(status_code: int) -> str:
    return {
        401: "JEV API Key 无效或已过期",
        403: "JEV API Key 没有访问权限",
        422: "JEV 请求参数不符合接口要求",
        429: "JEV 请求过于频繁，请稍后重试",
        529: "JEV 服务当前过载，请稍后重试",
    }.get(status_code, f"JEV API 返回 HTTP {status_code}")


def ask_jev(
    *,
    api_key: str,
    state: str | dict[str, Any] | list[Any],
    questions: dict[str, Any],
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    opener: Callable[..., Any] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Evaluate typed questions through TypeSafe's official JEV endpoint."""

    key = str(api_key or "").strip()
    if not key:
        raise JEVError("请先保存 JEV API Key")
    if not isinstance(questions, dict) or not questions:
        raise JEVError("JEV 请求至少需要一个判断问题")
    endpoint = _validate_endpoint(endpoint)
    try:
        body = json.dumps(
            {
                "state": state,
                "model": str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
                "questions": questions,
            },
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise JEVError("JEV 请求内容无法编码") from exc

    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "ScanSci-Desktop/0.1",
        },
        method="POST",
    )
    open_request = opener or request.urlopen
    sleep = sleeper or time.sleep
    attempts = max(1, min(3, int(retries) + 1))
    timeout = max(1.0, min(60.0, float(timeout)))

    for attempt in range(attempts):
        try:
            with open_request(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            status_code = int(exc.code)
            retryable = status_code == 429 or status_code == 529 or status_code >= 500
            if retryable and attempt + 1 < attempts:
                sleep(min(2.0, 0.5 * (2**attempt)))
                continue
            raise JEVError(
                _http_error_message(status_code),
                status_code=status_code,
                retryable=retryable,
            ) from exc
        except error.URLError as exc:
            raise JEVError("无法连接 JEV API") from exc
        except (OSError, TimeoutError) as exc:
            raise JEVError("JEV API 请求超时或网络不可用") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JEVError("JEV API 返回了无法解析的响应") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
            raise JEVError("JEV API 返回缺少结构化判断结果")
        return payload

    raise JEVError("JEV API 请求未完成")


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT_SECONDS",
    "JEVError",
    "ask_jev",
]
