#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Loopback-only startup health check for the OpenShop systemd template.

Exit codes: 0 healthy, 1 unhealthy, 2 invalid arguments/configuration.
This file is a deployment template. It does not install or start anything.
"""
from __future__ import annotations

import argparse
import http.client
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_URL = "http://127.0.0.1:8780/healthz"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http":
        raise ValueError("health URL must use plain http on loopback")
    if parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("health URL host must be 127.0.0.1 or ::1")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("health URL must not contain credentials")
    if parsed.path != "/healthz" or parsed.query or parsed.fragment:
        raise ValueError("health URL must target /healthz without query or fragment")
    if parsed.port is None or parsed.port <= 0:
        raise ValueError("health URL must include an explicit non-zero port")
    return url


def _healthy(opener: urllib.request.OpenerDirector, url: str, timeout: float) -> bool:
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/plain", "User-Agent": "openshop-healthcheck/0.1"},
        method="GET",
    )
    with opener.open(request, timeout=timeout) as response:
        if int(response.status) != 200:
            return False
        return response.read(1024).strip() == b"ok"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the local OpenShop health endpoint")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.wait_seconds < 0 or args.timeout <= 0:
        print("HEALTHCHECK verdict=FAIL reason=invalid_timeouts")
        return 2
    try:
        url = _validate_url(args.url)
    except ValueError as exc:
        print(f"HEALTHCHECK verdict=FAIL reason=invalid_url detail={exc}")
        return 2

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + args.wait_seconds
    last_reason = "no_attempt"
    while True:
        try:
            if _healthy(opener, url, args.timeout):
                print(f"HEALTHCHECK verdict=PASS url={url}")
                return 0
            last_reason = "unexpected_response"
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            last_reason = type(exc).__name__
        if time.monotonic() >= deadline:
            break
        delay = min(0.5, max(0.0, deadline - time.monotonic()))
        time.sleep(delay)

    print(f"HEALTHCHECK verdict=FAIL reason={last_reason} url={url}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
