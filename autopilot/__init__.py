"""Reusable release-scheduler primitives for Free Fabric CI."""

from .lease_handoff import assign_blocked_packet
from .provider_outcomes import (
    ProviderOutcome,
    ProviderOutcomeCode,
    ProviderPoolDecision,
    classify_provider_outcome,
    decide_provider_pool,
)
from .state_machine import (
    StateConflict,
    claim_packet,
    release_packet,
    transition_packet,
    validate_state,
)

__all__ = [
    "ProviderOutcome",
    "ProviderOutcomeCode",
    "ProviderPoolDecision",
    "StateConflict",
    "assign_blocked_packet",
    "classify_provider_outcome",
    "decide_provider_pool",
    "claim_packet",
    "release_packet",
    "transition_packet",
    "validate_state",
]
