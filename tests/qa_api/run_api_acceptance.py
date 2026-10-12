#!/usr/bin/env python3
"""Run the real-backend OpenShop API acceptance suite.

Usage:
  sudo -n -u infra python3 -B tests/qa_api/run_api_acceptance.py \
      --port 8870 --work-dir /hf3fs-jd/data/tmp/openshop/qa/manual-api

The runner starts and stops a real server, uses only a new isolated SQLite DB,
prints per-check PASS/FAIL and a final OPENSHOP_API_ACCEPTANCE verdict line,
and exits non-zero on any failure. No mocks, no skips.
"""
from __future__ import annotations

import argparse
import shlex
import sys
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO_ROOT = HERE.parents[1]
DEFAULT_WORK_ROOT = Path("/hf3fs-jd/data/tmp/openshop/qa")

from api_suite import ApiSuite  # noqa: E402
from qa_harness import CheckFailure, CheckRunner  # noqa: E402
from qa_server import ServerProcess, ServerStartError, runtime_path, tail  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenShop real HTTP API acceptance")
    parser.add_argument("--port", type=int, default=8870)
    parser.add_argument("--work-dir", default="", help="New run directory under /hf3fs-jd/data/tmp/openshop")
    parser.add_argument("--repo", default=str(REPO_ROOT))
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    work_dir = Path(args.work_dir) if args.work_dir else DEFAULT_WORK_ROOT / f"api-{stamp}"
    try:
        work_dir = runtime_path(work_dir, "API work dir")
        work_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"OPENSHOP_API_ACCEPTANCE verdict=FAIL rc=1 reason=unsafe_work_dir {type(exc).__name__}: {exc}", flush=True)
        return 1
    data_dir = work_dir / "data"
    server_log = work_dir / "server.log"
    summary_path = work_dir / "api-summary.json"
    base_url = f"http://127.0.0.1:{args.port}"
    runner = CheckRunner("qa-api", worktree=str(Path(args.repo).resolve()), base_url=base_url)
    command = " ".join(shlex.quote(part) for part in sys.argv)
    print(f"API_RUN work_dir={work_dir} port={args.port} base_url={base_url} data_dir={data_dir}", flush=True)

    server: ServerProcess | None = None
    try:
        server = ServerProcess(
            repo=args.repo,
            port=args.port,
            data_dir=data_dir,
            log_path=server_log,
        ).start()
        print(f"API_SERVER READY log={server_log}", flush=True)
        try:
            suite = ApiSuite(base_url=server.base_url, data_dir=server.data_dir, runner=runner)
            suite.run_all()
        except Exception as exc:  # noqa: BLE001
            print(f"HARNESS API_SUITE_CRASH {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()

            def crash() -> None:
                raise CheckFailure(f"API suite crashed before completing all checks: {type(exc).__name__}: {exc}")

            runner.check("API-999", "suite must complete without harness crash", crash)
    except (ServerStartError, OSError) as exc:
        print(f"API_SERVER_START_FAIL {type(exc).__name__}: {exc}", flush=True)
        if server is not None:
            try:
                print(tail(server_log), flush=True)
            except Exception:  # noqa: BLE001
                pass

        def server_fail() -> None:
            raise CheckFailure(f"real server lifecycle failed: {type(exc).__name__}: {exc}")

        runner.check("API-000", "start real server with isolated data dir", server_fail)
    finally:
        if server is not None:
            server.stop()
            print(f"API_SERVER STOPPED log={server_log}", flush=True)
        try:
            summary = runner.write_summary(
                summary_path,
                command=command,
                server_log=str(server_log),
                data_dir=str(data_dir),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"OPENSHOP_API_ACCEPTANCE verdict=FAIL rc=1 reason=summary_write_failed {type(exc).__name__}: {exc}", flush=True)
            return 1
        runner.print_verdict(summary)
    return 0 if runner.run.verdict() == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
