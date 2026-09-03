from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import desktop.combined_acceptance as ca


class F52EndpointIdentityTests(unittest.TestCase):
    def _surface(self, name: str, url: str, protocol: str) -> ca.Surface:
        return ca.Surface(name, url, frozenset({name + "-model"}), protocol)

    def test_localhost_and_ipv4_loopback_same_port_are_duplicate(self):
        surfaces = [self._surface("qwen", "http://localhost:18080", "openai-chat"), self._surface("notion", "http://127.0.0.1:18080", "responses")]
        with self.assertRaisesRegex(ValueError, "surface endpoints must be unique"):
            ca._validate_combined_surfaces(surfaces, require_tools=True)

    def test_equivalent_ipv6_loopback_spellings_same_port_are_duplicate(self):
        surfaces = [self._surface("qwen", "http://[::1]:18080", "openai-chat"), self._surface("notion", "http://[0:0:0:0:0:0:0:1]:18080", "responses")]
        with self.assertRaisesRegex(ValueError, "surface endpoints must be unique"):
            ca._validate_combined_surfaces(surfaces, require_tools=True)

    def test_same_loopback_address_different_ports_remains_valid(self):
        surfaces = [self._surface("qwen", "http://127.0.0.1:18080", "openai-chat"), self._surface("notion", "http://127.0.0.1:18081", "responses")]
        ca._validate_combined_surfaces(surfaces, require_tools=True)

    def test_nonfinite_timing_fails_before_any_probe(self):
        surfaces = [self._surface("qwen", "http://127.0.0.1:18080", "openai-chat"), self._surface("notion", "http://127.0.0.1:18081", "responses")]
        for field, values in {"duration": [math.nan, math.inf, -math.inf], "interval": [math.nan, math.inf, -math.inf], "timeout": [math.nan, math.inf, -math.inf]}.items():
            for bad in values:
                kwargs = {"duration": 0.0, "interval": 0.1, "timeout": 1.0, "require_tools": True}
                kwargs[field] = bad
                with self.subTest(field=field, value=bad), patch.object(ca, "probe_models") as probe:
                    with self.assertRaisesRegex(ValueError, "invalid timing"):
                        ca.run_soak(surfaces, **kwargs)
                    probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
