#!/usr/bin/env python3
"""Start a fresh OpenShop server and smoke the session/auth envelope.

This is intentionally narrower than the full API suite: it proves the server
boots, the seeded database is readable, guest session + CSRF work, and a demo
login changes identity. It never deletes an existing data directory.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    if args.data_dir:
        data_dir = Path(args.data_dir)
        if data_dir.exists() and any(data_dir.iterdir()):
            print(f"OPENSHOP_SMOKE verdict=FAIL reason=data_dir_not_empty path={data_dir}", flush=True)
            return 2
    else:
        data_dir = Path("/hf3fs-jd/data/tmp/openshop/smoke") / stamp
    data_dir.mkdir(parents=True, exist_ok=False)
    log_dir = Path("/hf3fs-jd/data/tmp/openshop/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"smoke-{stamp}.log"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    proc = subprocess.Popen(
        [sys.executable, "-u", "-B", str(ROOT / "apps/storefront/server.py"), "--host", "127.0.0.1",
         "--port", str(args.port), "--data-dir", str(data_dir)],
        cwd=str(ROOT), stdout=open(log_path, "w", encoding="utf-8"), stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{args.port}"
    try:
        deadline = time.monotonic() + 35
        last_error = ""
        health = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                last_error = f"server_exited_{proc.returncode}"
                break
            try:
                with opener.open(base + "/api/health", timeout=1.5) as resp:
                    health = json.loads(resp.read().decode())
                if health.get("ok"):
                    break
            except Exception as exc:
                last_error = type(exc).__name__
            time.sleep(0.2)
        if not health or not health.get("ok"):
            print(f"OPENSHOP_SMOKE verdict=FAIL reason=not_ready detail={last_error} log={log_path}", flush=True)
            return 1

        with opener.open(base + "/api/session", timeout=4) as resp:
            session_env = json.loads(resp.read().decode())
        session = session_env.get("data") or {}
        csrf = session.get("csrf", "")
        if not csrf or session.get("user") is not None:
            print(f"OPENSHOP_SMOKE verdict=FAIL reason=guest_session csrf={bool(csrf)} log={log_path}", flush=True)
            return 1
        print(f"GUEST_SESSION verdict=PASS csrf_len={len(csrf)} cookie_count={len(list(jar))}", flush=True)

        req = urllib.request.Request(
            base + "/api/auth/demo",
            data=json.dumps({"role": "buyer"}).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-OpenShop-CSRF": csrf, "Connection": "close"},
        )
        with opener.open(req, timeout=6) as resp:
            demo_env = json.loads(resp.read().decode())
        demo = demo_env.get("data") or {}
        user = demo.get("user") or {}
        if user.get("role") != "buyer" or not demo.get("csrf"):
            print(f"OPENSHOP_SMOKE verdict=FAIL reason=demo_login detail={demo_env} log={log_path}", flush=True)
            return 1
        print(f"DEMO_LOGIN verdict=PASS user={user.get('handle')} role={user.get('role')} cart={demo.get('cart_count')}", flush=True)

        req = urllib.request.Request(
            base + "/api/auth/logout",
            data=json.dumps({}).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-OpenShop-CSRF": demo["csrf"], "Connection": "close"},
        )
        with opener.open(req, timeout=6) as resp:
            logout_env = json.loads(resp.read().decode())
        if (logout_env.get("data") or {}).get("is_guest") is not True:
            print(f"OPENSHOP_SMOKE verdict=FAIL reason=logout detail={logout_env} log={log_path}", flush=True)
            return 1
        print(f"LOGOUT_GUEST verdict=PASS csrf_present={bool((logout_env.get('data') or {}).get('csrf'))}", flush=True)
        print(f"OPENSHOP_SMOKE verdict=PASS data_dir={data_dir} log={log_path}", flush=True)
        return 0
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=4)


if __name__ == "__main__":
    raise SystemExit(main())
