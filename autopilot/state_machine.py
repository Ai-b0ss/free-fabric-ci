from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


class StateConflict(RuntimeError):
    """Raised when a transition violates lease/dependency/release invariants."""


_ALLOWED_PACKET_STATES = {"READY", "RUNNING", "BLOCKED", "DONE", "DEFERRED"}
_ALLOWED_PHASES = {"BUILD", "STABILIZE", "RC", "READ_ONLY"}


@dataclass(frozen=True)
class ProviderOutcome:
    code: str
    account_eligible: bool
    system_blocker: bool
    rotate_account: bool
    retry_later: bool
    explanation: str


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    generation = state.get("generation")
    if not isinstance(generation, int) or generation < 1:
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

    packets = state.get("packets")
    if not isinstance(packets, Mapping):
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
        if not isinstance(epoch, int) or epoch < 0:
            raise ValueError(f"invalid lease epoch: {packet_id}")
        deps = packet.get("depends_on")
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
            raise ValueError(f"invalid dependencies: {packet_id}")
        evidence = packet.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
            raise ValueError(f"invalid evidence: {packet_id}")

    for packet_id, packet in packets.items():
        for dep in packet["depends_on"]:
            if dep not in packets:
                raise ValueError(f"unknown dependency {dep} for {packet_id}")

    workers = state.get("workers")
    if not isinstance(workers, Mapping):
        raise ValueError("missing workers")
    for slot in ("DIRECTOR", "A", "B", "C", "D"):
        if slot not in workers:
            raise ValueError(f"missing worker {slot}")


def _deps_done(state: Mapping[str, Any], packet_id: str) -> bool:
    packet = state["packets"][packet_id]
    return all(state["packets"][dep]["state"] == "DONE" for dep in packet["depends_on"])


def claim_packet(state: Mapping[str, Any], packet_id: str, slot: str) -> dict[str, Any]:
    validate_state(state)
    if slot == "DIRECTOR" or slot not in state["workers"]:
        raise StateConflict("invalid worker slot")
    packet = state["packets"].get(packet_id)
    if packet is None:
        raise StateConflict("unknown packet")
    if packet["state"] not in {"READY", "BLOCKED"}:
        raise StateConflict("packet is not claimable")
    if packet.get("owner_slot") not in {None, slot}:
        raise StateConflict("packet is leased by another worker")
    if packet["state"] == "READY" and not _deps_done(state, packet_id):
        raise StateConflict("dependencies are not done")

    out = deepcopy(state)
    target = out["packets"][packet_id]
    target["owner_slot"] = slot
    target["lease_epoch"] += 1
    if target["state"] == "READY":
        target["state"] = "RUNNING"
    out["workers"][slot]["packet"] = packet_id
    out["generation"] += 1
    validate_state(out)
    return out


def release_packet(state: Mapping[str, Any], packet_id: str, slot: str) -> dict[str, Any]:
    validate_state(state)
    packet = state["packets"].get(packet_id)
    if packet is None or packet.get("owner_slot") != slot:
        raise StateConflict("worker does not own packet")
    out = deepcopy(state)
    out["packets"][packet_id]["owner_slot"] = None
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
    if new_state not in _ALLOWED_PACKET_STATES:
        raise StateConflict("invalid target state")
    packet = state["packets"].get(packet_id)
    if packet is None:
        raise StateConflict("unknown packet")
    if packet.get("owner_slot") != slot:
        raise StateConflict("worker does not own packet")
    if new_state == "DONE" and not _deps_done(state, packet_id):
        raise StateConflict("cannot close packet with incomplete dependencies")

    out = deepcopy(state)
    target = out["packets"][packet_id]
    target["state"] = new_state
    target["blocker_class"] = blocker_class
    if evidence:
        target["evidence"].append(evidence)
    if new_state in {"DONE", "DEFERRED"}:
        target["owner_slot"] = None
        if out["workers"].get(slot, {}).get("packet") == packet_id:
            out["workers"][slot]["packet"] = None
    out["generation"] += 1
    validate_state(out)
    return out


def classify_provider_outcome(
    *,
    http_status: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> ProviderOutcome:
    """Classify provider/account failures without conflating account health with system health.

    This intentionally uses conservative textual buckets. Callers should pass sanitized
    provider codes/messages, never credentials or response bodies containing secrets.
    """
    code = (error_code or "").strip().lower()
    text = (message or "").strip().lower()
    joined = f"{code} {text}"

    trust_terms = (
        "trust",
        "abuse",
        "safety",
        "suspicious activity",
        "policy violation",
        "account suspended",
        "account banned",
    )
    if any(term in joined for term in trust_terms):
        return ProviderOutcome(
            "PROVIDER_TRUST_DENIAL", False, False, False, False,
            "explicit trust/safety denial; do not rotate accounts to evade it",
        )

    capability_terms = (
        "ai is not available",
        "ai unavailable",
        "ai disabled",
        "ai access disabled",
        "ai feature disabled",
        "not entitled",
        "entitlement",
        "upgrade required",
        "plan does not include",
        "feature unavailable for this account",
        "workspace does not have ai",
    )
    if any(term in joined for term in capability_terms):
        return ProviderOutcome(
            "ACCOUNT_CAPABILITY_RESTRICTED", False, False, True, False,
            "session may be valid but this account lacks the required AI capability",
        )

    auth_terms = (
        "invalid token",
        "token expired",
        "session expired",
        "unauthorized",
        "invalid session",
        "authentication failed",
    )
    if http_status in {401, 419} or any(term in joined for term in auth_terms):
        return ProviderOutcome(
            "AUTH_EXPIRED", False, True, False, False,
            "credentials/session were rejected and need refresh",
        )

    rate_terms = ("rate limit", "too many requests", "quota exceeded", "capacity")
    if http_status == 429 or any(term in joined for term in rate_terms):
        return ProviderOutcome(
            "RATE_LIMIT", True, False, True, True,
            "temporary provider/account capacity condition; use normal cooldown/failover",
        )

    waf_terms = ("cloudflare", "waf", "challenge required", "captcha")
    if http_status == 403 and any(term in joined for term in waf_terms):
        return ProviderOutcome(
            "PROVIDER_WAF_BLOCK", True, True, False, True,
            "provider boundary blocked the request; treat separately from product logic",
        )

    if http_status is not None and 200 <= http_status < 300 and not code:
        return ProviderOutcome("GREEN", True, False, False, False, "provider boundary succeeded")

    return ProviderOutcome(
        "UNKNOWN_PROVIDER_FAILURE", True, True, False, True,
        "unclassified provider failure; preserve sanitized evidence and fail closed",
    )
