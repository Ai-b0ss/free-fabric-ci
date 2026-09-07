from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .state_machine import StateConflict, validate_state

_WORKER_SLOTS = {"A", "B", "C", "D"}


def assign_blocked_packet(
    state: Mapping[str, Any],
    packet_id: str,
    slot: str,
    *,
    evidence: str,
) -> dict[str, Any]:
    """Assign an unowned BLOCKED packet without pretending its blocker is resolved.

    This is the safe handoff path after a stale/disabled owner releases a blocked lease.
    The packet remains BLOCKED. The new owner must later use an explicit BLOCKED->RUNNING
    transition with fresh unblock evidence before execution resumes.
    """
    validate_state(state)
    if slot not in _WORKER_SLOTS:
        raise StateConflict("invalid worker slot")
    if state["workers"][slot].get("packet") is not None:
        raise StateConflict("worker already owns a packet")
    if not isinstance(evidence, str) or not evidence.strip():
        raise StateConflict("blocked handoff requires evidence")

    packet = state["packets"].get(packet_id)
    if packet is None:
        raise StateConflict("unknown packet")
    if packet["state"] != "BLOCKED":
        raise StateConflict("only BLOCKED packets use blocked handoff")
    if packet.get("owner_slot") is not None:
        raise StateConflict("blocked packet is still owned")

    out = deepcopy(state)
    target = out["packets"][packet_id]
    target["owner_slot"] = slot
    target["lease_epoch"] += 1
    target["evidence"].append(evidence.strip())
    out["workers"][slot]["packet"] = packet_id
    out["generation"] += 1
    validate_state(out)
    return out
