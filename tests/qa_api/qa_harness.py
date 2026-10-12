#!/usr/bin/env python3
"""Assertions, fail-loud check runner and evidence writer for OpenShop QA."""
from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from qa_server import runtime_path
from http_client import ApiResponse


class CheckFailure(AssertionError):
    """An acceptance assertion failed. Always red; never downgraded to skip."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def eq(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise CheckFailure(f"{message}: expected={expected!r} actual={actual!r}")


def in_set(value: Any, allowed: Iterable[Any], message: str) -> None:
    allowed_tuple = tuple(allowed)
    if value not in allowed_tuple:
        raise CheckFailure(f"{message}: allowed={allowed_tuple!r} actual={value!r}")


def parse_success(resp: ApiResponse, *, status: int = 200, where: str = "") -> Any:
    prefix = f"{where}: " if where else ""
    if resp.status != status:
        detail = f"error={resp.error_code()} message={resp.error_message()}" if resp.status >= 400 else "unexpected success status"
        raise CheckFailure(f"{prefix}{resp.summary()}, expected HTTP {status}; {detail}")
    if not isinstance(resp.json_body, dict):
        raise CheckFailure(f"{prefix}{resp.summary()}: response is not a JSON object (text[:160]={resp.text[:160]!r})")
    if "error" in resp.json_body:
        raise CheckFailure(f"{prefix}{resp.summary()}: success expected but error envelope returned: {resp.error_code()}")
    if "data" not in resp.json_body:
        raise CheckFailure(f"{prefix}{resp.summary()}: success envelope missing top-level data")
    meta = resp.meta()
    require(meta.get("demo") is True, f"{prefix}{resp.summary()}: meta.demo must be true")
    require(meta.get("currency") == "CNY", f"{prefix}{resp.summary()}: meta.currency must be CNY")
    require(isinstance(meta.get("request_id"), str) and meta.get("request_id"), f"{prefix}{resp.summary()}: meta.request_id missing")
    require(isinstance(meta.get("server_time"), str) and meta.get("server_time"), f"{prefix}{resp.summary()}: meta.server_time missing")
    return resp.json_body["data"]


def parse_error(
    resp: ApiResponse,
    allowed_status: Iterable[int],
    *,
    allowed_codes: Iterable[str] | None = None,
    where: str = "",
) -> dict:
    prefix = f"{where}: " if where else ""
    allowed_tuple = tuple(allowed_status)
    if resp.status not in allowed_tuple:
        raise CheckFailure(
            f"{prefix}{resp.summary()}, expected error status in {allowed_tuple}; "
            f"detail={resp.text[:240]!r}"
        )
    if not isinstance(resp.json_body, dict):
        raise CheckFailure(f"{prefix}{resp.summary()}: error response is not JSON: {resp.text[:240]!r}")
    error = resp.json_body.get("error")
    if not isinstance(error, dict):
        raise CheckFailure(f"{prefix}{resp.summary()}: missing error object")
    code = error.get("code")
    if not isinstance(code, str) or not code:
        raise CheckFailure(f"{prefix}{resp.summary()}: missing error.code")
    if allowed_codes is not None:
        in_set(code, allowed_codes, f"{prefix}{resp.summary()} error.code")
    require(isinstance(error.get("message"), str) and error.get("message"), f"{prefix}{resp.summary()}: missing error.message")
    meta = resp.meta()
    require(meta.get("demo") is True, f"{prefix}{resp.summary()}: error envelope meta.demo must be true")
    require(isinstance(meta.get("request_id"), str) and meta.get("request_id"), f"{prefix}{resp.summary()}: error meta.request_id missing")
    return error


def require_no_secret_fields(payload: Any, where: str) -> None:
    forbidden = {
        "password", "password_hash", "password_salt", "csrf_hash", "token",
        "session_id", "token_hash", "openshop_session",
    }
    if isinstance(payload, dict):
        for key, value in payload.items():
            require(str(key).lower() not in forbidden, f"{where}: forbidden secret field leaked: {key}")
            require_no_secret_fields(value, where)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            require_no_secret_fields(item, f"{where}[{index}]")


@dataclass
class CheckResult:
    check_id: str
    title: str
    status: str
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class SuiteRun:
    suite: str
    worktree: str
    base_url: str
    started_at: str
    checks: list[CheckResult] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def verdict(self) -> str:
        if not self.checks:
            return "FAIL"
        return "PASS" if all(c.status == "PASS" for c in self.checks) else "FAIL"

    def counts(self) -> dict:
        counts = {"PASS": 0, "FAIL": 0}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return counts


class CheckRunner:
    """Runs every check; an exception is printed as FAIL and never skipped."""

    def __init__(self, suite: str, *, worktree: str, base_url: str) -> None:
        self.run = SuiteRun(
            suite=suite,
            worktree=str(worktree),
            base_url=base_url,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def check(self, check_id: str, title: str, fn: Callable[[], None]) -> CheckResult:
        started = time.monotonic()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - acceptance must record and continue
            detail = f"{type(exc).__name__}: {exc}"
            result = CheckResult(check_id, title, "FAIL", detail, (time.monotonic() - started) * 1000.0)
            one_line = " | ".join(detail.splitlines())[:900]
            print(f"{check_id} verdict=FAIL title={title} detail={one_line}", flush=True)
            self.run.checks.append(result)
            return result
        result = CheckResult(check_id, title, "PASS", "", (time.monotonic() - started) * 1000.0)
        print(f"{check_id} verdict=PASS title={title} ms={result.duration_ms:.0f}", flush=True)
        self.run.checks.append(result)
        return result

    def add_extra(self, key: str, value: Any) -> None:
        self.run.extra[key] = value

    def write_summary(self, path: str | Path, *, command: str, server_log: str | None = None, data_dir: str | None = None) -> Path:
        target = runtime_path(path, "summary_path")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "suite": self.run.suite,
            "verdict": self.run.verdict(),
            "rc": 0 if self.run.verdict() == "PASS" else 1,
            "started_at": self.run.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "worktree": self.run.worktree,
            "base_url": self.run.base_url,
            "command": command,
            "python": sys.version,
            "platform": platform.platform(),
            "counts": self.run.counts(),
            "checks": [asdict(c) for c in self.run.checks],
            "server_log": server_log,
            "data_dir": data_dir,
            "extra": self.run.extra,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target

    def print_verdict(self, summary_path: Path) -> None:
        counts = self.run.counts()
        print(
            f"OPENSHOP_API_ACCEPTANCE verdict={self.run.verdict()} "
            f"pass={counts.get('PASS', 0)} fail={counts.get('FAIL', 0)} "
            f"summary={summary_path}",
            flush=True,
        )
