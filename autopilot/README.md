# AUTOPILOT control-plane utilities

This package contains public, reusable release-orchestration primitives used by Free Fabric CI.

It deliberately contains **no private product state, provider credentials, cookies, runtime archives, or private repository checkout logic**. Product-specific scheduler state lives in its private project repository; this package tests the generic machinery.

## Covered contracts

- strict scheduler-state validation;
- one-owner packet leases;
- dependency-aware claim/close transitions;
- fail-closed transition conflicts;
- provider outcome classification that separates product/system failures from account health;
- explicit `ACCOUNT_CAPABILITY_RESTRICTED` handling for valid accounts that lack an AI entitlement;
- trust/safety denials are never treated as a reason to rotate accounts;
- auth expiry, rate limits and WAF/provider failures remain distinct outcomes.

The public CI matrix runs these contracts together with the transport, combined acceptance and release-verifier regressions across supported Python/OS combinations.
