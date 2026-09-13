from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .provider_outcomes import (
    ProviderOutcome,
    ProviderOutcomeCode,
    ProviderPoolDecision,
    classify_provider_outcome,
    decide_provider_pool,
)


class StateConflict(RuntimeError):
    """Raised when a transition violates lease/dependency/release invariants."""


_ALLOWED_PACKET_STATES = {"READY", "RUNNING", "BLOCKED", "DONE", "DEFERRED"}
_ALLOWED_PHASES = {"BUILD", "STABILIZE", "RC", "READ_ONLY"}
_WORKER_SLOTS = ("A", "B", "C", "D")
_REQUIRED_SLOTS = ("DIRECTOR",) + _WORKER_SLOTS


def _deps_done(state: Mapping[str, Any], packet_id: str) -> bool:
    packet = state["packets"][packet_id]
    return all(state["packets"][dep]["state"] == "DONE" for dep in packet["depends_on"])


def _check_dependency_graph(packets: Mapping[str, Any]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(packet_id: str) -> None:
        if packet_id in visited:
            return
        if packet_id in visiting:
            raise ValueError(f"dependency cycle at {packet_id}")
        visiting.add(packet_id)
        for dep in packets[packet_id]["depends_on"]:
            visit(dep)
        visiting.remove(packet_id)
        visited.add(packet_id)

    for packet_id in packets:
        visit(packet_id)


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    generation = state.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError("invalid generation")

    release = state.get("release")
    if not isinstance(release, Mapping):
        raise ValueError("missing release")
    if release.get("phase") not in {"BUILD", "STABILIZE", "RC"}:
        raise ValueError("invalid release phase")
    candidate = release.get("candidate_sha")
    if not isinstance(candidate, str) or len(candidate) != 40 or any(c not in "0123456789abcdef" for c in candidate):
        raise ValueError("invalid candidate sha")
    if not isinstance(release.get("frozen"), bool):
        raise ValueError("invalid frozen flag")
    if release["frozen"] and release["phase"] != "RC":
        raise ValueError("frozen release must be RC")

    workers = state.get("workers")
    if not isinstance(workers, Mapping):
        raise ValueError("missing workers")
    for slot in _REQUIRED_SLOTS:
        worker = workers.get(slot)
        if not isinstance(worker, Mapping):
            raise ValueError(f"missing worker {slot}")
        if not isinstance(worker.get("automation_id"), str) or not worker["automation_id"]:
            raise ValueError(f"invalid automation id: {slot}")
        if not isinstance(worker.get("enabled"), bool):
            raise ValueError(f"invalid enabled flag: {slot}")
        if worker.get("packet") is not None and not isinstance(worker.get("packet"), str):
            raise ValueError(f"invalid worker packet: {slot}")
    if workers["DIRECTOR"].get("packet") is not None:
        raise ValueError("DIRECTOR cannot own a packet")

    packets = state.get("packets")
    if not isinstance(packets, Mapping) or not packets:
        raise ValueError("missing packets")
    for packet_id, packet in packets.items():
        if not isinstance(packet_id, str) or not packet_id:
            raise ValueError("invalid packet id")
        if not isinstance(packet, Mapping):
            raise ValueError(f"invalid packet: {packet_id}")
        if packet.get("state") not in _ALLOWED_PACKET_STATES:
            raise ValueError(f"invalid packet state: {packet_id}")
        if packet.get("phase") not in _ALLOWED_PHASES:
            raise ValueError(f"invalid packet phase: {packet_id}")
        epoch = packet.get("lease_epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise ValueError(f"invalid lease epoch: {packet_id}")
        owner = packet.get("owner_slot")
        if owner is not None and owner not in _WORKER_SLOTS:
            raise ValueError(f"invalid owner slot: {packet_id}")
        deps = packet.get("depends_on")
        if not isinstance(deps, list) or not all(isinstance(x, str) and x for x in deps):
            raise ValueError(f"invalid dependencies: {packet_id}")
        if len(deps) != len(set(deps)) or packet_id in deps:
            raise ValueError(f"duplicate/self dependency: {packet_id}")
        evidence = packet.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
            raise ValueError(f"invalid evidence: {packet_id}")
        state_name = packet["state"]
        blocker_class = packet.get("blocker_class")
        if state_name == "READY" and owner is not None:
            raise ValueError(f"READY packet cannot be owned: {packet_id}")
        if state_name == "RUNNING" and owner is None:
            raise ValueError(f"RUNNING packet must be owned: {packet_id}")
        if state_name in {"DONE", "DEFERRED"} and owner is not None:
            raise ValueError(f"terminal packet cannot be owned: {packet_id}")
        if state_name == "BLOCKED" and not isinstance(blocker_class, str):
            raise ValueError(f"BLOCKED packet needs blocker_class: {packet_id}")
        if state_name in {"READY", "RUNNING", "DONE"} and blocker_class is not None:
            raise ValueError(f"non-blocked packet has blocker_class: {packet_id}")

    for packet_id, packet in packets.items():
        for dep in packet["depends_on"]:
            if dep not in packets:
                raise ValueError(f"unknown dependency {dep} for {packet_id}")
    _check_dependency_graph(packets)

    # A DONE packet cannot depend on unfinished work.
    for packet_id, packet in packets.items():
        if packet["state"] == "DONE" and not _deps_done(state, packet_id):
            raise ValueError(f"DONE packet has incomplete dependency: {packet_id}")

    seen_packets: set[str] = set()
    for slot in _WORKER_SLOTS:
        packet_id = workers[slot].get("packet")
        if packet_id is None:
            continue
        if packet_id not in packets:
            raise ValueError(f"worker {slot} references unknown packet")
        if packet_id in seen_packets:
            raise ValueError(f"packet assigned to multiple workers: {packet_id}")
        seen_packets.add(packet_id)
        if packets[packet_id].get("owner_slot") != slot:
            raise ValueError(f"worker/packet ownership mismatch: {slot}/{packet_id}")
    for packet_id, packet in packets.items():
        owner = packet.get("owner_slot")
        if owner is not None and workers[owner].get("packet") != packet_id:
            raise ValueError(f"packet/worker ownership mismatch: {packet_id}/{owner}")

    critical_path = release.get("critical_path")
    if critical_path is not None:
        if not isinstance(critical_path, list) or not all(isinstance(x, str) for x in critical_path):
            raise ValueError("invalid critical_path")
        for packet_id in critical_path:
            if packet_id not in packets:
                raise ValueError(f"critical path references unknown packet: {packet_id}")

    policy = state.get("scheduler_policy", {})
    max_wip = policy.get("max_mutable_product_wip", 3) if isinstance(policy, Mapping) else 3
    if not isinstance(max_wip, int) or isinstance(max_wip, bool) or max_wip < 1:
        raise ValueError("invalid max_mutable_product_wip")
    mutable_wip = sum(
        1
        for packet in packets.values()
        if packet["state"] == "RUNNING" and packet["phase"] != "READ_ONLY"
    )
    if mutable_wip > max_wip:
        raise ValueError("mutable WIP limit exceeded")


def claim_packet(state: Mapping[str, Any], packet_id: str, slot: str) -> dict[str, Any]:
    validate_state(state)
    if slot not in _WORKER_SLOTS:
        raise StateConflict("invalid worker slot")
    if state["workers"][slot].get("packet") is not None:
        raise StateConflict("worker already owns a packet")
    packet = state["packets"].get(packet_id)
    if packet is None:
        raise StateConflict("unknown packet")
    # BLOCKED is deliberately not claimable. The blocker must first be resolved/reclassified
    # by an explicit state transition, otherwise hourly workers can accidentally retry-loop it.
    if packet["state"] != "READY":
        raise StateConflict("only READY packets are claimable")
    if packet.get("owner_slot") is not None:
        raise StateConflict("packet is leased by another worker")
    if not _deps_done(state, packet_id):
        raise StateConflict("dependencies are not done")

    out = deepcopy(state)
    target = out["packets"][packet_id]
    target["owner_slot"] = slot
    target["lease_epoch"] += 1
    target["state"] = "RUNNING"
    target["blocker_class"] = None
    out["workers"][slot]["packet"] = packet_id
    out["generation"] += 1
    validate_state(out)
    return out


def release_packet(state: Mapping[str, Any], packet_id: str, slot: str) -> dict[str, Any]:
    """Release a lease safely.

    RUNNING handoff becomes READY; BLOCKED stays BLOCKED. Terminal packets already release
    their lease through transition_packet and therefore cannot be released here.
    """
    validate_state(state)
    packet = state["packets"].get(packet_id)
    if packet is None or packet.get("owner_slot") != slot:
        raise StateConflict("worker does not own packet")
    if packet["state"] not in {"RUNNING", "BLOCKED"}:
        raise StateConflict("packet cannot be released from this state")
    out = deepcopy(state)
    target = out["packets"][packet_id]
    if target["state"] == "RUNNING":
        if not _deps_done(out, packet_id):
            raise StateConflict("cannot requeue packet with incomplete dependencies")
        target["state"] = "READY"
    target["owner_slot"] = None
    if out["workers"].get(slot, {}).get("packet") == packet_id:
        out["workers"][slot]["packet"] = None
    out["generation"] += 1
    validate_state(out)
    return out


def transition_packet(
    state: Mapping[str, Any],
    packet_id: str,
    slot: str,
    new_state: str,
    *,
    blocker_class: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    validate_state(state)
    packet = state["packets"].get(packet_id)
    if packet is None:
        raise StateConflict("unknown packet")
    if packet.get("owner_slot") != slot:
        raise StateConflict("worker does not own packet")

    current = packet["state"]
    allowed = {
        "RUNNING": {"BLOCKED", "DONE", "DEFERRED"},
        "BLOCKED": {"RUNNING", "DONE", "DEFERRED"},
    }
    if new_state not in allowed.get(current, set()):
        raise StateConflict(f"illegal transition {current}->{new_state}")
    if new_state in {"RUNNING", "DONE"} and not _deps_done(state, packet_id):
        raise StateConflict("dependencies are not done")
    if new_state == "BLOCKED" and not blocker_class:
        raise StateConflict("BLOCKED transition requires blocker_class")
    if new_state != "BLOCKED" and blocker_class is not None:
        raise StateConflict("blocker_class only valid for BLOCKED transition")
    if new_state in {"BLOCKED", "DONE", "DEFERRED"} and not evidence:
        raise StateConflict("transition requires evidence")
    if current == "BLOCKED" and new_state == "RUNNING" and not evidence:
        raise StateConflict("resuming BLOCKED packet requires unblock evidence")

    out = deepcopy(state)
    target = out["packets"][packet_id]
    target["state"] = new_state
    target["blocker_class"] = blocker_class if new_state == "BLOCKED" else None
    if evidence:
        target["evidence"].append(evidence)
    if new_state in {"DONE", "DEFERRED"}:
        target["owner_slot"] = None
        if out["workers"].get(slot, {}).get("packet") == packet_id:
            out["workers"][slot]["packet"] = None
    out["generation"] += 1
    validate_state(out)
    return out


__all__ = [
    "ProviderOutcome",
    "ProviderOutcomeCode",
    "ProviderPoolDecision",
    "StateConflict",
    "classify_provider_outcome",
    "decide_provider_pool",
    "claim_packet",
    "release_packet",
    "transition_packet",
    "validate_state",
]
