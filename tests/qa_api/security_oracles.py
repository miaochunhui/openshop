#!/usr/bin/env python3
"""Security oracle primitives shared by live checks and their self-tests."""
from __future__ import annotations


class OracleFailure(AssertionError):
    """A security invariant was not met."""


def evaluate_csrf_rejection(
    status: int,
    error_code: str | None,
    *,
    mutation_applied: bool | None = None,
    context: str = "csrf-negative",
) -> None:
    """Raise unless the server actually rejected a write carrying a bad CSRF token.

    The live API suite uses this detector. selftest_csrf_detector.py feeds it a
    forged HTTP-200 observation and requires it to go red.
    """
    if status != 403:
        raise OracleFailure(
            f"{context}: unsafe write was not rejected with HTTP 403 (got {status}, code={error_code!r})"
        )
    if error_code != "CSRF_FAILED":
        raise OracleFailure(
            f"{context}: HTTP 403 carried wrong error code {error_code!r}, expected CSRF_FAILED"
        )
    if mutation_applied is True:
        raise OracleFailure(
            f"{context}: server returned CSRF_FAILED but the protected mutation was still applied"
        )
