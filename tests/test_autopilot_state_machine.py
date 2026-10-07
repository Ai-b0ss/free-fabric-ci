import copy
import unittest

from autopilot.provider_outcomes import ProviderOutcomeCode
from autopilot.state_machine import (
    StateConflict,
    classify_provider_outcome,
    claim_packet,
    release_packet,
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
        "critical_path": ["P"],
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
    "scheduler_policy": {"max_mutable_product_wip": 3},
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

    def test_blocked_packet_is_not_directly_claimable(self):
        state = copy.deepcopy(BASE)
        state["packets"]["P"]["state"] = "BLOCKED"
        state["packets"]["P"]["blocker_class"] = "EXECUTOR_CONTROL_BLOCKED"
        with self.assertRaises(StateConflict):
            claim_packet(state, "P", "A")

    def test_worker_with_existing_packet_cannot_claim_second(self):
        claimed = claim_packet(BASE, "P", "A")
        state = copy.deepcopy(claimed)
        state["packets"]["Q"] = {
            "state": "READY",
            "owner_slot": None,
            "lease_epoch": 0,
            "depends_on": ["PRE"],
            "phase": "RC",
            "blocker_class": None,
            "evidence": [],
        }
        with self.assertRaises(StateConflict):
            claim_packet(state, "Q", "A")

    def test_incomplete_dependency_blocks_claim(self):
        state = copy.deepcopy(BASE)
        state["packets"]["PRE"]["state"] = "BLOCKED"
        state["packets"]["PRE"]["blocker_class"] = "DEPENDENCY_BLOCKED"
        with self.assertRaises(StateConflict):
            claim_packet(state, "P", "A")

    def test_non_owner_cannot_transition(self):
        claimed = claim_packet(BASE, "P", "A")
        with self.assertRaises(StateConflict):
            transition_packet(claimed, "P", "B", "DONE", evidence="proof")

    def test_done_requires_evidence_and_releases_lease(self):
        claimed = claim_packet(BASE, "P", "A")
        with self.assertRaises(StateConflict):
            transition_packet(claimed, "P", "A", "DONE")
        done = transition_packet(claimed, "P", "A", "DONE", evidence="proof green")
        self.assertEqual(done["packets"]["P"]["state"], "DONE")
        self.assertIsNone(done["packets"]["P"]["owner_slot"])
        self.assertIsNone(done["workers"]["A"]["packet"])
        self.assertEqual(done["packets"]["P"]["evidence"], ["proof green"])

    def test_running_release_requeues_ready(self):
        claimed = claim_packet(BASE, "P", "A")
        released = release_packet(claimed, "P", "A")
        self.assertEqual(released["packets"]["P"]["state"], "READY")
        self.assertIsNone(released["packets"]["P"]["owner_slot"])
        self.assertIsNone(released["workers"]["A"]["packet"])

    def test_validate_rejects_worker_packet_owner_mismatch(self):
        state = copy.deepcopy(BASE)
        state["workers"]["A"]["packet"] = "P"
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_validate_rejects_running_without_owner(self):
        state = copy.deepcopy(BASE)
        state["packets"]["P"]["state"] = "RUNNING"
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_validate_rejects_dependency_cycle(self):
        state = copy.deepcopy(BASE)
        state["packets"]["PRE"]["state"] = "READY"
        state["packets"]["PRE"]["depends_on"] = ["P"]
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_validate_rejects_frozen_non_rc(self):
        state = copy.deepcopy(BASE)
        state["release"]["phase"] = "STABILIZE"
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_block_requires_blocker_class_and_evidence(self):
        claimed = claim_packet(BASE, "P", "A")
        with self.assertRaises(StateConflict):
            transition_packet(claimed, "P", "A", "BLOCKED", evidence="executor unavailable")
        blocked = transition_packet(
            claimed,
            "P",
            "A",
            "BLOCKED",
            blocker_class="EXECUTOR_CONTROL_BLOCKED",
            evidence="executor unavailable",
        )
        self.assertEqual(blocked["packets"]["P"]["state"], "BLOCKED")


class ProviderCompatibilityTests(unittest.TestCase):
    def test_state_machine_reexports_single_provider_contract(self):
        restricted = classify_provider_outcome(
            http_status=403,
            message="AI is not available for this account",
        )
        self.assertEqual(restricted.code, ProviderOutcomeCode.ACCOUNT_CAPABILITY_RESTRICTED)
        self.assertTrue(restricted.rotate_account)
        self.assertFalse(restricted.stop_rotation)

        expired = classify_provider_outcome(http_status=401, message="session expired")
        self.assertEqual(expired.code, ProviderOutcomeCode.AUTH_EXPIRED)
        self.assertTrue(expired.rotate_account)
        self.assertFalse(expired.stop_rotation)

        trust = classify_provider_outcome(http_status=403, message="suspicious activity detected")
        self.assertEqual(trust.code, ProviderOutcomeCode.PROVIDER_TRUST_DENIAL)
        self.assertFalse(trust.rotate_account)
        self.assertTrue(trust.stop_rotation)


if __name__ == "__main__":
    unittest.main()
