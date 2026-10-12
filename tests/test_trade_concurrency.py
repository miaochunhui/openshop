#!/usr/bin/env python3
"""Deterministic real-HTTP concurrency acceptance for the trade domain.

Runs three independent rounds on loopback port 8881 by default. Every round
starts the real stdlib server against a fresh SQLite data dir. Buyers, their
independent sessions/CSRF values and the scarce stock/coupon/group fixtures are
created through the project schema before the concurrent HTTP phases. The
phases use ThreadPoolExecutor plus a Barrier; no sleep is used as a correctness
assertion.

Environment:
    OPENSHOP_CONCURRENCY_TEST_PORT       loopback port (default 8881)
    OPENSHOP_CONCURRENCY_EVIDENCE_ROOT  durable evidence directory
                                        (default $TMPDIR/openshop-trade-concurrency)
    OPENSHOP_TMP_BASE                   optional base for default evidence root

Evidence includes server logs, DB dirs, raw scenario JSON, console log and the
first failure traceback. The test never pushes, installs services or touches
network entry points.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.commerce import db as dbutil  # noqa: E402

PORT = int(os.environ.get("OPENSHOP_CONCURRENCY_TEST_PORT", "8881"))
BASE_URL = f"http://127.0.0.1:{PORT}"
_TMP_BASE = Path(os.environ.get("OPENSHOP_TMP_BASE", tempfile.gettempdir()))
EVIDENCE_ROOT = Path(
    os.environ.get(
        "OPENSHOP_CONCURRENCY_EVIDENCE_ROOT",
        str(_TMP_BASE / "openshop-trade-concurrency"),
    )
)
ROUNDS = 3
HTTP_TIMEOUT = 35
BARRIER_TIMEOUT = 30


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EvidenceLogger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = path.open("w", encoding="utf-8")

    def line(self, message: str) -> None:
        print(message, flush=True)
        self._handle.write(message + "\n")
        self._handle.flush()

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:
            pass


class HttpResponse:
    def __init__(self, status: int, payload, text: str, transport_error: str | None = None):
        self.status = status
        self.payload = payload
        self.text = text
        self.transport_error = transport_error

    @property
    def data(self):
        if isinstance(self.payload, dict):
            return self.payload.get("data")
        return None

    @property
    def error_code(self) -> str:
        if isinstance(self.payload, dict):
            error = self.payload.get("error")
            if isinstance(error, dict):
                return str(error.get("code", ""))
        return ""

    @property
    def error_message(self) -> str:
        if isinstance(self.payload, dict):
            error = self.payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message", ""))
        return ""


class WorkerClient:
    """One explicitly owned cookie + CSRF pair, never shared across workers."""

    def __init__(self, *, cookie: str | None = None, csrf: str | None = None):
        self.cookie = cookie
        self.csrf = csrf

    def request(self, method: str, path: str, body=None) -> HttpResponse:
        headers = {"Accept": "application/json", "Connection": "close"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if method.upper() != "GET" and self.csrf:
            headers["X-OpenShop-CSRF"] = self.csrf
        if self.cookie:
            headers["Cookie"] = self.cookie
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=HTTP_TIMEOUT)
        try:
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            status = int(response.status)
        finally:
            conn.close()
        text = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = None
        return HttpResponse(status, payload, text)


class Fixture:
    """Schema-level fixture users/sessions/coupons. No API or password is needed.

    Sessions are inserted with the same token hash contract used by auth.py, so
    concurrent HTTP requests still traverse the real cookie + CSRF middleware.
    """

    def __init__(self, db_path: Path, prefix: str):
        self.db_path = db_path
        self.prefix = prefix
        self.counter = 0
        self.lock = threading.Lock()

    def connect(self):
        return dbutil.connect(self.db_path)

    def _next_label(self) -> int:
        with self.lock:
            self.counter += 1
            return self.counter

    def _iso(self, delta: timedelta | None = None) -> str:
        return dbutil.iso(dbutil.utc_now() + (delta or timedelta()))

    def create_buyers(self, tag: str, count: int, sessions_per_user: int = 1):
        if sessions_per_user < 1:
            raise ValueError("sessions_per_user must be >= 1")
        created = []
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = self._iso()
            for index in range(count):
                number = self._next_label()
                handle = f"c{self.prefix[:4]}{number:04d}{''.join(ch for ch in tag if ch.isalnum())[:5]}"
                handle = handle[:30]
                display_name = f"并发买家{number:03d}"
                email = f"{handle}@concurrency.test"
                conn.execute(
                    """INSERT INTO users(handle,display_name,email,role,avatar,member_since,created_at)
                       VALUES(?,?,?,'buyer',?,?,?)""",
                    (handle, display_name, email, "/web/assets/brand/avatar-lin.svg", "2026-01-01", now),
                )
                user_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                phone = f"139{number:08d}"[:11]
                cur = conn.execute(
                    """INSERT INTO addresses(user_id,label,receiver,phone,province,city,district,detail,is_default,
                         created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
                    (
                        user_id,
                        "并发",
                        display_name,
                        phone,
                        "上海市",
                        "上海市",
                        "徐汇区",
                        "并发验收路 88 号 502",
                        now,
                        now,
                    ),
                )
                address_id = int(cur.lastrowid)
                sessions = []
                for session_index in range(sessions_per_user):
                    raw_token = secrets.token_urlsafe(32)
                    session_id = secrets.token_urlsafe(18)
                    csrf = secrets.token_urlsafe(32)
                    conn.execute(
                        """INSERT INTO sessions(id,token_hash,user_id,csrf,created_at,expires_at,last_seen_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            session_id,
                            hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
                            user_id,
                            csrf,
                            now,
                            self._iso(timedelta(days=30)),
                            now,
                        ),
                    )
                    sessions.append(
                        {
                            "id": session_id,
                            "token": raw_token,
                            "csrf": csrf,
                            "worker": f"{tag}-{index + 1}-{session_index + 1}",
                        }
                    )
                created.append(
                    {
                        "user_id": user_id,
                        "handle": handle,
                        "display_name": display_name,
                        "address_id": address_id,
                        "sessions": sessions,
                    }
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        return created

    def add_coupon(self, code: str, *, quantity: int, threshold_cents: int, discount_cents: int,
                   claimed_count: int = 0) -> None:
        conn = self.connect()
        try:
            conn.execute(
                """INSERT INTO coupons(code,title,description,threshold_cents,discount_cents,scope,valid_days,
                     auto_claim_new_user,quantity,claimed_count,created_at)
                   VALUES(?,?,?,?,?,'all',30,0,?,?,?)""",
                (
                    code,
                    "并发券",
                    "并发验证用券",
                    int(threshold_cents),
                    int(discount_cents),
                    int(quantity),
                    int(claimed_count),
                    self._iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def grant_user_coupon(self, user_id: int, code: str) -> None:
        conn = self.connect()
        try:
            conn.execute(
                """INSERT INTO user_coupons(user_id,coupon_code,status,claimed_at,expires_at)
                   VALUES(?,?,'AVAILABLE',?,?)""",
                (user_id, code, self._iso(), self._iso(timedelta(days=30))),
            )
            conn.commit()
        finally:
            conn.close()

    def set_stock(self, offer_id: int, stock: int) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "UPDATE offers SET stock=?,updated_at=? WHERE id=?",
                (int(stock), self._iso(), int(offer_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def scalar(self, sql: str, params=()):
        conn = self.connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def row(self, sql: str, params=()):
        conn = self.connect()
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()

    def rows(self, sql: str, params=()):
        conn = self.connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def execute(self, sql: str, params=()):
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


class ServerProcess:
    def __init__(self, data_dir: Path, log_path: Path):
        self.data_dir = data_dir
        self.log_path = log_path
        self.proc = None
        self._handle = None

    def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=False)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.log_path.open("wb")
        cmd = [
            sys.executable,
            "-u",
            "-B",
            "apps/storefront/server.py",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--data-dir",
            str(self.data_dir),
            "--log-level",
            "INFO",
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=self._handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 35
        last_error = ""
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                last_error = f"server exited rc={self.proc.returncode}"
                break
            try:
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1.5)
                conn.request("GET", "/api/health", headers={"Connection": "close"})
                response = conn.getresponse()
                raw = response.read()
                conn.close()
                if response.status == 200 and json.loads(raw.decode("utf-8")).get("ok") is True:
                    return
                last_error = f"health status={response.status}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                # Readiness retry only; never used as a correctness assertion.
                time.sleep(0.1)
        detail = ""
        if self.log_path.exists():
            detail = self.log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        self.stop()
        raise RuntimeError(f"server not ready at {BASE_URL}: {last_error}\n{detail}")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(timeout=4)
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                self._handle = None
            self._handle = None


# ---------------------------------------------------------------------------
# scenario runner
# ---------------------------------------------------------------------------

class RoundHarness:
    def __init__(self, round_no: int, run_root: Path, logger: EvidenceLogger):
        self.round_no = round_no
        self.run_root = run_root
        self.round_dir = run_root / f"round-{round_no}"
        self.logger = logger
        self.server = None
        self.fixture = None
        self.offers = None
        self.results = []
        self.current_scenario = None
        self.current_http = []
        self.current_checks = []

    def start(self) -> None:
        self.round_dir.mkdir(parents=True, exist_ok=True)
        data_dir = self.round_dir / "data"
        log_path = self.round_dir / "server.log"
        self.server = ServerProcess(data_dir, log_path)
        self.server.start()
        self.db_path = data_dir / "openshop.sqlite3"
        self.fixture = Fixture(self.db_path, prefix=f"r{self.round_no}")
        self.offers = self._select_offers()
        print(
            f"CONCURRENCY_ROUND_START round={self.round_no} port={PORT} "
            f"data_dir={data_dir} log={log_path}",
            flush=True,
        )

    def stop(self) -> None:
        if self.server is not None:
            self.server.stop()

    def client_for(self, user: dict, index: int = 0) -> WorkerClient:
        session = user["sessions"][index]
        return WorkerClient(
            cookie=f"openshop_session={session['token']}",
            csrf=session["csrf"],
        )

    def _select_offers(self):
        query = """
            SELECT o.id AS offer_id, o.shop_id, o.stock, o.price_cents, o.market_price_cents,
                   o.group_size, o.group_price_cents, o.kind AS offer_kind, o.name AS offer_name,
                   p.id AS product_id, p.slug, p.title, s.name AS shop_name,
                   k.id AS sku_id, k.name AS sku_name, k.price_delta_cents
            FROM offers o
            JOIN products p ON p.id=o.product_id
            JOIN shops s ON s.id=o.shop_id
            JOIN skus k ON k.product_id=o.product_id
            WHERE o.status='active' AND p.status='active'
            ORDER BY o.id, k.sort_order, k.id
        """
        rows = [dict(row) for row in self.fixture.rows(query)]
        unique = []
        seen = set()
        for row in rows:
            offer_id = int(row["offer_id"])
            if offer_id in seen:
                continue
            seen.add(offer_id)
            unique.append(row)
        group_offer = next(
            (row for row in unique if row["group_size"] is not None and int(row["group_size"]) == 3),
            None,
        )
        if group_offer is None:
            raise RuntimeError("no group_size=3 offer fixture; cannot test real group competition")
        standards = [row for row in unique if int(row["offer_id"]) != int(group_offer["offer_id"])]
        eligible_coupons = [
            row for row in standards
            if int(row["price_cents"]) + int(row["price_delta_cents"]) >= 19900
        ]
        if len(standards) < 5 or not eligible_coupons:
            raise RuntimeError("insufficient distinct offer fixtures for five isolated scenarios")
        ordered = [eligible_coupons[0]] + [row for row in standards if row is not eligible_coupons[0]]
        return {
            "stock": ordered[0],
            "idempotency": ordered[1],
            "coupon": ordered[2],
            "cancel": ordered[3],
            "pay_cancel": ordered[4],
            "group": group_offer,
            "all": unique,
        }

    def _session_bundle(self, user: dict) -> dict:
        expected = len(user["sessions"])
        unique_csrf = len({session["csrf"] for session in user["sessions"]})
        unique_tokens = len({session["token"] for session in user["sessions"]})
        if unique_csrf != expected or unique_tokens != expected:
            raise AssertionError("worker sessions must have independent cookie and CSRF values")
        return {"sessions": expected, "unique_csrf": unique_csrf, "unique_tokens": unique_tokens}

    def _record_http(self, label: str, worker: str, response: HttpResponse) -> None:
        entry = {
            "label": label,
            "worker": worker,
            "status": response.status,
            "code": response.error_code,
            "message": response.error_message[:160],
            "transport_error": response.transport_error,
            "text": response.text[:2000],
        }
        if isinstance(response.data, dict):
            data = response.data
            if "order_no" in data:
                entry["order_no"] = data.get("order_no")
            if "status" in data:
                entry["order_status"] = data.get("status")
            if "joined_count" in data:
                entry["joined_count"] = data.get("joined_count")
        self.current_http.append(entry)
        code_text = f" code={entry['code']}" if entry["code"] else ""
        order_text = f" order={entry['order_no']}" if entry.get("order_no") else ""
        err_text = f" err={entry['transport_error']}" if entry["transport_error"] else ""
        self.logger.line(
            f"CONCURRENCY_HTTP round={self.round_no} scenario={self.current_scenario} "
            f"phase={label} worker={worker} status={response.status}{code_text}{order_text}{err_text}"
        )

    def _setup_request(self, label: str, worker: str, client: WorkerClient, method: str, path: str, body=None,
                       expected: int = 200) -> HttpResponse:
        response = client.request(method, path, body)
        self._record_http(label, worker, response)
        if response.transport_error:
            raise AssertionError(f"{label} transport error: {response.transport_error}")
        if response.status != expected:
            raise AssertionError(
                f"{label} expected HTTP {expected}, got {response.status}: "
                f"code={response.error_code} message={response.error_message} body={response.text[:400]}"
            )
        return response

    def _check(self, label: str, condition: bool, detail: str) -> None:
        verdict = "PASS" if condition else "FAIL"
        self.current_checks.append({"name": label, "verdict": verdict, "detail": detail})
        self.logger.line(
            f"CONCURRENCY_CHECK round={self.round_no} scenario={self.current_scenario} "
            f"name={label} verdict={verdict} {detail}"
        )
        if not condition:
            raise AssertionError(f"{label}: {detail}")

    def _status_counts(self, results) -> dict:
        counts = {}
        for result in results:
            key = str(result["status"])
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _assert_no_errors(self, results) -> None:
        bad = [
            result for result in results
            if result["status"] == 0 or int(result["status"]) >= 500
        ]
        self._check(
            "no_500_or_transport",
            not bad,
            f"statuses={self._status_counts(results)} bad={[{'worker': r['worker'], 'status': r['status'], 'error': r.get('error') or r.get('transport_error')} for r in bad]}",
        )

    def _parallel(self, label: str, jobs):
        barrier = threading.Barrier(len(jobs), timeout=BARRIER_TIMEOUT)

        def run_one(job):
            started = time.monotonic()
            try:
                barrier.wait()
                started = time.monotonic()
                response = job["client"].request(job["method"], job["path"], job.get("body"))
                return {
                    "worker": job["worker"],
                    "status": response.status,
                    "code": response.error_code,
                    "message": response.error_message,
                    "transport_error": response.transport_error,
                    "text": response.text[:3000],
                    "started": started,
                    "finished": time.monotonic(),
                    "response": response,
                }
            except BaseException as exc:
                return {
                    "worker": job["worker"],
                    "status": 0,
                    "code": "TRANSPORT_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "transport_error": f"{type(exc).__name__}: {exc}",
                    "text": "",
                    "started": started,
                    "finished": time.monotonic(),
                    "error": f"{type(exc).__name__}: {exc}",
                    "response": None,
                }

        with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix=f"r{self.round_no}-{label}") as pool:
            futures = [pool.submit(run_one, job) for job in jobs]
            results = []
            for future in futures:
                try:
                    result = future.result(timeout=HTTP_TIMEOUT + 15)
                except BaseException as exc:
                    result = {
                        "worker": "unknown",
                        "status": 0,
                        "code": "FUTURE_ERROR",
                        "message": f"{type(exc).__name__}: {exc}",
                        "transport_error": f"{type(exc).__name__}: {exc}",
                        "text": "",
                        "started": time.monotonic(),
                        "finished": time.monotonic(),
                        "error": f"{type(exc).__name__}: {exc}",
                        "response": None,
                    }
                results.append(result)
                response = result.get("response")
                if response is not None:
                    self._record_http(label, result["worker"], response)
                else:
                    entry = {
                        "label": label,
                        "worker": result["worker"],
                        "status": 0,
                        "code": result.get("code"),
                        "message": result.get("message", "")[:300],
                        "transport_error": result.get("transport_error"),
                        "text": result.get("text", "")[:500],
                    }
                    self.current_http.append(entry)
                    self.logger.line(
                        f"CONCURRENCY_HTTP round={self.round_no} scenario={self.current_scenario} "
                        f"phase={label} worker={result['worker']} status=0 code=TRANSPORT_ERROR "
                        f"err={result.get('transport_error')}"
                    )
        overlap_start = max(result["started"] for result in results)
        overlap_end = min(result["finished"] for result in results)
        self.logger.line(
            f"CONCURRENCY_PHASE round={self.round_no} scenario={self.current_scenario} phase={label} "
            f"workers={len(results)} all_started_before_first_finished={overlap_start <= overlap_end} "
            f"statuses={self._status_counts(results)}"
        )
        return results

    def _worker_code(self, result) -> str:
        return str(result.get("code") or "")

    def _worker_payload(self, result):
        response = result.get("response")
        return response.payload if response is not None else None

    def _worker_data(self, result) -> dict:
        payload = self._worker_payload(result)
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        return {}

    def _db_scalar(self, sql: str, params=()):
        return self.fixture.scalar(sql, params)

    def _db_int(self, sql: str, params=(), default: int = 0) -> int:
        value = self.fixture.scalar(sql, params)
        return int(value) if value is not None else int(default)

    def _key(self, label: str) -> str:
        return f"conc-r{self.round_no}-{label}-{secrets.token_hex(6)}"

    def _order_body(self, user: dict, key: str, *, coupon_code=None, shipping="standard", note="") -> dict:
        return {
            "address_id": int(user["address_id"]),
            "coupon_code": coupon_code,
            "shipping_method": shipping,
            "note": note,
            "payment_method": "demo",
            "idempotency_key": key,
        }

    def _add_standard(self, label: str, worker: str, client: WorkerClient, offer: dict, quantity: int = 1) -> HttpResponse:
        response = self._setup_request(
            label,
            worker,
            client,
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(offer["offer_id"]),
                "sku_id": int(offer["sku_id"]),
                "quantity": int(quantity),
                "buy_mode": "standard",
            },
        )
        return response

    def _add_group(self, label: str, worker: str, client: WorkerClient, offer: dict, group_code: str | None) -> HttpResponse:
        return self._setup_request(
            label,
            worker,
            client,
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(offer["offer_id"]),
                "sku_id": int(offer["sku_id"]),
                "quantity": 1,
                "buy_mode": "group",
                "group_code": group_code,
            },
        )

    def _get_json(self, label: str, worker: str, client: WorkerClient, path: str) -> dict:
        response = self._setup_request(label, worker, client, "GET", path)
        if not isinstance(response.data, dict):
            raise AssertionError(f"{label} missing data object")
        return response.data

    def _scenario(self, name: str, function):
        self.current_scenario = name
        self.current_http = []
        self.current_checks = []
        print(
            f"CONCURRENCY_SCENARIO_START round={self.round_no} scenario={name}",
            flush=True,
        )
        try:
            summary = function()
        except BaseException as exc:
            self.logger.line(
                f"CONCURRENCY_SCENARIO round={self.round_no} scenario={name} verdict=FAIL "
                f"first_error={type(exc).__name__}: {exc}"
            )
            failure = {
                "round": self.round_no,
                "scenario": name,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "http": self.current_http,
                "checks": self.current_checks,
            }
            (self.round_dir / f"scenario-{name}-failure.json").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            raise
        self.results.append({"scenario": name, "verdict": "PASS", "summary": summary})
        self.logger.line(
            f"CONCURRENCY_SCENARIO round={self.round_no} scenario={name} verdict=PASS {summary}"
        )
        payload = {
            "round": self.round_no,
            "scenario": name,
            "verdict": "PASS",
            "summary": summary,
            "http": self.current_http,
            "checks": self.current_checks,
        }
        (self.round_dir / f"scenario-{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

    def _check_independent_clients(self, clients, label: str) -> None:
        unique_cookies = len({client.cookie for client in clients})
        unique_csrf = len({client.csrf for client in clients})
        self._check(
            f"{label}_independent_sessions",
            unique_cookies == len(clients) and unique_csrf == len(clients),
            f"workers={len(clients)} unique_cookies={unique_cookies} unique_csrf={unique_csrf}",
        )

    def _create_order_setup(self, label: str, worker: str, client: WorkerClient, body: dict) -> dict:
        response = self._setup_request(label, worker, client, "POST", "/api/orders", body)
        data = response.data
        if not isinstance(data, dict) or not data.get("order_no"):
            raise AssertionError(f"{label} missing order_no: {response.text[:400]}")
        return data

    def _public_group(self, label: str, code: str) -> dict:
        public = WorkerClient()
        return self._get_json(label, "public", public, f"/api/groups/{code}")

    def _order_count_for_keys(self, keys) -> int:
        keys = list(keys)
        if not keys:
            return 0
        marks = ",".join("?" for _ in keys)
        return int(self._db_scalar(f"SELECT COUNT(*) FROM orders WHERE idempotency_key IN ({marks})", tuple(keys)) or 0)

    def _package_statuses(self, order_id: int):
        return [row["status"] for row in self.fixture.rows(
            "SELECT status FROM order_packages WHERE order_id=? ORDER BY id", (int(order_id),)
        )]

    # ------------------------------------------------------------- scenario 01

    def scenario_stock_race(self):
        offer = self.offers["stock"]
        offer_id = int(offer["offer_id"])
        count = 8
        self.fixture.set_stock(offer_id, 5)
        buyers = self.fixture.create_buyers("stk", count)
        clients = []
        for index, buyer in enumerate(buyers, 1):
            client = self.client_for(buyer)
            self._add_standard(f"cart_{index}", f"stock-cart-{index}", client, offer)
            clients.append(client)
        self._check_independent_clients(clients, "stock_race")

        self.fixture.set_stock(offer_id, 1)
        stock_before = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        keys = [self._key(f"stk{i}") for i in range(count)]
        jobs = [
            {
                "worker": f"stock-buyer-{index + 1}",
                "client": clients[index],
                "method": "POST",
                "path": "/api/orders",
                "body": self._order_body(buyers[index], keys[index], note="stock-race"),
            }
            for index in range(count)
        ]
        results = self._parallel("stock_order_burst", jobs)
        self._assert_no_errors(results)

        successes = [result for result in results if result["status"] == 200]
        failures = [result for result in results if result["status"] != 200]
        self._check(
            "exactly_one_stock_winner",
            len(successes) == 1 and stock_before == 1,
            f"successes={len(successes)} failures={len(failures)} stock_before={stock_before}",
        )
        self._check(
            "all_losers_are_409_stock_changed",
            len(failures) == count - 1
            and all(result["status"] == 409 and self._worker_code(result) == "STOCK_CHANGED" for result in failures),
            f"statuses={self._status_counts(results)} loser_codes={[self._worker_code(r) for r in failures]}",
        )
        winner_data = self._worker_data(successes[0]) if successes else {}
        winner_no = winner_data.get("order_no")
        stock_after = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        order_rows = self._order_count_for_keys(keys)
        winner_item_count = 0
        if winner_no:
            winner_order = self.fixture.row("SELECT id,status FROM orders WHERE order_no=?", (winner_no,))
            if winner_order is not None:
                winner_item_count = int(
                    self._db_scalar("SELECT COUNT(*) FROM order_items WHERE order_id=?", (int(winner_order["id"]),)) or 0
                )
        self._check(
            "stock_race_db_final",
            stock_after == 0 and order_rows == 1 and winner_item_count == 1,
            f"stock={stock_after} order_rows={order_rows} winner_items={winner_item_count}",
        )
        return (
            f"workers={count} statuses={self._status_counts(results)} stock=1->{stock_after} "
            f"order_rows={order_rows} no_500=true"
        )

    # ------------------------------------------------------------- scenario 02

    def scenario_idempotency_race(self):
        offer = self.offers["idempotency"]
        offer_id = int(offer["offer_id"])
        self.fixture.set_stock(offer_id, 5)
        buyer = self.fixture.create_buyers("idem", 1, sessions_per_user=4)[0]
        clients = [self.client_for(buyer, index) for index in range(4)]
        self._check_independent_clients(clients, "idempotency_race")
        self._add_standard("idem_cart", "idem-cart-owner", clients[0], offer)
        key = self._key("idem")
        body = self._order_body(buyer, key, note="idempotency-race")
        jobs = [
            {
                "worker": f"idem-session-{index + 1}",
                "client": clients[index],
                "method": "POST",
                "path": "/api/orders",
                "body": body,
            }
            for index in range(len(clients))
        ]
        stock_before = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        results = self._parallel("same_key_order_burst", jobs)
        self._assert_no_errors(results)

        success_results = [result for result in results if result["status"] == 200]
        conflicts = [
            result for result in results
            if result["status"] == 409 and self._worker_code(result) == "IDEMPOTENCY_CONFLICT"
        ]
        order_numbers = {
            str(self._worker_data(result).get("order_no"))
            for result in success_results
            if self._worker_data(result).get("order_no")
        }
        order_rows = int(
            self._db_scalar(
                "SELECT COUNT(*) FROM orders WHERE user_id=? AND idempotency_key=?",
                (int(buyer["user_id"]), key),
            ) or 0
        )
        item_rows = int(
            self._db_scalar(
                "SELECT COUNT(*) FROM order_items oi JOIN orders o ON o.id=oi.order_id "
                "WHERE o.user_id=? AND o.idempotency_key=?",
                (int(buyer["user_id"]), key),
            ) or 0
        )
        stock_after = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        self._check(
            "idempotency_one_order_and_one_deduction",
            bool(success_results)
            and all(result["status"] in {200, 409} for result in results)
            and len(success_results) + len(conflicts) == len(results)
            and len(order_numbers) == 1
            and order_rows == 1
            and item_rows == 1
            and stock_after == stock_before - 1,
            (
                f"statuses={self._status_counts(results)} order_numbers={len(order_numbers)} "
                f"order_rows={order_rows} item_rows={item_rows} stock={stock_before}->{stock_after}"
            ),
        )
        self._check(
            "idempotency_replay_same_order_no_or_explicit_conflict",
            all(result["status"] == 200 for result in results) or bool(conflicts),
            f"successes={len(success_results)} explicit_conflicts={len(conflicts)}",
        )
        return (
            f"workers={len(clients)} statuses={self._status_counts(results)} order_rows=1 "
            f"stock={stock_before}->{stock_after} order_numbers={len(order_numbers)}"
        )

    # ------------------------------------------------------------- scenario 03

    def scenario_coupon_redemption_race(self):
        offer = self.offers["coupon"]
        offer_id = int(offer["offer_id"])
        self.fixture.set_stock(offer_id, 5)
        coupon_code = f"CONCUSE{self.round_no:02d}{secrets.token_hex(3).upper()}"
        self.fixture.add_coupon(
            coupon_code,
            quantity=10,
            threshold_cents=19900,
            discount_cents=2000,
            claimed_count=1,
        )
        buyer = self.fixture.create_buyers("cpn", 1, sessions_per_user=2)[0]
        self.fixture.grant_user_coupon(int(buyer["user_id"]), coupon_code)
        clients = [self.client_for(buyer, index) for index in range(2)]
        self._check_independent_clients(clients, "coupon_redemption_race")
        self._add_standard("coupon_cart", "coupon-cart-owner", clients[0], offer)
        quote_response = self._setup_request(
            "coupon_quote",
            "coupon-quote",
            clients[0],
            "POST",
            "/api/checkout/quote",
            {"coupon_code": coupon_code, "shipping_method": "standard"},
        )
        quote_data = quote_response.data if isinstance(quote_response.data, dict) else {}
        self._check(
            "coupon_quote_eligible",
            bool(quote_data.get("coupon")) and not quote_data.get("coupon_error"),
            f"coupon={bool(quote_data.get('coupon'))} error={quote_data.get('coupon_error')}",
        )
        claimed_before = int(
            self._db_scalar("SELECT claimed_count FROM coupons WHERE code=?", (coupon_code,)) or -1
        )
        stock_before = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        keys = [self._key("cpn1"), self._key("cpn2")]
        jobs = [
            {
                "worker": f"coupon-session-{index + 1}",
                "client": clients[index],
                "method": "POST",
                "path": "/api/orders",
                "body": self._order_body(buyer, keys[index], coupon_code=coupon_code, note="coupon-race"),
            }
            for index in range(2)
        ]
        results = self._parallel("coupon_order_burst", jobs)
        self._assert_no_errors(results)
        successes = [result for result in results if result["status"] == 200]
        failures = [result for result in results if result["status"] != 200]
        self._check(
            "at_most_one_coupon_order",
            len(successes) == 1 and len(failures) == 1 and 400 <= int(failures[0]["status"]) < 500,
            f"statuses={self._status_counts(results)} loser_code={self._worker_code(failures[0]) if failures else None}",
        )
        winner_data = self._worker_data(successes[0]) if successes else {}
        winner_no = winner_data.get("order_no")
        order_row = self.fixture.row(
            "SELECT id,status,coupon_code,user_coupon_id,discount_cents FROM orders WHERE order_no=?",
            (winner_no,),
        ) if winner_no else None
        coupon_row = self.fixture.row(
            """SELECT uc.id AS user_coupon_id, uc.status AS uc_status, uc.used_at, uc.order_id,
                      c.claimed_count AS claimed_count, c.quantity AS quantity
               FROM user_coupons uc JOIN coupons c ON c.code=uc.coupon_code
               WHERE uc.user_id=? AND uc.coupon_code=?""",
            (int(buyer["user_id"]), coupon_code),
        )
        claimed_after = int(
            self._db_scalar("SELECT claimed_count FROM coupons WHERE code=?", (coupon_code,)) or -1
        )
        user_coupon_rows = int(
            self._db_scalar(
                "SELECT COUNT(*) FROM user_coupons WHERE coupon_code=?",
                (coupon_code,),
            ) or 0
        )
        stock_after = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        self._check(
            "coupon_bound_to_exactly_one_order",
            order_row is not None
            and order_row["coupon_code"] == coupon_code
            and order_row["user_coupon_id"] is not None
            and int(order_row["discount_cents"]) == 2000
            and coupon_row is not None
            and coupon_row["uc_status"] == "USED"
            and coupon_row["used_at"] is not None
            and int(coupon_row["order_id"]) == int(order_row["id"])
            and user_coupon_rows == 1
            and claimed_after == claimed_before == int(coupon_row["claimed_count"]),
            (
                f"statuses={self._status_counts(results)} coupon_status={coupon_row['uc_status'] if coupon_row else None} "
                f"claimed={claimed_before}->{claimed_after} user_coupon_rows={user_coupon_rows} "
                f"stock={stock_before}->{stock_after}"
            ),
        )
        self._check(
            "coupon_race_one_stock_deduction",
            stock_after == stock_before - 1,
            f"stock={stock_before}->{stock_after}",
        )

        # A re-selected cart proves the used coupon is fail-closed after the
        # winner commits. (Logged-in carts are user-scoped: both sessions share
        # the one cart, so the first serialized order consumes it.)
        self._add_standard("coupon_reuse_cart", "coupon-reuse", clients[0], offer)
        reuse_key = self._key("cpnreuse")
        reuse_response = self._setup_request(
            "coupon_reuse_order",
            "coupon-reuse",
            clients[0],
            "POST",
            "/api/orders",
            self._order_body(buyer, reuse_key, coupon_code=coupon_code, note="coupon-reuse"),
            expected=422,
        )
        self._check(
            "used_coupon_rejected_after_new_cart",
            reuse_response.error_code == "COUPON_INVALID"
            and self._order_count_for_keys([reuse_key]) == 0
            and self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1) == stock_after,
            (
                f"http={reuse_response.status} code={reuse_response.error_code} "
                f"orders={self._order_count_for_keys([reuse_key])}"
            ),
        )
        return (
            f"workers=2 statuses={self._status_counts(results)} one_coupon_order=1 "
            f"claimed={claimed_before}->{claimed_after} stock={stock_before}->{stock_after} reuse=422"
        )

    # ------------------------------------------------------------- scenario 04

    def scenario_coupon_claim_race(self):
        coupon_code = f"CONCCLM{self.round_no:02d}{secrets.token_hex(3).upper()}"
        self.fixture.add_coupon(
            coupon_code,
            quantity=1,
            threshold_cents=0,
            discount_cents=100,
            claimed_count=0,
        )
        users = self.fixture.create_buyers("clm", 5)
        clients = [self.client_for(user) for user in users]
        self._check_independent_clients(clients, "coupon_claim_race")
        jobs = [
            {
                "worker": f"claimant-{index + 1}",
                "client": clients[index],
                "method": "POST",
                "path": f"/api/coupons/{coupon_code}/claim",
                "body": {},
            }
            for index in range(len(clients))
        ]
        results = self._parallel("coupon_claim_burst", jobs)
        self._assert_no_errors(results)
        successes = [result for result in results if result["status"] == 200]
        failures = [result for result in results if result["status"] != 200]
        claimed_count = int(
            self._db_scalar("SELECT claimed_count FROM coupons WHERE code=?", (coupon_code,)) or -1
        )
        granted = int(
            self._db_scalar("SELECT COUNT(*) FROM user_coupons WHERE coupon_code=?", (coupon_code,)) or 0
        )
        self._check(
            "coupon_claim_single_winner",
            len(successes) == 1
            and len(failures) == len(users) - 1
            and all(result["status"] == 409 for result in failures),
            f"statuses={self._status_counts(results)} loser_codes={[self._worker_code(r) for r in failures]}",
        )
        self._check(
            "coupon_claim_counter_consistent",
            claimed_count == 1 and granted == 1,
            f"claimed_count={claimed_count} user_coupon_rows={granted} quantity=1",
        )
        return (
            f"workers={len(users)} statuses={self._status_counts(results)} "
            f"claimed_count={claimed_count} user_coupon_rows={granted}"
        )

    # ------------------------------------------------------------- scenario 05

    def scenario_group_join_pay_race(self):
        offer = self.offers["group"]
        offer_id = int(offer["offer_id"])
        self.fixture.set_stock(offer_id, 50)
        initiator = self.fixture.create_buyers("gin", 1)[0]
        init_client = self.client_for(initiator)
        self._add_group("group_init_cart", "group-initiator", init_client, offer, None)
        init_key = self._key("ginit")
        init_order = self._create_order_setup(
            "group_init_order",
            "group-initiator",
            init_client,
            self._order_body(initiator, init_key, note="group-initiator"),
        )
        group_code = (init_order.get("group") or {}).get("code")
        required = (init_order.get("group") or {}).get("required_size")
        init_order_no = init_order.get("order_no")
        if not group_code or not init_order_no:
            raise AssertionError(f"group initiator missing group/order: {init_order}")
        self._check(
            "group_shape",
            init_order.get("status") == "PENDING_PAYMENT" and int(required or 0) == 3,
            f"status={init_order.get('status')} required={required} joined={(init_order.get('group') or {}).get('joined_count')}",
        )
        init_pay = self._setup_request(
            "group_init_pay",
            "group-initiator",
            init_client,
            "POST",
            f"/api/orders/{init_order_no}/pay",
            {"payment_method": "demo"},
        )
        init_paid = init_pay.data if isinstance(init_pay.data, dict) else {}
        self._check(
            "group_initiator_paid_not_full",
            init_paid.get("status") == "GROUP_FORMING"
            and (init_paid.get("group") or {}).get("joined_count") == 1
            and (init_paid.get("group") or {}).get("status") == "FORMING",
            f"status={init_paid.get('status')} group={(init_paid.get('group') or {})}",
        )

        joiners = self.fixture.create_buyers("gjn", 5)
        joiner_clients = [self.client_for(user) for user in joiners]
        self._check_independent_clients(joiner_clients, "group_join")
        for index, (user, client) in enumerate(zip(joiners, joiner_clients), 1):
            self._add_group(f"group_join_cart_{index}", f"group-joiner-{index}", client, offer, group_code)

        keys = [self._key(f"gjoin{i}") for i in range(len(joiners))]
        jobs = [
            {
                "worker": f"group-joiner-{index + 1}",
                "client": joiner_clients[index],
                "method": "POST",
                "path": "/api/orders",
                "body": self._order_body(joiners[index], keys[index], note="group-join"),
            }
            for index in range(len(joiners))
        ]
        join_results = self._parallel("group_join_burst", jobs)
        self._assert_no_errors(join_results)
        join_successes = [result for result in join_results if result["status"] == 200]
        join_failures = [result for result in join_results if result["status"] != 200]
        self._check(
            "group_slots_not_oversold",
            len(join_successes) == 2
            and len(join_failures) == 3
            and all(result["status"] == 409 and self._worker_code(result) == "GROUP_FULL" for result in join_failures),
            f"statuses={self._status_counts(join_results)} loser_codes={[self._worker_code(r) for r in join_failures]}",
        )
        group_row = self.fixture.row(
            "SELECT id,status,required_size,expires_at FROM groups WHERE code=?", (group_code,)
        )
        if group_row is None:
            raise AssertionError("group disappeared after join race")
        member_rows = self.fixture.rows(
            "SELECT gm.user_id,gm.order_id,gm.paid_at FROM group_members gm WHERE gm.group_id=? ORDER BY gm.id",
            (int(group_row["id"]),),
        )
        paid_before = sum(1 for row in member_rows if row["paid_at"] is not None)
        public_before = self._public_group("group_public_after_join", group_code)
        self._check(
            "group_pending_join_is_not_fake_full",
            group_row["status"] == "FORMING"
            and len(member_rows) == 3
            and paid_before == 1
            and public_before.get("status") == "FORMING"
            and public_before.get("joined_count") == 1
            and len(public_before.get("members") or []) == 1,
            (
                f"db_members={len(member_rows)} db_status={group_row['status']} paid={paid_before} "
                f"public_status={public_before.get('status')} joined={public_before.get('joined_count')}"
            ),
        )
        pay_order_jobs = []
        successful_orders = []
        for index, result in enumerate(join_results):
            if result["status"] != 200:
                continue
            order_no = self._worker_data(result).get("order_no")
            if not order_no:
                raise AssertionError("successful group join missing order_no")
            successful_orders.append(order_no)
            pay_order_jobs.append(
                {
                    "worker": f"group-pay-{len(pay_order_jobs) + 1}",
                    "client": joiner_clients[index],
                    "method": "POST",
                    "path": f"/api/orders/{order_no}/pay",
                    "body": {"payment_method": "demo"},
                }
            )
        self._check(
            "group_two_pending_orders_created",
            len(pay_order_jobs) == 2 and len(set(order_no for order_no in successful_orders)) == 2,
            f"successful_join_orders={len(pay_order_jobs)}",
        )
        self._check_independent_clients([job["client"] for job in pay_order_jobs], "group_pay")
        pay_results = self._parallel("group_pay_burst", pay_order_jobs)
        self._assert_no_errors(pay_results)
        self._check(
            "group_payments_all_succeed",
            len(pay_results) == 2 and all(result["status"] == 200 for result in pay_results),
            f"statuses={self._status_counts(pay_results)}",
        )

        group_after = self.fixture.row(
            "SELECT id,status,required_size FROM groups WHERE code=?", (group_code,)
        )
        members_after = self.fixture.rows(
            """SELECT gm.user_id,gm.paid_at,o.order_no,o.status AS order_status
               FROM group_members gm JOIN orders o ON o.id=gm.order_id
               WHERE gm.group_id=? ORDER BY gm.id""",
            (int(group_row["id"]),),
        )
        public_after = self._public_group("group_public_after_full", group_code)
        expected_member_orders = {init_order_no, *successful_orders}
        all_member_orders = {str(row["order_no"]) for row in members_after}
        package_rows = self.fixture.rows(
            """SELECT p.status FROM order_packages p JOIN orders o ON o.id=p.order_id
               WHERE o.order_no IN (?,?,?)""",
            (init_order_no, successful_orders[0], successful_orders[1]),
        )
        stock_final = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        self._check(
            "group_full_membership_consistent",
            group_after is not None
            and group_after["status"] == "FULL"
            and int(group_after["required_size"]) == 3
            and len(members_after) == 3
            and len({int(row["user_id"]) for row in members_after}) == 3
            and all(row["paid_at"] is not None for row in members_after)
            and all(row["order_status"] == "PAID" for row in members_after)
            and all_member_orders == expected_member_orders
            and public_after.get("status") == "FULL"
            and public_after.get("joined_count") == 3
            and len(public_after.get("members") or []) == 3
            and all(row["status"] == "PENDING" for row in package_rows),
            (
                f"db_status={group_after['status'] if group_after else None} members={len(members_after)} "
                f"paid={sum(1 for row in members_after if row['paid_at'] is not None)} "
                f"public={public_after.get('status')}/{public_after.get('joined_count')} "
                f"stock={stock_final} packages={[row['status'] for row in package_rows]}"
            ),
        )
        self._check(
            "group_join_stock_and_rollback",
            stock_final == 47
            and self._order_count_for_keys(keys) == 2
            and all(
                self._order_count_for_keys([keys[index]]) == (1 if join_results[index]["status"] == 200 else 0)
                for index in range(len(keys))
            ),
            f"stock_final={stock_final} join_key_order_rows={self._order_count_for_keys(keys)}",
        )
        return (
            f"join_statuses={self._status_counts(join_results)} pay_statuses={self._status_counts(pay_results)} "
            f"public_before=FORMING/1 public_after={public_after.get('status')}/{public_after.get('joined_count')} "
            f"paid_members=3 stock={stock_final}"
        )

    # ------------------------------------------------------------- scenario 06

    def scenario_cancel_cancel_race(self):
        offer = self.offers["cancel"]
        offer_id = int(offer["offer_id"])
        self.fixture.set_stock(offer_id, 5)
        buyer = self.fixture.create_buyers("cxl", 1, sessions_per_user=2)[0]
        clients = [self.client_for(buyer, index) for index in range(2)]
        self._check_independent_clients(clients, "cancel_race")
        self._add_standard("cancel_cart", "cancel-cart-owner", clients[0], offer)
        stock_before = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        key = self._key("cancel")
        order = self._create_order_setup(
            "cancel_order_create",
            "cancel-order-owner",
            clients[0],
            self._order_body(buyer, key, note="cancel-race"),
        )
        order_no = str(order["order_no"])
        stock_after_create = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        jobs = [
            {
                "worker": f"cancel-session-{index + 1}",
                "client": clients[index],
                "method": "POST",
                "path": f"/api/orders/{order_no}/cancel",
                "body": {"reason": "并发取消"},
            }
            for index in range(2)
        ]
        results = self._parallel("cancel_burst", jobs)
        self._assert_no_errors(results)
        successes = [result for result in results if result["status"] == 200]
        failures = [result for result in results if result["status"] != 200]
        self._check(
            "one_cancel_winner",
            len(successes) == 1
            and len(failures) == 1
            and failures[0]["status"] == 409
            and self._worker_code(failures[0]) == "ORDER_STATE",
            f"statuses={self._status_counts(results)} loser_code={self._worker_code(failures[0]) if failures else None}",
        )
        order_row = self.fixture.row("SELECT id,status,cancelled_at FROM orders WHERE order_no=?", (order_no,))
        if order_row is None:
            raise AssertionError("cancelled order missing")
        package_statuses = self._package_statuses(int(order_row["id"]))
        cancel_events = int(
            self._db_scalar(
                "SELECT COUNT(*) FROM order_events WHERE order_id=? AND label='订单已取消'",
                (int(order_row["id"]),),
            ) or 0
        )
        stock_final = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        self._check(
            "cancel_race_consistent_state",
            order_row["status"] == "CANCELLED"
            and order_row["cancelled_at"] is not None
            and package_statuses == ["CANCELLED"]
            and stock_final == stock_before
            and stock_final >= 0
            and cancel_events == 1,
            (
                f"order_status={order_row['status']} packages={package_statuses} stock={stock_before}->"
                f"{stock_after_create}->{stock_final} cancel_events={cancel_events}"
            ),
        )
        return (
            f"workers=2 statuses={self._status_counts(results)} order=CANCELLED packages={package_statuses} "
            f"stock={stock_before}->{stock_after_create}->{stock_final} cancel_events={cancel_events}"
        )

    # ------------------------------------------------------------- scenario 07

    def scenario_pay_cancel_race(self):
        offer = self.offers["pay_cancel"]
        offer_id = int(offer["offer_id"])
        self.fixture.set_stock(offer_id, 5)
        buyer = self.fixture.create_buyers("pyc", 1, sessions_per_user=2)[0]
        clients = [self.client_for(buyer, index) for index in range(2)]
        self._check_independent_clients(clients, "pay_cancel_race")
        self._add_standard("pay_cancel_cart", "pay-cancel-owner", clients[0], offer)
        stock_before = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        key = self._key("paycancel")
        order = self._create_order_setup(
            "pay_cancel_order_create",
            "pay-cancel-owner",
            clients[0],
            self._order_body(buyer, key, note="pay-cancel-race"),
        )
        order_no = str(order["order_no"])
        stock_after_create = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        jobs = [
            {
                "worker": "pay-worker",
                "client": clients[0],
                "method": "POST",
                "path": f"/api/orders/{order_no}/pay",
                "body": {"payment_method": "demo"},
            },
            {
                "worker": "cancel-worker",
                "client": clients[1],
                "method": "POST",
                "path": f"/api/orders/{order_no}/cancel",
                "body": {"reason": "pay-cancel 并发取消"},
            },
        ]
        results = self._parallel("pay_cancel_burst", jobs)
        self._assert_no_errors(results)
        pay_result = results[0]
        cancel_result = results[1]
        self._check(
            "single_terminal_winner",
            (pay_result["status"] == 200) ^ (cancel_result["status"] == 200)
            and all(
                result["status"] in {200, 409}
                for result in results
            ),
            f"pay={pay_result['status']}/{self._worker_code(pay_result)} cancel={cancel_result['status']}/{self._worker_code(cancel_result)}",
        )
        losing_result = cancel_result if pay_result["status"] == 200 else pay_result
        self._check(
            "pay_cancel_loser_is_order_state",
            losing_result["status"] == 409 and self._worker_code(losing_result) == "ORDER_STATE",
            f"loser={self._worker_code(losing_result)}",
        )
        order_row = self.fixture.row(
            "SELECT id,status,paid_at,cancelled_at FROM orders WHERE order_no=?", (order_no,)
        )
        if order_row is None:
            raise AssertionError("pay/cancel order missing")
        package_statuses = self._package_statuses(int(order_row["id"]))
        stock_final = self._db_int("SELECT stock FROM offers WHERE id=?", (offer_id,), default=-1)
        if pay_result["status"] == 200:
            coherent = (
                order_row["status"] == "PAID"
                and order_row["paid_at"] is not None
                and order_row["cancelled_at"] is None
                and package_statuses == ["PENDING"]
                and stock_final == stock_after_create
                and stock_final >= 0
            )
        else:
            coherent = (
                order_row["status"] == "CANCELLED"
                and order_row["paid_at"] is None
                and order_row["cancelled_at"] is not None
                and package_statuses == ["CANCELLED"]
                and stock_final == stock_before
                and stock_final >= 0
            )
        self._check(
            "pay_cancel_final_state_coherent",
            coherent,
            (
                f"winner={'pay' if pay_result['status'] == 200 else 'cancel'} order={order_row['status']} "
                f"paid_at={bool(order_row['paid_at'])} cancelled_at={bool(order_row['cancelled_at'])} "
                f"packages={package_statuses} stock={stock_before}->{stock_after_create}->{stock_final}"
            ),
        )
        return (
            f"pay={pay_result['status']} cancel={cancel_result['status']} "
            f"winner={'pay' if pay_result['status'] == 200 else 'cancel'} order={order_row['status']} "
            f"packages={package_statuses} stock={stock_final}"
        )


    def run_scenarios(self) -> int:
        scenarios = [
            ("stock_race", self.scenario_stock_race),
            ("idempotency_race", self.scenario_idempotency_race),
            ("coupon_redemption_race", self.scenario_coupon_redemption_race),
            ("coupon_claim_race", self.scenario_coupon_claim_race),
            ("group_join_pay_race", self.scenario_group_join_pay_race),
            ("cancel_cancel_race", self.scenario_cancel_cancel_race),
            ("pay_cancel_race", self.scenario_pay_cancel_race),
        ]
        for name, function in scenarios:
            self._scenario(name, function)
        summary = {"round": self.round_no, "verdict": "PASS", "scenarios": self.results}
        (self.round_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.logger.line(
            f"CONCURRENCY_ROUND round={self.round_no} verdict=PASS "
            f"scenarios={len(self.results)} data_dir={self.round_dir / 'data'}"
        )
        return len(self.results)


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    run_root = EVIDENCE_ROOT / f"run-{stamp}"
    run_root.mkdir(parents=True, exist_ok=False)
    logger = EvidenceLogger(run_root / "console.log")
    completed_rounds = 0
    summaries = []
    started = time.monotonic()
    try:
        logger.line(f"CONCURRENCY_RUN start={utc_now_text()} rounds={ROUNDS} port={PORT} root={run_root}")
        for round_no in range(1, ROUNDS + 1):
            harness = RoundHarness(round_no, run_root, logger)
            try:
                harness.start()
                scenario_count = harness.run_scenarios()
                summaries.append(
                    {
                        "round": round_no,
                        "verdict": "PASS",
                        "scenario_count": scenario_count,
                        "scenarios": harness.results,
                    }
                )
                completed_rounds += 1
                harness.stop()
            except BaseException:
                harness.stop()
                raise
        acceptance = {
            "verdict": "PASS",
            "rounds": ROUNDS,
            "completed_rounds": completed_rounds,
            "port": PORT,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "run_root": str(run_root),
            "rounds_result": summaries,
        }
        (run_root / "acceptance.json").write_text(
            json.dumps(acceptance, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.line(
            f"CONCURRENCY_ACCEPTANCE verdict=PASS rounds={ROUNDS} port={PORT} "
            f"elapsed_seconds={acceptance['elapsed_seconds']} evidence={run_root}"
        )
        return 0
    except BaseException as exc:
        trace = traceback.format_exc()
        (run_root / "first-failure.txt").write_text(
            f"timestamp={utc_now_text()}\ncompleted_rounds={completed_rounds}\n"
            f"error={type(exc).__name__}: {exc}\n\n{trace}\n",
            encoding="utf-8",
        )
        logger.line(
            f"CONCURRENCY_ACCEPTANCE verdict=FAIL rounds_completed={completed_rounds} port={PORT} "
            f"first_error={type(exc).__name__}: {exc}"
        )
        logger.line(f"CONCURRENCY_FIRST_FAILURE evidence={run_root / 'first-failure.txt'}")
        logger.line(trace)
        return 1
    finally:
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
