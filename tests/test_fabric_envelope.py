import hashlib
import json
import unittest

from autopilot.fabric_envelope import CompletionLedger, EnvelopeError, verify_completion, verify_envelope


def h(request):
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


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


class FabricEnvelopeTests(unittest.TestCase):
    def assert_envelope_error(self, pattern, func, *args, **kwargs):
        with self.assertRaisesRegex(EnvelopeError, pattern):
            func(*args, **kwargs)

    def test_valid_envelope_and_identity_preservation(self):
        env = verify_envelope(
            envelope(),
            expected_adapter_class="notion",
            expected_generation=7,
            expected_fence="fence-9",
            expected_request_id="req-3",
        )
        self.assertEqual(env.identity.task_id, "task-1")
        self.assertEqual(env.identity.generation, 7)
        self.assertEqual(env.identity.resource_id, "resource-a")
        self.assertEqual(env.identity.adapter_class, "notion")
        self.assertEqual(env.identity.fence, "fence-9")
        self.assertEqual(env.identity.request_id, "req-3")
        self.assertEqual(env.identity.request_hash, h({"op": "run", "args": [1, 2]}))

    def test_fail_closed_expected_identity(self):
        cases = [
            ("schema", "wrong", "malformed envelope"),
            ("adapter_class", "qwen", "adapter mismatch"),
            ("generation", 6, "stale generation"),
            ("fence", "old", "stale fence"),
            ("request_id", "wrong", "wrong request identity"),
        ]
        for field, value, error in cases:
            with self.subTest(field=field):
                data = envelope()
                data[field] = value
                self.assert_envelope_error(
                    error,
                    verify_envelope,
                    data,
                    expected_adapter_class="notion",
                    expected_generation=7,
                    expected_fence="fence-9",
                    expected_request_id="req-3",
                )

    def test_request_hash_tamper_fails_closed(self):
        data = envelope()
        data["request"]["args"].append(3)
        self.assert_envelope_error("wrong request identity", verify_envelope, data)

    def test_callback_metadata_rejects_secret_like_or_unbounded_values(self):
        cases = [
            {"authorization": "redacted"},
            {"trace": "Bearer abc"},
            {str(i): "x" for i in range(9)},
        ]
        for metadata in cases:
            with self.subTest(metadata=metadata):
                data = envelope()
                data["callback_metadata"] = metadata
                with self.assertRaises(EnvelopeError):
                    verify_envelope(data)

    def test_completion_and_checkpoint_preserve_identity(self):
        env = verify_envelope(envelope())
        self.assertEqual(verify_completion(completion(env), envelope=env).kind, "completion")
        self.assertEqual(
            verify_completion(completion(env, kind="checkpoint"), envelope=env).kind,
            "checkpoint",
        )

    def test_completion_mismatch_fails_closed(self):
        cases = [
            ("generation", 8, "stale generation"),
            ("fence", "other", "stale fence"),
            ("request_id", "other", "wrong request identity"),
            ("request_hash", "0" * 64, "wrong request identity"),
            ("adapter_class", "qwen", "adapter mismatch"),
        ]
        env = verify_envelope(envelope())
        for field, value, error in cases:
            with self.subTest(field=field):
                result = completion(env)
                result[field] = value
                self.assert_envelope_error(error, verify_completion, result, envelope=env)

    def test_exact_duplicate_is_idempotent_conflict_is_rejected(self):
        env = verify_envelope(envelope())
        ledger = CompletionLedger()
        record = verify_completion(completion(env, payload={"ok": True}), envelope=env)
        self.assertTrue(ledger.accept(record))
        self.assertFalse(ledger.accept(record))
        conflict = verify_completion(completion(env, payload={"ok": False}), envelope=env)
        self.assert_envelope_error("conflicting duplicate completion", ledger.accept, conflict)

    def test_malformed_envelope_missing_identity_rejected(self):
        data = envelope()
        del data["task_id"]
        self.assert_envelope_error("malformed envelope", verify_envelope, data)


if __name__ == "__main__":
    unittest.main()
