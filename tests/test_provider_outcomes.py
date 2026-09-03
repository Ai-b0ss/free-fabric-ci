import unittest

from autopilot.provider_outcomes import (
    ProviderOutcomeCode,
    classify_provider_outcome,
    decide_provider_pool,
)


class ProviderOutcomeContractTests(unittest.TestCase):
    def test_green_closes_prerequisite(self):
        out = classify_provider_outcome(http_status=200)
        self.assertEqual(out.code, ProviderOutcomeCode.GREEN)
        decision = decide_provider_pool([out])
        self.assertTrue(decision.close_prerequisite)
        self.assertIsNone(decision.blocker_class)

    def test_capability_restriction_rotates_only_account(self):
        out = classify_provider_outcome(
            http_status=403,
            error_code="feature_unavailable",
            message="AI is not available for this account",
        )
        self.assertEqual(out.code, ProviderOutcomeCode.ACCOUNT_CAPABILITY_RESTRICTED)
        self.assertTrue(out.rotate_account)
        self.assertFalse(out.stop_rotation)

    def test_auth_expiry_allows_legitimate_failover(self):
        out = classify_provider_outcome(http_status=401, message="session expired")
        self.assertEqual(out.code, ProviderOutcomeCode.AUTH_EXPIRED)
        self.assertTrue(out.rotate_account)
        self.assertFalse(out.stop_rotation)

    def test_rate_limit_allows_cooldown_and_failover(self):
        out = classify_provider_outcome(http_status=429, message="too many requests")
        self.assertEqual(out.code, ProviderOutcomeCode.RATE_LIMIT)
        self.assertTrue(out.rotate_account)
        self.assertTrue(out.retry_later)
        self.assertFalse(out.stop_rotation)

    def test_trust_denial_stops_rotation(self):
        out = classify_provider_outcome(http_status=403, message="suspicious activity detected")
        self.assertEqual(out.code, ProviderOutcomeCode.PROVIDER_TRUST_DENIAL)
        self.assertFalse(out.rotate_account)
        self.assertTrue(out.stop_rotation)
        decision = decide_provider_pool([out])
        self.assertTrue(decision.stop_rotation)
        self.assertEqual(decision.blocker_class, "PROVIDER_TRUST_DENIAL")

    def test_green_after_account_health_failures_closes_gate(self):
        outcomes = [
            classify_provider_outcome(http_status=401, message="session expired"),
            classify_provider_outcome(http_status=429, message="rate limit"),
            classify_provider_outcome(http_status=200),
        ]
        decision = decide_provider_pool(outcomes)
        self.assertEqual(decision.code, ProviderOutcomeCode.GREEN)
        self.assertTrue(decision.close_prerequisite)

    def test_exhausted_failover_safe_pool_is_all_accounts_unavailable(self):
        outcomes = [
            classify_provider_outcome(message="workspace does not have AI"),
            classify_provider_outcome(http_status=401),
            classify_provider_outcome(http_status=429),
        ]
        decision = decide_provider_pool(outcomes)
        self.assertEqual(decision.code, ProviderOutcomeCode.ALL_ACCOUNTS_UNAVAILABLE)
        self.assertFalse(decision.close_prerequisite)
        self.assertFalse(decision.stop_rotation)
        self.assertEqual(decision.blocker_class, "ACCOUNT_POOL_UNAVAILABLE")

    def test_trust_denial_short_circuits_before_later_green(self):
        outcomes = [
            classify_provider_outcome(message="AI disabled"),
            classify_provider_outcome(message="policy violation"),
            classify_provider_outcome(http_status=200),
        ]
        decision = decide_provider_pool(outcomes)
        self.assertEqual(decision.code, ProviderOutcomeCode.PROVIDER_TRUST_DENIAL)
        self.assertTrue(decision.stop_rotation)
        self.assertFalse(decision.close_prerequisite)

    def test_unknown_failure_fails_closed(self):
        out = classify_provider_outcome(http_status=500, error_code="unexpected")
        self.assertEqual(out.code, ProviderOutcomeCode.UNKNOWN_PROVIDER_FAILURE)
        decision = decide_provider_pool([out])
        self.assertTrue(decision.stop_rotation)
        self.assertEqual(decision.blocker_class, "EXTERNAL_PROVIDER_FAILURE")


if __name__ == "__main__":
    unittest.main()
