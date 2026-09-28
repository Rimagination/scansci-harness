"""Thin Host-to-Pi boundary for capability-bearing model turns.

The harness validates the immutable turn envelope and forwards it to Pi.  Pi
owns planning, tool discovery, Skill loading, subagents, and session control;
the Host keeps authority over contracts, effects, persistence, and evidence.
Plain direct_chat turns may use the Host fast lane without entering this
boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .task_contract import TASK_CONTRACT_SCHEMA, TASK_CONTRACT_VERSION, TaskContract


HARNESS_SCHEMA_VERSION = "scansci.harness.v1"
_SUPPORTED_MESSAGE_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})
_CAPABILITIES = (
    "durable_session",
    "dynamic_tools",
    "skills",
    "subagents",
    "mcp",
    "external_capability_manifests",
    "multimodal",
    "branchable_history",
    "capability_profiles",
)


class HarnessContractError(ValueError):
    """Raised when a turn cannot cross the ScanSci harness boundary safely."""


def _is_loopback_endpoint(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        hostname = urlparse(raw).hostname
    except ValueError:
        return False
    if not hostname:
        return False
    normalized = hostname.rstrip(".").casefold()
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ip_address(normalized.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _is_local_model_route(driver_payload: Mapping[str, Any]) -> bool:
    model_runtime = driver_payload.get("model_runtime")
    runtime_descriptor = model_runtime if isinstance(model_runtime, Mapping) else {}
    provider_kind = str(driver_payload.get("provider_kind", "") or "").strip().casefold()
    descriptor_provider_kind = str(runtime_descriptor.get("provider_kind", "") or "").strip().casefold()
    if provider_kind == "local" or descriptor_provider_kind == "local":
        return True
    return _is_loopback_endpoint(driver_payload.get("base_url"))


def _model_advisories_for_turn(
    *,
    task_contract: Mapping[str, Any],
    driver_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Return host-owned, non-blocking quality notices for risky model routes."""

    profile = task_contract.get("task_profile")
    task_profile = profile if isinstance(profile, Mapping) else {}
    evidence_policy = str(task_profile.get("evidence_policy", "off") or "off").casefold()
    if evidence_policy == "off" or not _is_local_model_route(driver_payload):
        return ()
    model_id = str(driver_payload.get("model_id", "") or "").strip()[:300]
    notice: dict[str, Any] = {
        "code": "local-model-evidence-boundary",
        "level": "warning",
        "blocking": False,
        "source": "scansci-harness",
        "title": "本地模型提示",
        "message": (
            "当前使用本地模型处理检索、文献或证据任务。小型或蒸馏模型可能把未出现在检索结果、"
            "全文或工具结果中的细节补全为事实；任务会继续执行，请以已下载全文、DOI、原文引用和"
            "结构化工具结果为准。"
        ),
    }
    if model_id:
        notice["model_id"] = model_id
    return (notice,)


@dataclass(frozen=True)
class HarnessDescriptor:
    id: str = "scansci-harness"
    schema_version: str = HARNESS_SCHEMA_VERSION
    driver: str = "pi"
    driver_protocol: int = 7
    task_contract: str = TASK_CONTRACT_SCHEMA
    capabilities: tuple[str, ...] = _CAPABILITIES

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "driver": self.driver,
            "driver_protocol": self.driver_protocol,
            "task_contract": self.task_contract,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class HarnessTurn:
    request_id: str
    conversation_id: str
    session_id: str
    task_mode: str
    messages: tuple[dict[str, Any], ...] = field(repr=False)
    task_contract: dict[str, Any] = field(repr=False)
    driver_payload: dict[str, Any] = field(default_factory=dict, repr=False)
    model_advisories: tuple[dict[str, Any], ...] = field(default_factory=tuple, repr=False)

    def pi_payload(self) -> dict[str, Any]:
        payload = dict(self.driver_payload)
        payload.update(
            {
                "messages": deepcopy(list(self.messages)),
                "task_mode": self.task_mode,
                "task_contract": deepcopy(self.task_contract),
                "session_id": self.session_id or None,
            }
        )
        return payload


class ScanSciHarness:
    """Validate capability-bearing turns and forward them to Pi."""

    def __init__(
        self,
        *,
        pi_options: Mapping[str, Any] | None = None,
        pi_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._pi_options = dict(pi_options or {})
        self._pi_client_factory = pi_client_factory
        self._client: Any | None = None

    @property
    def descriptor(self) -> HarnessDescriptor:
        return HarnessDescriptor()

    @property
    def client(self) -> Any:
        if self._client is None:
            factory = self._pi_client_factory
            if factory is None:
                from .pi_agent import PiAgentClient

                factory = PiAgentClient
            self._client = factory(**self._pi_options)
        return self._client

    def prepare_turn(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        task_mode: str,
        task_contract: Mapping[str, Any],
        request_id: str = "",
        conversation_id: str = "",
        session_id: str | None = None,
        **driver_payload: Any,
    ) -> HarnessTurn:
        canonical_messages = self._messages(messages)
        canonical_contract = self._task_contract(task_contract)
        normalized_task_mode = str(task_mode or "general").strip() or "general"
        canonical_driver_payload = dict(driver_payload)
        return HarnessTurn(
            request_id=str(request_id or f"harness-{uuid4().hex}"),
            conversation_id=str(conversation_id or ""),
            session_id=str(session_id or ""),
            task_mode=normalized_task_mode,
            messages=canonical_messages,
            task_contract=canonical_contract,
            driver_payload=canonical_driver_payload,
            model_advisories=_model_advisories_for_turn(
                task_contract=canonical_contract,
                driver_payload=canonical_driver_payload,
            ),
        )

    def stream(self, turn: HarnessTurn) -> Iterable[dict[str, Any]]:
        if not isinstance(turn, HarnessTurn):
            raise HarnessContractError("ScanSci harness requires a prepared HarnessTurn")
        for advisory in turn.model_advisories:
            yield {"type": "notice", **deepcopy(advisory)}
        yield from self.client.stream_chat(**turn.pi_payload())

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @staticmethod
    def _messages(messages: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
            raise HarnessContractError("ScanSci harness messages must be an ordered sequence")
        canonical: list[dict[str, Any]] = []
        has_user = False
        for index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                raise HarnessContractError(f"ScanSci harness message {index} must be an object")
            role = str(message.get("role", "")).strip().casefold()
            if role not in _SUPPORTED_MESSAGE_ROLES:
                raise HarnessContractError(f"ScanSci harness message {index} has unsupported role: {role}")
            has_user = has_user or role == "user"
            canonical.append(deepcopy(dict(message)))
        if not has_user:
            raise HarnessContractError("ScanSci harness turn requires at least one user message")
        return tuple(canonical)

    @staticmethod
    def _task_contract(task_contract: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(task_contract, Mapping):
            raise HarnessContractError("ScanSci harness task contract must be an object")
        raw = deepcopy(dict(task_contract))
        parsed = TaskContract.from_payload(raw)
        if not parsed.source_contract_valid:
            detail = parsed.source_contract_error or "invalid declaration"
            raise HarnessContractError(f"ScanSci harness task contract is invalid: {detail}")
        if raw.get("schema_version") != TASK_CONTRACT_SCHEMA:
            raise HarnessContractError(
                f"ScanSci harness requires task contract {TASK_CONTRACT_SCHEMA}"
            )
        if "version" in raw and raw.get("version") not in (TASK_CONTRACT_VERSION, str(TASK_CONTRACT_VERSION)):
            raise HarnessContractError(
                f"ScanSci harness requires task contract version {TASK_CONTRACT_VERSION}"
            )
        if raw.get("source_contract_valid") is False:
            raise HarnessContractError("ScanSci harness task contract has no authority")
        return raw


def default_harness_descriptor() -> dict[str, Any]:
    return HarnessDescriptor().to_dict()


__all__ = [
    "HARNESS_SCHEMA_VERSION",
    "HarnessContractError",
    "HarnessDescriptor",
    "HarnessTurn",
    "ScanSciHarness",
    "default_harness_descriptor",
]
