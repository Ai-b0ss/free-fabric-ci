from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Mapping, MutableMapping, Optional

SCHEMA = "free-fabric.agent-execution-envelope.v1"
MAX_CALLBACK_KEYS = 8
MAX_CALLBACK_KEY_LEN = 48
MAX_CALLBACK_VALUE_LEN = 256
_FORBIDDEN_CALLBACK_FRAGMENTS = ("secret", "token", "password", "cookie", "authorization", "webhook")


class EnvelopeError(ValueError):
    """Raised when an execution envelope/result fails closed validation."""


def _require_text(value: Any, name: str, *, max_len: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise EnvelopeError(f"invalid {name}")
    return value


def _require_generation(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EnvelopeError("invalid generation")
    return value


def _canonical_request_hash(request: Any) -> str:
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(encoded).hexdigest()


def _validate_callback_metadata(value: Any) -> Mapping[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_CALLBACK_KEYS:
        raise EnvelopeError("invalid callback metadata")
    out: MutableMapping[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > MAX_CALLBACK_KEY_LEN:
            raise EnvelopeError("invalid callback metadata key")
        lowered = key.lower()
        if any(fragment in lowered for fragment in _FORBIDDEN_CALLBACK_FRAGMENTS):
            raise EnvelopeError("secret-bearing callback metadata key")
        if not isinstance(item, str) or len(item) > MAX_CALLBACK_VALUE_LEN:
            raise EnvelopeError("invalid callback metadata value")
        lowered_value = item.lower()
        if any(marker in lowered_value for marker in ("bearer ", "basic ", "apikey=", "api_key=", "token=")):
            raise EnvelopeError("secret-like callback metadata value")
        out[key] = item
    return dict(out)


@dataclass(frozen=True)
class ExecutionIdentity:
    task_id: str
    generation: int
    resource_id: str
    adapter_class: str
    fence: str
    request_id: str
    request_hash: str


@dataclass(frozen=True)
class ExecutionEnvelope:
    identity: ExecutionIdentity
    request: Any
    callback_metadata: Mapping[str, str] = field(default_factory=dict)


def verify_envelope(payload: Any, *, expected_adapter_class: Optional[str] = None,
                    expected_generation: Optional[int] = None,
                    expected_fence: Optional[str] = None,
                    expected_request_id: Optional[str] = None) -> ExecutionEnvelope:
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
        raise EnvelopeError("malformed envelope")
    required = {"schema", "task_id", "generation", "resource_id", "adapter_class", "fence", "request_id", "request", "request_hash"}
    if not required.issubset(payload.keys()):
        raise EnvelopeError("malformed envelope")
    task_id = _require_text(payload["task_id"], "task_id")
    generation = _require_generation(payload["generation"])
    resource_id = _require_text(payload["resource_id"], "resource_id")
    adapter_class = _require_text(payload["adapter_class"], "adapter_class")
    fence = _require_text(payload["fence"], "fence")
    request_id = _require_text(payload["request_id"], "request_id")
    request_hash = _require_text(payload["request_hash"], "request_hash", max_len=64)
    if request_hash != _canonical_request_hash(payload["request"]):
        raise EnvelopeError("wrong request identity")
    if expected_adapter_class is not None and adapter_class != expected_adapter_class:
        raise EnvelopeError("adapter mismatch")
    if expected_generation is not None and generation != expected_generation:
        raise EnvelopeError("stale generation")
    if expected_fence is not None and fence != expected_fence:
        raise EnvelopeError("stale fence")
    if expected_request_id is not None and request_id != expected_request_id:
        raise EnvelopeError("wrong request identity")
    callback_metadata = _validate_callback_metadata(payload.get("callback_metadata"))
    return ExecutionEnvelope(
        identity=ExecutionIdentity(task_id, generation, resource_id, adapter_class, fence, request_id, request_hash),
        request=payload["request"],
        callback_metadata=callback_metadata,
    )


@dataclass(frozen=True)
class CompletionRecord:
    identity: ExecutionIdentity
    kind: str
    payload: Any


def verify_completion(value: Any, *, envelope: ExecutionEnvelope) -> CompletionRecord:
    if not isinstance(value, Mapping):
        raise EnvelopeError("malformed completion")
    kind = value.get("kind")
    if kind not in {"completion", "checkpoint"}:
        raise EnvelopeError("malformed completion")
    identity = envelope.identity
    expected = {
        "task_id": identity.task_id,
        "generation": identity.generation,
        "resource_id": identity.resource_id,
        "adapter_class": identity.adapter_class,
        "fence": identity.fence,
        "request_id": identity.request_id,
        "request_hash": identity.request_hash,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            if key == "generation":
                raise EnvelopeError("stale generation")
            if key == "fence":
                raise EnvelopeError("stale fence")
            if key in {"request_id", "request_hash"}:
                raise EnvelopeError("wrong request identity")
            if key == "adapter_class":
                raise EnvelopeError("adapter mismatch")
            raise EnvelopeError(f"identity mismatch: {key}")
    return CompletionRecord(identity=identity, kind=kind, payload=value.get("payload"))


class CompletionLedger:
    """Accept exact duplicates, reject conflicting terminal/checkpoint records for one fence."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int, str], CompletionRecord] = {}

    def accept(self, record: CompletionRecord) -> bool:
        key = (record.identity.task_id, record.identity.generation, record.identity.fence)
        current = self._records.get(key)
        if current is None:
            self._records[key] = record
            return True
        if current == record:
            return False
        raise EnvelopeError("conflicting duplicate completion")
