import copy
import unittest

from autopilot.state_machine import (
    StateConflict,
    classify_provider_outcome,
    claim_packet,
    transition_packet,
    validate_state,
)


BASE = {
    "schema_version": 1,
    "generation": 1,
    "release": {
        "phase": "RC",
        "candidate_sha": "a" * 40,
        "frozen": True,
    },
    "workers": {
        "DIRECTOR": {"automation_id": "d", "enabled": True, "packet": None},
        "A": {"automation_id": "a", "enabled": True, "packet": None},
        "B": {"automation_id": "b", "enabled": True, "packet": None},
        "C": {"automation_id": "c", "enabled": True, "packet": None},
        "D": {"automation_id": "e", "enabled": True, "packet": None},
    },
    "packets": {
        "PRE": {
            "state": "DONE",
            "owner_slot": None,
            "lease_epoch": 0,
            "depends_on": [],
            "phase": "RC",
            "blocker_class": None,
            "evidence": [],
        },
        "P": {
            "state": "READY",
            "owner_slot": None,
            "lease_epoch": 0,
            "depends_on": ["PRE"],
            "phase": "RC",
            "blocker_class": None,
            "evidence": [],
        },
    },
}


class StateMachineTests(unittest.TestCase):
    def test_validate_accepts_minimal_state(self):
        validate_state(BASE)

    def test_claim_is_sticky_and_increments_epoch(self):
        claimed = claim_packet(BASE, "P", "A")
        self.assertEqual(claimed["packets"]["P"]["owner_slot"], "A")
        self.assertEqual(claimed["packets"]["P"]["lease_epoch"], 1)
        self.assertEqual(claimed["packets"]["P"]["state"], "RUNNING")
        self.assertEqual(claimed["workers"]["A"]["packet"], "P")
        self.assertEqual(claimed["generation"], 2)

    def test_second_worker_cannot_claim_owned_packet(self):
        state = copy.deepcopy(BASE)
        state["packets"]["P"]["state"] = "BLOCKED"
        state["packets"]["P"]["owner_slot"] = "A"
        with self.assertRaises(StateConflict):
            claim_packet(state, "P", "B")

    def test_incomplete_dependency_blocks_claim(self):
        state = copy.deepcopy(BASE)
        state["packets"]["PRE"]["state"] = "BLOCKED"
        with self.assertRaises(StateConflict):
            claim_packet(state, "P", "A")

    def test_non_owner_cannot_transition(self):
        claimed = claim_packet(BASE, "P", "A")
        with self.assertRaises(StateConflict):
            transition_packet(claimed, "P", "B", "DONE")

    def test_done_releases_lease(self):
        claimed = claim_packet(BASE, "P", "A")
        done = transition_packet(claimed, "P", "A", "DONE", evidence="proof green")
        self.assertEqual(done["packets"]["P"]["state"], "DONE")
        self.assertIsNone(done["packets"]["P"]["owner_slot"])
        self.assertIsNone(done["workers"]["A"]["packet"])
        self.assertEqual(done["packets"]["P"]["evidence"], ["proof green"])


class ProviderOutcomeTests(unittest.TestCase):
    def test_ai_disabled_is_account_health_not_system_blocker(self):
        out = classify_provider_outcome(
            http_status=403,
            error_code="feature_unavailable",
            message="AI is not available for this account",
        )
        self.assertEqual(out.code, "ACCOUNT_CAPABILITY_RESTRICTED")
        self.assertFalse(out.system_blocker)
        self.assertTrue(out.rotate_account)
        self.assertFalse(out.account_eligible)

    def test_plan_entitlement_is_account_restriction(self):
        out = classify_provider_outcome(message="Workspace does not have AI; upgrade required")
        self.assertEqual(out.code, "ACCOUNT_CAPABILITY_RESTRICTED")
        self.assertTrue(out.rotate_account)

    def test_expired_session_is_auth_blocker(self):
        out = classify_provider_outcome(http_status=401, message="session expired")
        self.assertEqual(out.code, "AUTH_EXPIRED")
        self.assertTrue(out.system_blocker)
        self.assertFalse(out.rotate_account)

    def test_trust_denial_is_not_rotated(self):
        out = classify_provider_outcome(http_status=403, message="suspicious activity detected")
        self.assertEqual(out.code, "PROVIDER_TRUST_DENIAL")
        self.assertFalse(out.rotate_account)
        self.assertFalse(out.retry_later)

    def test_rate_limit_uses_normal_failover(self):
        out = classify_provider_outcome(http_status=429, message="too many requests")
        self.assertEqual(out.code, "RATE_LIMIT")
        self.assertTrue(out.rotate_account)
        self.assertTrue(out.retry_later)
        self.assertFalse(out.system_blocker)

    def test_success_is_green(self):
        out = classify_provider_outcome(http_status=200)
        self.assertEqual(out.code, "GREEN")
        self.assertFalse(out.system_blocker)


if __name__ == "__main__":
    unittest.main()
