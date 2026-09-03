import copy
import unittest

from autopilot.lease_handoff import assign_blocked_packet
from autopilot.state_machine import StateConflict, release_packet, transition_packet
from tests.test_autopilot_state_machine import BASE


class BlockedHandoffTests(unittest.TestCase):
    def _blocked_owned(self):
        state = copy.deepcopy(BASE)
        state["packets"]["P"]["state"] = "RUNNING"
        state["packets"]["P"]["owner_slot"] = "A"
        state["workers"]["A"]["packet"] = "P"
        return transition_packet(
            state,
            "P",
            "A",
            "BLOCKED",
            blocker_class="EXECUTOR_CONTROL_BLOCKED",
            evidence="executor unavailable",
        )

    def test_released_blocked_packet_can_be_handed_off_without_resuming(self):
        blocked = self._blocked_owned()
        released = release_packet(blocked, "P", "A")
        self.assertEqual(released["packets"]["P"]["state"], "BLOCKED")
        self.assertIsNone(released["packets"]["P"]["owner_slot"])

        handed = assign_blocked_packet(
            released,
            "P",
            "B",
            evidence="A stale for two cadences; Director reassigned lease only",
        )
        self.assertEqual(handed["packets"]["P"]["state"], "BLOCKED")
        self.assertEqual(handed["packets"]["P"]["owner_slot"], "B")
        self.assertEqual(handed["workers"]["B"]["packet"], "P")

        resumed = transition_packet(
            handed,
            "P",
            "B",
            "RUNNING",
            evidence="new private executor is online",
        )
        self.assertEqual(resumed["packets"]["P"]["state"], "RUNNING")

    def test_handoff_refuses_owned_or_nonblocked_packet(self):
        blocked = self._blocked_owned()
        with self.assertRaises(StateConflict):
            assign_blocked_packet(blocked, "P", "B", evidence="no")
        with self.assertRaises(StateConflict):
            assign_blocked_packet(BASE, "P", "B", evidence="no")

    def test_handoff_requires_idle_worker_and_evidence(self):
        blocked = release_packet(self._blocked_owned(), "P", "A")
        busy = copy.deepcopy(blocked)
        busy["workers"]["B"]["packet"] = "OTHER"
        with self.assertRaises(ValueError):
            # Snapshot itself is invalid and must fail before handoff.
            assign_blocked_packet(busy, "P", "B", evidence="reason")
        with self.assertRaises(StateConflict):
            assign_blocked_packet(blocked, "P", "B", evidence="")


if __name__ == "__main__":
    unittest.main()
