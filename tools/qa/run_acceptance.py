#!/usr/bin/env python3
"""OpenShop one-command acceptance orchestrator.

Runs, in order:
  1. QAS security-detector self-tests (CSRF stays-red probe, Chromium external
     request stays-red probe);
  2. tests/qa_api against a freshly started real server with an isolated DB;
  3. Chromium E2E against a second real server with another isolated DB.

All evidence goes below /hf3fs-jd/data/tmp/openshop/; no product data or
production service is touched. Missing/blocked evidence is a FAIL. No skips and
no automatic retries of a failed suite.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
QA_API_DIR = REPO_ROOT / "tests" / "qa_api"
sys.path.insert(0, str(QA_API_DIR))
from qa_server import RUNTIME_ROOT, ServerProcess, ServerStartError, runtime_path, tail  # noqa: E402

DEFAULT_OUT_ROOT = Path("/hf3fs-jd/data/tmp/openshop/qa")
DEFAULT_PLAYWRIGHT = "/opt/dsh/app-master-0.1.6-alpha.2/node_modules/.pnpm/node_modules/playwright/index.mjs"
DEFAULT_CHROMIUM = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
BROWSER_STAGE_IDS = [
    "E2E-01-home",
    "E2E-02-search",
    "E2E-03-product",
    "E2E-04-login-cart",
    "E2E-05-checkout",
    "E2E-06-pay",
    "E2E-07-seller",
    "E2E-08-confirm",
    "E2E-09-review",
    "E2E-10-mobile",
    "E2E-11-final-audit",
]
API_REQUIRED_IDS = {
    "API-003", "API-004", "API-005", "API-010", "API-035", "API-036",
    "API-038", "API-039", "API-040", "API-041", "API-051", "API-052",
    "API-054", "API-060",
}
API_MIN_CHECKS = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path: Path, limit: int = 4_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cmd_string(cmd: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


class Logger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.log_path, "a", encoding="utf-8")

    def line(self, message: str = "") -> None:
        print(message, flush=True)
        self._handle.write(message + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def base_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    return env


def run_logged(cmd: list[str], log_path: Path, timeout_seconds: int, *, cwd: Path = REPO_ROOT) -> dict[str, Any]:
    """Run a child once, preserve raw stdout+stderr, kill its group on timeout."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    rc: int
    error = ""
    with open(log_path, "w", encoding="utf-8") as log_handle:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=base_env(),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            log_handle.write(f"CHILD_SPAWN_FAILED {type(exc).__name__}: {exc}\n")
            return {
                "rc": 127,
                "timed_out": False,
                "log_path": str(log_path),
                "duration_seconds": round(time.monotonic() - started, 2),
                "command": cmd_string(cmd),
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            rc = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            error = f"timeout_after_{timeout_seconds}s"
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                error += ";could_not_reap"
            rc = -9
    return {
        "rc": int(rc),
        "timed_out": timed_out,
        "log_path": str(log_path),
        "duration_seconds": round(time.monotonic() - started, 2),
        "command": cmd_string(cmd),
        "error": error,
    }


def last_marker(log_text: str, prefix: str) -> str:
    found = [line for line in log_text.splitlines() if prefix in line]
    return found[-1] if found else ""


def validate_csrf_selftest(summary: Any, log_text: str, outcome: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if outcome["rc"] != 0:
        errors.append(f"csrf selftest rc={outcome['rc']}")
    if "SELFTEST csrf_detector verdict=PASS" not in log_text:
        errors.append("csrf selftest log has no PASS marker")
    if not isinstance(summary, dict):
        errors.append("csrf selftest summary missing or invalid JSON")
        return errors
    if summary.get("verdict") != "PASS":
        errors.append(f"csrf selftest summary verdict={summary.get('verdict')!r}")
    cases = summary.get("cases") or []
    red_ok = any(isinstance(c, dict) and c.get("observed") == "RED" and "wrong_csrf" in str(c.get("case")) for c in cases)
    green_ok = any(isinstance(c, dict) and c.get("observed") == "GREEN" for c in cases)
    if not red_ok:
        errors.append("csrf selftest did not record a caught-red forged CSRF observation")
    if not green_ok:
        errors.append("csrf selftest did not record a green control")
    return errors


def validate_external_selftest(summary: Any, log_text: str, outcome: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if outcome["rc"] != 0:
        errors.append(f"external-request selftest rc={outcome['rc']}")
    if "SELFTEST external_request_detector verdict=PASS" not in log_text:
        errors.append("external-request selftest log has no PASS marker")
    if not isinstance(summary, dict):
        errors.append("external-request selftest summary missing or invalid JSON")
        return errors
    if summary.get("verdict") != "PASS":
        errors.append(f"external-request selftest summary verdict={summary.get('verdict')!r}")
    cases = summary.get("cases") or []
    external_red = any(
        isinstance(c, dict) and c.get("case") == "synthetic_external_request" and c.get("observed") == "RED"
        for c in cases
    )
    clean_green = any(
        isinstance(c, dict) and c.get("case") == "clean_local_page" and c.get("observed") == "GREEN"
        for c in cases
    )
    if not external_red:
        errors.append("external-request selftest did not catch the synthetic external request as RED")
    if not clean_green:
        errors.append("external-request selftest clean page control was not GREEN")
    return errors


def validate_api(outcome: dict[str, Any], summary_path: Path, server_log: Path, log_path: Path) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    summary = read_json(summary_path) if summary_path.is_file() else None
    log_text = read_text(log_path)
    marker = last_marker(log_text, "OPENSHOP_API_ACCEPTANCE")
    if outcome["rc"] != 0:
        errors.append(f"API runner rc={outcome['rc']} timed_out={outcome['timed_out']}")
    if "OPENSHOP_API_ACCEPTANCE verdict=PASS" not in marker:
        errors.append(f"API raw verdict not PASS: {marker or '<missing>'}")
    if not isinstance(summary, dict):
        errors.append(f"API summary missing or invalid: {summary_path}")
        return errors, None
    if summary.get("verdict") != "PASS":
        errors.append(f"API summary verdict={summary.get('verdict')!r}")
    counts = summary.get("counts") or {}
    if counts.get("FAIL") not in (0, None):
        errors.append(f"API summary contains FAIL checks: {counts.get('FAIL')}")
    checks = summary.get("checks") or []
    ids = [c.get("check_id") for c in checks if isinstance(c, dict)]
    statuses = {c.get("check_id"): c.get("status") for c in checks if isinstance(c, dict)}
    if not checks:
        errors.append("API summary has zero checks")
    if len(ids) != len(set(ids)):
        errors.append("API summary has duplicate check ids")
    bad_checks = [(check_id, statuses.get(check_id)) for check_id in ids if statuses.get(check_id) != "PASS"]
    if bad_checks:
        errors.append(f"API summary contains non-PASS checks: {bad_checks[:8]!r}")
    for check_id in sorted(API_REQUIRED_IDS):
        if statuses.get(check_id) != "PASS":
            errors.append(f"required API check {check_id} not PASS: {statuses.get(check_id)!r}")
    extra = summary.get("extra") or {}
    declared = extra.get("declared_checks")
    declared_ids = extra.get("declared_check_ids")
    if not isinstance(declared, int):
        errors.append("API summary missing extra.declared_checks")
    elif declared < API_MIN_CHECKS:
        errors.append(f"API declared only {declared} checks (<{API_MIN_CHECKS})")
    elif declared != len(checks):
        errors.append(f"API declared {declared} checks but summary has {len(checks)}")
    if not isinstance(declared_ids, list) or set(declared_ids) != set(ids):
        errors.append("API extra.declared_check_ids does not match executed check ids")
    if not server_log.is_file() or server_log.stat().st_size == 0:
        errors.append(f"API server log missing/empty: {server_log}")
    else:
        server_text = read_text(server_log)
        if "OPENSHOP_READY" not in server_text:
            errors.append("API server log lacks OPENSHOP_READY")
        if "OPENSHOP_STOPPED" not in server_text:
            errors.append("API server log lacks OPENSHOP_STOPPED clean shutdown")
    return errors, summary


def validate_browser(outcome: dict[str, Any], summary_path: Path, server_log: Path, log_path: Path, out_dir: Path) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    summary = read_json(summary_path) if summary_path.is_file() else None
    log_text = read_text(log_path)
    marker = last_marker(log_text, "OPENSHOP_BROWSER")
    if outcome["rc"] != 0:
        errors.append(f"browser runner rc={outcome['rc']} timed_out={outcome['timed_out']}")
    if "OPENSHOP_BROWSER verdict=PASS" not in marker:
        errors.append(f"browser raw verdict not PASS: {marker or '<missing>'}")
    if not isinstance(summary, dict):
        errors.append(f"browser summary missing or invalid: {summary_path}")
        return errors, None
    if summary.get("verdict") != "PASS" or summary.get("rc") != 0:
        errors.append(f"browser summary verdict/rc={summary.get('verdict')!r}/{summary.get('rc')!r}")
    if summary.get("fatal_error"):
        errors.append("browser summary contains fatal_error")
    if summary.get("fixture_error"):
        errors.append("browser summary contains fixture_error")
    stats = summary.get("stats") or {}
    if stats.get("PASS") != len(BROWSER_STAGE_IDS) or stats.get("FAIL") != 0 or stats.get("BLOCKED") != 0:
        errors.append(f"browser stage stats not all green: {stats!r}")
    stages = summary.get("stages") or []
    stage_by_id = {stage.get("id"): stage for stage in stages if isinstance(stage, dict)}
    if len(stages) != len(BROWSER_STAGE_IDS):
        errors.append(f"browser expected {len(BROWSER_STAGE_IDS)} stage records, got {len(stages)}")
    for stage_id in BROWSER_STAGE_IDS:
        stage = stage_by_id.get(stage_id)
        if not stage:
            errors.append(f"browser stage missing: {stage_id}")
        elif stage.get("status") != "PASS":
            errors.append(f"browser stage {stage_id} status={stage.get('status')!r}")
    audit = stage_by_id.get("E2E-11-final-audit")
    if not audit or audit.get("status") != "PASS":
        errors.append("browser final audit stage is not PASS")
    if summary.get("console_errors"):
        errors.append(f"browser summary has {len(summary['console_errors'])} console errors")
    if summary.get("page_errors"):
        errors.append("browser summary has page errors")
    if summary.get("external_requests"):
        errors.append("browser summary has external requests")
    if summary.get("audit_issues"):
        errors.append("browser summary has non-empty audit_issues")
    api_calls = summary.get("api_calls") or []
    api_errors = [call for call in api_calls if isinstance(call, dict) and int(call.get("status") or 0) >= 400]
    if api_errors:
        errors.append(f"browser observed {len(api_errors)} API responses >=400")
    if summary.get("simulated_payment_seen") is not True:
        errors.append("browser did not record visible simulated-payment disclosure")
    try:
        viewport = int(summary.get("mobile_viewport_width"))
    except (TypeError, ValueError):
        viewport = -1
    if abs(viewport - 390) > 1:
        errors.append(f"browser mobile viewport {viewport} != 390")
    for key in ("order_no", "tracking_no", "buyer_order_path"):
        if not summary.get(key):
            errors.append(f"browser summary missing {key}")
    required = summary.get("required_screenshots") or []
    shots = summary.get("screenshots") or []
    shot_names = {shot.get("name"): shot for shot in shots if isinstance(shot, dict)}
    if len(required) < 2:
        errors.append("browser summary lists fewer than 2 required screenshots")
    for name in required:
        record = shot_names.get(name)
        if not record:
            errors.append(f"browser required screenshot record missing: {name}")
            continue
        record_path = Path(str(record.get("path") or ""))
        try:
            resolved = record_path.resolve()
            resolved.relative_to(out_dir.resolve())
        except (OSError, ValueError):
            errors.append(f"browser screenshot path escapes evidence dir: {record_path}")
            continue
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            errors.append(f"browser screenshot missing/empty: {record_path}")
    mobile_shots = [shot for shot in shots if isinstance(shot, dict) and "390" in str(shot.get("name"))]
    if not mobile_shots:
        errors.append("browser has no 390px mobile screenshot")
    if not server_log.is_file() or server_log.stat().st_size == 0:
        errors.append(f"browser server log missing/empty: {server_log}")
    else:
        server_text = read_text(server_log)
        if "OPENSHOP_READY" not in server_text:
            errors.append("browser server log lacks OPENSHOP_READY")
        if "OPENSHOP_STOPPED" not in server_text:
            errors.append("browser server log lacks OPENSHOP_STOPPED clean shutdown")
    return errors, summary


def run_selftests(run_dir: Path, *, playwright: str, chromium: str, logger: Logger) -> dict[str, Any]:
    logs_dir = run_dir / "logs"
    selftests_dir = run_dir / "selftests"
    csrf_summary_path = selftests_dir / "csrf-detector-summary.json"
    external_dir = selftests_dir / "external-request"
    external_summary_path = external_dir / "summary.json"
    csrf_cmd = [
        sys.executable, "-B", str(REPO_ROOT / "tests/qa_api/selftest_csrf_detector.py"),
        "--summary", str(csrf_summary_path),
    ]
    csrf_outcome = run_logged(csrf_cmd, logs_dir / "selftest-csrf.log", 60)
    csrf_log_text = read_text(Path(csrf_outcome["log_path"]))
    csrf_summary = read_json(csrf_summary_path)
    csrf_errors = validate_csrf_selftest(csrf_summary, csrf_log_text, csrf_outcome)
    csrf_status = "PASS" if not csrf_errors else "FAIL"
    logger.line(
        f"SUITE selftest_csrf verdict={csrf_status} rc={csrf_outcome['rc']} "
        f"log={csrf_outcome['log_path']} summary={csrf_summary_path}"
    )

    node = shutil.which("node") or "/usr/local/bin/node"
    if not Path(node).is_file():
        external_outcome = {
            "rc": 127, "timed_out": False, "log_path": str(logs_dir / "selftest-external.log"),
            "duration_seconds": 0, "command": "<node missing>", "error": "node executable missing",
        }
        (logs_dir / "selftest-external.log").write_text("node executable missing\n", encoding="utf-8")
        external_log_text = ""
    else:
        external_cmd = [
            node, str(REPO_ROOT / "tests/browser/browser_selftest.mjs"),
            "--out-dir", str(external_dir),
            "--playwright", playwright,
            "--chromium", chromium,
        ]
        external_outcome = run_logged(external_cmd, logs_dir / "selftest-external.log", 120)
        external_log_text = read_text(Path(external_outcome["log_path"]))
    external_summary = read_json(external_summary_path)
    external_errors = validate_external_selftest(external_summary, external_log_text, external_outcome)
    external_status = "PASS" if not external_errors else "FAIL"
    logger.line(
        f"SUITE selftest_external verdict={external_status} rc={external_outcome['rc']} "
        f"log={external_outcome['log_path']} summary={external_summary_path}"
    )
    return {
        "csrf": {
            "status": csrf_status, "errors": csrf_errors, "outcome": csrf_outcome,
            "summary_path": str(csrf_summary_path),
        },
        "external": {
            "status": external_status, "errors": external_errors, "outcome": external_outcome,
            "summary_path": str(external_summary_path),
        },
    }


def run_api_suite(run_dir: Path, *, api_port: int, timeout_seconds: int, logger: Logger) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    logs_dir = run_dir / "logs"
    api_work_dir = run_dir / "api"
    api_log_path = logs_dir / "api-run.log"
    summary_path = api_work_dir / "api-summary.json"
    server_log = api_work_dir / "server.log"
    cmd = [
        sys.executable, "-B", str(REPO_ROOT / "tests/qa_api/run_api_acceptance.py"),
        "--port", str(api_port), "--work-dir", str(api_work_dir),
    ]
    logger.line("SUITE api_command " + cmd_string(cmd))
    outcome = run_logged(cmd, api_log_path, timeout_seconds)
    errors, summary = validate_api(outcome, summary_path, server_log, api_log_path)
    status = "PASS" if not errors else "FAIL"
    raw_marker = last_marker(read_text(api_log_path), "OPENSHOP_API_ACCEPTANCE")
    if raw_marker:
        logger.line("RAW " + raw_marker)
    counts = (summary or {}).get("counts") or {}
    logger.line(
        "SUITE api verdict=" + status + f" rc={outcome['rc']} counts={json.dumps(counts, ensure_ascii=False)} "
        f"log={outcome['log_path']} summary={summary_path}"
    )
    return outcome, summary, ([] if status == "PASS" else errors)


def run_browser_suite(
    run_dir: Path, *, browser_port: int, playwright: str, chromium: str, timeout_seconds: int, logger: Logger,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    logs_dir = run_dir / "logs"
    browser_out = run_dir / "browser"
    browser_log = logs_dir / "browser-run.log"
    server_log = logs_dir / "browser-server.log"
    data_dir = run_dir / "browser-data"
    summary_path = browser_out / "browser-summary.json"
    base_url = f"http://127.0.0.1:{browser_port}"
    node = shutil.which("node") or "/usr/local/bin/node"
    errors: list[str] = []
    outcome: dict[str, Any]
    node_ran = False
    server: ServerProcess | None = None
    if not Path(node).is_file():
        errors.append(f"node executable missing: {node}")
    if browser_out.exists():
        errors.append(f"browser out dir unexpectedly exists before run: {browser_out}")
    if not errors:
        try:
            server = ServerProcess(
                repo=REPO_ROOT, port=browser_port, data_dir=data_dir, log_path=server_log
            ).start()
            logger.line(f"SUITE browser_server READY log={server_log} data_dir={data_dir}")
        except (ServerStartError, OSError) as exc:
            errors.append(f"browser real server failed to start: {type(exc).__name__}: {exc}")
            server = None
    if not errors:
        cmd = [
            node, str(REPO_ROOT / "tests/browser/e2e_acceptance.mjs"),
            "--base-url", base_url,
            "--out-dir", str(browser_out),
            "--playwright", playwright,
            "--chromium", chromium,
        ]
        logger.line("SUITE browser_command " + cmd_string(cmd))
        outcome = run_logged(cmd, browser_log, timeout_seconds)
        node_ran = True
        if server is not None:
            server.stop()
            server = None
        logger.line(f"BROWSER_SERVER STOPPED log={server_log}")
    else:
        outcome = {
            "rc": 1, "timed_out": False, "log_path": str(browser_log),
            "duration_seconds": 0, "command": "", "error": "; ".join(errors),
        }
        browser_log.write_text("BROWSER_BOOTSTRAP_FAIL " + "; ".join(errors) + "\n", encoding="utf-8")
    if server is not None:
        server.stop()
    validation_errors, summary = validate_browser(outcome, summary_path, server_log, browser_log, browser_out)
    all_errors = errors + validation_errors
    status = "PASS" if not all_errors else "FAIL"
    raw_marker = last_marker(read_text(browser_log), "OPENSHOP_BROWSER")
    if raw_marker:
        logger.line("RAW " + raw_marker)
    stats = (summary or {}).get("stats") or {}
    logger.line(
        "SUITE browser verdict=" + status + f" rc={outcome['rc']} stats={json.dumps(stats, ensure_ascii=False)} "
        f"log={outcome['log_path']} summary={summary_path} node_ran={node_ran}"
    )
    return outcome, summary, ([] if status == "PASS" else all_errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenShop full real-backend acceptance")
    parser.add_argument("--api-port", type=int, default=8870)
    parser.add_argument("--browser-port", type=int, default=8871)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--playwright", default=DEFAULT_PLAYWRIGHT)
    parser.add_argument("--chromium", default=DEFAULT_CHROMIUM)
    parser.add_argument("--timeout-api", type=int, default=300)
    parser.add_argument("--timeout-browser", type=int, default=420)
    args = parser.parse_args()
    if args.api_port == args.browser_port:
        print("OPENSHOP_ACCEPTANCE verdict=FAIL rc=1 reason=api_port_equals_browser_port", flush=True)
        return 1
    try:
        out_root = runtime_path(args.out_root, "out_root")
        out_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        run_dir = out_root / f"acceptance-{stamp}-{os.getpid()}"
        if run_dir.exists():
            print(f"OPENSHOP_ACCEPTANCE verdict=FAIL rc=1 reason=run_dir_exists path={run_dir}", flush=True)
            return 1
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"OPENSHOP_ACCEPTANCE verdict=FAIL rc=1 reason=unsafe_run_dir {type(exc).__name__}: {exc}", flush=True)
        return 1

    logger = Logger(run_dir / "logs" / "orchestrator.log")
    started_at = now_iso()
    started = time.monotonic()
    api_outcome: dict[str, Any] | None = None
    api_summary: dict[str, Any] | None = None
    browser_outcome: dict[str, Any] | None = None
    browser_summary: dict[str, Any] | None = None
    api_errors: list[str] = []
    browser_errors: list[str] = []
    selftests: dict[str, Any] = {}
    validation_errors: list[str] = []
    summary_path = run_dir / "acceptance-summary.json"
    verdict = "FAIL"
    rc = 1
    try:
        logger.line(
            f"OPENSHOP_ACCEPTANCE_START run_dir={run_dir} repo={REPO_ROOT} "
            f"api_port={args.api_port} browser_port={args.browser_port}"
        )
        logger.line(
            "CONTRACT selftests=csrf+external api_checks=real-http browser=real-chromium "
            "data_root=/hf3fs-jd/data/tmp/openshop no_mocks=1"
        )
        for required_path in (
            REPO_ROOT / "tests/qa_api/run_api_acceptance.py",
            REPO_ROOT / "tests/browser/e2e_acceptance.mjs",
            REPO_ROOT / "tests/browser/browser_selftest.mjs",
            REPO_ROOT / "tests/qa_api/selftest_csrf_detector.py",
        ):
            if not required_path.is_file():
                validation_errors.append(f"required QA file missing: {required_path}")
        if not Path(args.playwright).is_file():
            validation_errors.append(f"Playwright module missing: {args.playwright}")
        if not Path(args.chromium).is_file():
            validation_errors.append(f"Chromium executable missing: {args.chromium}")
        selftests = run_selftests(
            run_dir, playwright=args.playwright, chromium=args.chromium, logger=logger
        )
        if selftests["csrf"]["status"] != "PASS":
            validation_errors.extend(["csrf selftest red"] + selftests["csrf"]["errors"])
        if selftests["external"]["status"] != "PASS":
            validation_errors.extend(["external detector red"] + selftests["external"]["errors"])
        api_outcome, api_summary, api_errors = run_api_suite(
            run_dir, api_port=args.api_port, timeout_seconds=args.timeout_api, logger=logger
        )
        browser_outcome, browser_summary, browser_errors = run_browser_suite(
            run_dir, browser_port=args.browser_port, playwright=args.playwright, chromium=args.chromium,
            timeout_seconds=args.timeout_browser, logger=logger,
        )
        validation_errors.extend(api_errors)
        validation_errors.extend(browser_errors)
        if not selftests.get("csrf", {}).get("status") == "PASS":
            validation_errors.append("required harness self-test failed: csrf detector")
        if not selftests.get("external", {}).get("status") == "PASS":
            validation_errors.append("required harness self-test failed: external request detector")
    except Exception as exc:  # noqa: BLE001
        detail = f"orchestrator crashed: {type(exc).__name__}: {exc}"
        validation_errors.append(detail)
        logger.line("ORCHESTRATOR_CRASH " + detail)
        logger.line(traceback.format_exc())
    finished_at = now_iso()
    if not validation_errors:
        verdict = "PASS"
        rc = 0
    api_counts = (api_summary or {}).get("counts") or {}
    browser_stats = (browser_summary or {}).get("stats") or {}
    payload = {
        "suite": "openshop-acceptance",
        "verdict": verdict,
        "rc": rc,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(time.monotonic() - started, 2),
        "run_dir": str(run_dir),
        "repo": str(REPO_ROOT),
        "ports": {"api": args.api_port, "browser": args.browser_port},
        "contracts": {
            "selftest_csrf_red_required": True,
            "selftest_external_red_required": True,
            "api_real_http": True,
            "browser_real_chromium": True,
            "data_under_3fs_tmp": True,
        },
        "selftests": {
            "csrf": {k: v for k, v in (selftests.get("csrf") or {}).items()},
            "external": {k: v for k, v in (selftests.get("external") or {}).items()},
        },
        "api": {
            "status": "FAIL" if api_errors else "PASS",
            "errors": api_errors,
            "outcome": api_outcome,
            "counts": api_counts,
            "summary_path": str(run_dir / "api" / "api-summary.json"),
            "server_log": str(run_dir / "api" / "server.log"),
        },
        "browser": {
            "status": "FAIL" if browser_errors else "PASS",
            "errors": browser_errors,
            "outcome": browser_outcome,
            "stats": browser_stats,
            "summary_path": str(run_dir / "browser" / "browser-summary.json"),
            "server_log": str(run_dir / "logs" / "browser-server.log"),
        },
        "validation_errors": validation_errors,
    }
    try:
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.line(f"ACCEPTANCE_SUMMARY {summary_path}")
    except OSError as exc:
        logger.line(f"ACCEPTANCE_SUMMARY_WRITE_FAIL {type(exc).__name__}: {exc}")
        rc = 1
        verdict = "FAIL"
    if validation_errors:
        for index, error in enumerate(validation_errors, start=1):
            logger.line(f"VALIDATION {index}: {error}")
    logger.line(
        "OPENSHOP_ACCEPTANCE verdict=" + verdict + " rc=" + str(rc) +
        " selftest_csrf=" + selftests.get("csrf", {}).get("status", "FAIL") +
        " selftest_external=" + selftests.get("external", {}).get("status", "FAIL") +
        " api=" + ("PASS" if not api_errors else "FAIL") +
        " browser=" + ("PASS" if not browser_errors else "FAIL") +
        f" api_pass={api_counts.get('PASS', 0)} api_fail={api_counts.get('FAIL', 0)}" +
        f" browser_pass={browser_stats.get('PASS', 0)} browser_fail={browser_stats.get('FAIL', 0)}" +
        f" run_dir={run_dir} summary={summary_path}"
    )
    logger.line(f"EVIDENCE {run_dir}")
    logger.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
