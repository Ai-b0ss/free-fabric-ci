import copy
import hashlib
import json

import pytest

from autopilot.fabric_envelope import CompletionLedger, EnvelopeError, verify_completion, verify_envelope


def h(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def envelope():
    request = {"op": "run", "args": [1, 2]}
    return {
        "schema": "free-fabric.agent-execution-envelope.v1",
        "task_id": "task-1",
        "generation": 7,
        "resource_id": "resource-a",
        "adapter_class": "notion",
        "fence": "fence-9",
        "request_id": "req-3",
        "request": request,
        "request_hash": h(request),
        "callback_metadata": {"trace_id": "trace-1"},
    }


def completion(env, *, kind="completion", payload=None):
    return {
        "kind": kind,
        "task_id": env.identity.task_id,
        "generation": env.identity.generation,
        "resource_id": env.identity.resource_id,
        "adapter_class": env.identity.adapter_class,
        "fence": env.identity.fence,
        "request_id": env.identity.request_id,
        "request_hash": env.identity.request_hash,
        "payload": payload,
    }


def test_valid_envelope_and_identity_preservation():
    env = verify_envelope(envelope(), expected_adapter_class="notion", expected_generation=7, expected_fence="fence-9", expected_request_id="req-3")
    assert env.identity.task_id == "task-1"
    assert env.identity.resource_id == "resource-a"


@pytest.mark.parametrize("field,value,error", [
    ("schema", "wrong", "malformed envelope"),
    ("adapter_class", "qwen", "adapter mismatch"),
    ("generation", 6, "stale generation"),
    ("fence", "old", "stale fence"),
    ("request_id", "wrong", "wrong request identity"),
])
def test_fail_closed_expected_identity(field, value, error):
    data = envelope(); data[field] = value
    with pytest.raises(EnvelopeError, match=error):
        verify_envelope(data, expected_adapter_class="notion", expected_generation=7, expected_fence="fence-9", expected_request_id="req-3")


def test_request_hash_tamper_fails_closed():
    data = envelope(); data["request"]["args"].append(3)
    with pytest.raises(EnvelopeError, match="wrong request identity"):
        verify_envelope(data)


@pytest.mark.parametrize("metadata", [
    {"authorization": "redacted"},
    {"trace": "Bearer abc"},
    {str(i): "x" for i in range(9)},
])
def test_callback_metadata_rejects_secret_like_or_unbounded_values(metadata):
    data = envelope(); data["callback_metadata"] = metadata
    with pytest.raises(EnvelopeError):
        verify_envelope(data)


def test_completion_and_checkpoint_preserve_identity():
    env = verify_envelope(envelope())
    assert verify_completion(completion(env), envelope=env).kind == "completion"
    assert verify_completion(completion(env, kind="checkpoint"), envelope=env).kind == "checkpoint"


@pytest.mark.parametrize("field,value,error", [
    ("generation", 8, "stale generation"),
    ("fence", "other", "stale fence"),
    ("request_id", "other", "wrong request identity"),
    ("request_hash", "0" * 64, "wrong request identity"),
    ("adapter_class", "qwen", "adapter mismatch"),
])
def test_completion_mismatch_fails_closed(field, value, error):
    env = verify_envelope(envelope())
    result = completion(env); result[field] = value
    with pytest.raises(EnvelopeError, match=error):
        verify_completion(result, envelope=env)


def test_exact_duplicate_is_idempotent_conflict_is_rejected():
    env = verify_envelope(envelope())
    ledger = CompletionLedger()
    record = verify_completion(completion(env, payload={"ok": True}), envelope=env)
    assert ledger.accept(record) is True
    assert ledger.accept(record) is False
    conflict = verify_completion(completion(env, payload={"ok": False}), envelope=env)
    with pytest.raises(EnvelopeError, match="conflicting duplicate completion"):
        ledger.accept(conflict)


def test_malformed_envelope_missing_identity_rejected():
    data = envelope(); del data["task_id"]
    with pytest.raises(EnvelopeError, match="malformed envelope"):
        verify_envelope(data)
