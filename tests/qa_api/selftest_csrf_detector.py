#!/usr/bin/env python3
"""Prove the CSRF negative-control detector can go red.

This does NOT replace the live HTTP CSRF check against the real backend. It
feeds the same oracle used by the live suite with a forged "server accepted a
write with a bad token" observation and requires the oracle to fail.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_server import runtime_path  # noqa: E402
from security_oracles import OracleFailure, evaluate_csrf_rejection  # noqa: E402


def require_red(label: str, status: int, code: str | None, *, mutation_applied: bool | None = None) -> dict:
    try:
        evaluate_csrf_rejection(status, code, mutation_applied=mutation_applied, context=label)
    except OracleFailure as exc:
        return {"case": label, "expected": "RED", "observed": "RED", "detail": str(exc)}
    raise AssertionError(f"{label}: detector returned GREEN for an insecure CSRF observation")


def require_green(label: str, status: int, code: str | None, *, mutation_applied: bool | None = None) -> dict:
    try:
        evaluate_csrf_rejection(status, code, mutation_applied=mutation_applied, context=label)
    except OracleFailure as exc:
        raise AssertionError(f"{label}: detector returned RED for a correct CSRF rejection: {exc}") from exc
    return {"case": label, "expected": "GREEN", "observed": "GREEN", "detail": "correct 403 CSRF_FAILED accepted"}


def main() -> int:
    parser = argparse.ArgumentParser(description="CSRF detector self-test")
    parser.add_argument("--summary", default="", help="optional JSON summary path (must be under 3FS tmp)")
    args = parser.parse_args()
    cases = []
    try:
        cases.append(require_red("accepts_wrong_csrf_200", 200, None, mutation_applied=True))
        cases.append(require_red("wrong_error_code", 403, "FORBIDDEN"))
        cases.append(require_red("rejected_but_mutated", 403, "CSRF_FAILED", mutation_applied=True))
        cases.append(require_green("real_csrf_rejection", 403, "CSRF_FAILED", mutation_applied=False))
    except Exception as exc:  # noqa: BLE001
        print(f"SELFTEST csrf_detector verdict=FAIL reason={type(exc).__name__}: {exc}", flush=True)
        summary = {
            "suite": "selftest-csrf-detector",
            "verdict": "FAIL",
            "summary": f"{type(exc).__name__}: {exc}",
            "cases": cases,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    else:
        print(
            "SELFTEST csrf_detector verdict=PASS "
            "detail=forged CSRF bypass observations are RED; real 403 CSRF_FAILED is GREEN",
            flush=True,
        )
        for case in cases:
            print(f"SELFTEST csrf_detector case={case['case']} expected={case['expected']} observed={case['observed']}", flush=True)
        summary = {
            "suite": "selftest-csrf-detector",
            "verdict": "PASS",
            "cases": cases,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    if args.summary:
        target = runtime_path(args.summary, "csrf_selftest_summary")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
