#!/usr/bin/env python3
"""Unit tests for trusted-proxy client IP resolution.

The public edge adds X-Forwarded-For; we only consult it when the immediate TCP
peer is explicitly trusted, otherwise a client-supplied header could rotate
rate-limit buckets. No server or network is started here.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.storefront.server import resolve_client_ip


class TrustedProxyTests(unittest.TestCase):
    def test_untrusted_peer_cannot_spoof_xff(self) -> None:
        self.assertEqual(
            resolve_client_ip("198.51.100.10", "203.0.113.99", "127.0.0.1,192.168.1.183"),
            "198.51.100.10",
        )

    def test_trusted_peer_uses_rightmost_untrusted_address(self) -> None:
        self.assertEqual(
            resolve_client_ip("192.168.1.183", "203.0.113.9", "192.168.1.183,192.168.1.184"),
            "203.0.113.9",
        )
        self.assertEqual(
            resolve_client_ip(
                "192.168.1.184",
                "10.0.0.5, 203.0.113.9, 192.168.1.183",
                "192.168.1.183,192.168.1.184",
            ),
            "203.0.113.9",
        )

    def test_malformed_or_missing_xff_falls_back_to_peer(self) -> None:
        trusted = "127.0.0.1"
        self.assertEqual(resolve_client_ip("127.0.0.1", "", trusted), "127.0.0.1")
        self.assertEqual(resolve_client_ip("127.0.0.1", "unknown, bad, ::gggg", trusted), "127.0.0.1")
        self.assertEqual(resolve_client_ip("not-an-ip", "203.0.113.9", trusted), "not-an-ip")

    def test_env_config_only_when_set(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENSHOP_TRUSTED_PROXIES", None)
            self.assertEqual(resolve_client_ip("127.0.0.1", "203.0.113.9"), "127.0.0.1")
            os.environ["OPENSHOP_TRUSTED_PROXIES"] = "127.0.0.1"
            self.assertEqual(resolve_client_ip("127.0.0.1", "203.0.113.9"), "203.0.113.9")


if __name__ == "__main__":
    unittest.main(verbosity=2)
