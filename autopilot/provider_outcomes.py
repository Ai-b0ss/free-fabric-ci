from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class ProviderOutcomeCode(str, Enum):
    GREEN = "GREEN"
    ACCOUNT_CAPABILITY_RESTRICTED = "ACCOUNT_CAPABILITY_RESTRICTED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMIT = "RATE_LIMIT"
    PROVIDER_TRUST_DENIAL = "PROVIDER_TRUST_DENIAL"
    ALL_ACCOUNTS_UNAVAILABLE = "ALL_ACCOUNTS_UNAVAILABLE"
    UNKNOWN_PROVIDER_FAILURE = "UNKNOWN_PROVIDER_FAILURE"


@dataclass(frozen=True)
class ProviderOutcome:
    code: ProviderOutcomeCode
    account_eligible: bool
    rotate_account: bool
    retry_later: bool
    stop_rotation: bool


@dataclass(frozen=True)
class ProviderPoolDecision:
    code: ProviderOutcomeCode
    close_prerequisite: bool
    try_next_account: bool
    stop_rotation: bool
    blocker_class: str | None


def classify_provider_outcome(
    *,
    http_status: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> ProviderOutcome:
    """Classify a sanitized bounded provider preflight result.

    The contract deliberately distinguishes account/session health from an explicit
    provider trust/safety denial. Callers must never pass credentials, cookies, or
    unredacted provider payloads into this helper.
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
            ProviderOutcomeCode.PROVIDER_TRUST_DENIAL,
            account_eligible=False,
            rotate_account=False,
            retry_later=False,
            stop_rotation=True,
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
            ProviderOutcomeCode.ACCOUNT_CAPABILITY_RESTRICTED,
            account_eligible=False,
            rotate_account=True,
            retry_later=False,
            stop_rotation=False,
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
            ProviderOutcomeCode.AUTH_EXPIRED,
            account_eligible=False,
            rotate_account=True,
            retry_later=False,
            stop_rotation=False,
        )

    rate_terms = ("rate limit", "too many requests", "quota exceeded", "capacity")
    if http_status == 429 or any(term in joined for term in rate_terms):
        return ProviderOutcome(
            ProviderOutcomeCode.RATE_LIMIT,
            account_eligible=False,
            rotate_account=True,
            retry_later=True,
            stop_rotation=False,
        )

    if http_status is not None and 200 <= http_status < 300 and not code:
        return ProviderOutcome(
            ProviderOutcomeCode.GREEN,
            account_eligible=True,
            rotate_account=False,
            retry_later=False,
            stop_rotation=False,
        )

    return ProviderOutcome(
        ProviderOutcomeCode.UNKNOWN_PROVIDER_FAILURE,
        account_eligible=False,
        rotate_account=False,
        retry_later=True,
        stop_rotation=True,
    )


def decide_provider_pool(outcomes: Iterable[ProviderOutcome]) -> ProviderPoolDecision:
    """Return the scheduler action for a sequence of legitimately configured accounts.

    Outcomes are evaluated in attempted order. GREEN closes the prerequisite. Explicit
    trust/safety denial stops rotation immediately. Capability/auth/rate conditions may
    continue to the next legitimate account. Exhausting only those failover-safe outcomes
    yields ALL_ACCOUNTS_UNAVAILABLE rather than a product/code failure.
    """
    saw_failover_safe = False
    for outcome in outcomes:
        if outcome.code is ProviderOutcomeCode.GREEN:
            return ProviderPoolDecision(
                ProviderOutcomeCode.GREEN,
                close_prerequisite=True,
                try_next_account=False,
                stop_rotation=False,
                blocker_class=None,
            )
        if outcome.code is ProviderOutcomeCode.PROVIDER_TRUST_DENIAL or outcome.stop_rotation:
            return ProviderPoolDecision(
                outcome.code,
                close_prerequisite=False,
                try_next_account=False,
                stop_rotation=True,
                blocker_class="PROVIDER_TRUST_DENIAL"
                if outcome.code is ProviderOutcomeCode.PROVIDER_TRUST_DENIAL
                else "EXTERNAL_PROVIDER_FAILURE",
            )
        if outcome.rotate_account:
            saw_failover_safe = True
            continue
        return ProviderPoolDecision(
            ProviderOutcomeCode.UNKNOWN_PROVIDER_FAILURE,
            close_prerequisite=False,
            try_next_account=False,
            stop_rotation=True,
            blocker_class="EXTERNAL_PROVIDER_FAILURE",
        )

    if saw_failover_safe:
        return ProviderPoolDecision(
            ProviderOutcomeCode.ALL_ACCOUNTS_UNAVAILABLE,
            close_prerequisite=False,
            try_next_account=False,
            stop_rotation=False,
            blocker_class="ACCOUNT_POOL_UNAVAILABLE",
        )

    return ProviderPoolDecision(
        ProviderOutcomeCode.ALL_ACCOUNTS_UNAVAILABLE,
        close_prerequisite=False,
        try_next_account=False,
        stop_rotation=False,
        blocker_class="ACCOUNT_POOL_UNAVAILABLE",
    )
