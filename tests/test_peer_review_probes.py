#!/usr/bin/env python3
"""Independent adversarial peer-review probes for core/auth/catalog/trade.

Read-only product audit. This file owns a real server on 127.0.0.1:8882
(task-10); OPENSHOP_AUDIT_PORT can override only when 8882 is unavailable.
It uses a fresh SQLite database and a server log under
/hf3fs-jd/data/tmp/openshop/peer-review/. It never edits product source.

Run:
    sudo -n -u infra python3 -B tests/test_peer_review_probes.py

The suite exits non-zero when a finding probe is red. Red probes are security
findings, not test-suite exceptions: this is an audit gate, not a happy-path
acceptance suite. Setup failures are fatal and print the server-log tail.

Known weaknesses this run deliberately exposes (see docs/peer-review-core-api.md):
  F-01 session identifier is not rotated on authentication
  F-02 successful registrations reset the IP registration limiter
  F-03 login limits are per account identifier, with no IP-wide ceiling
  F-04 more than 100 query fields produce HTTP 500 (ValueError escapes parsing)
  F-05 a NUL byte in a static path drops the connection without an HTTP reply
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import http.cookiejar
import http.cookies
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("OPENSHOP_AUDIT_PORT", "8882"))
BASE_URL = f"http://127.0.0.1:{PORT}"
EVIDENCE_ROOT = Path("/hf3fs-jd/data/tmp/openshop/peer-review")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_MISSING = object()


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + f"-{os.getpid()}"


def _url(path: str, **params: Any) -> str:
    cleaned = {key: value for key, value in params.items() if value is not None}
    if not cleaned:
        return path
    return path + "?" + urllib.parse.urlencode(cleaned)


def _quote_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _header_pairs(headers: Any) -> list[tuple[str, str]]:
    if headers is None:
        return []
    if isinstance(headers, list):
        return [(str(k), str(v)) for k, v in headers]
    if hasattr(headers, "raw_items"):
        return [(str(k), str(v)) for k, v in headers.raw_items()]
    try:
        return [(str(k), str(v)) for k, v in headers.items()]
    except Exception:
        return []


class Resp:
    def __init__(self, status: int, headers: list[tuple[str, str]], text: str) -> None:
        self.status = status
        self.headers = headers
        self.text = text
        try:
            self.payload = json.loads(text) if text else None
        except (ValueError, TypeError):
            self.payload = None

    @property
    def data(self) -> Any:
        return (self.payload or {}).get("data") if isinstance(self.payload, dict) else None

    @property
    def error(self) -> dict[str, Any]:
        if isinstance(self.payload, dict) and isinstance(self.payload.get("error"), dict):
            return self.payload["error"]
        return {}

    def header_values(self, name: str) -> list[str]:
        wanted = name.lower()
        return [v for k, v in self.headers if k.lower() == wanted]

    def header(self, name: str) -> str | None:
        values = self.header_values(name)
        return values[0] if values else None

    def error_code(self) -> str:
        return str(self.error.get("code") or "")

    def error_message(self) -> str:
        return str(self.error.get("message") or "")

    def set_cookie_values(self) -> list[str]:
        return self.header_values("Set-Cookie")


class Client:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.csrf = ""

    def request(
        self,
        method: str,
        path: str,
        body: Any = _MISSING,
        *,
        use_csrf: bool = True,
        csrf_token: Any = _MISSING,
        extra_headers: dict[str, str] | None = None,
        content_type: str = "application/json",
        timeout: float = 30,
    ) -> Resp:
        method = method.upper()
        headers = {"Accept": "application/json", "Connection": "close"}
        data: bytes | None = None
        if method in SAFE_METHODS:
            if body is not _MISSING:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            headers["Content-Type"] = content_type
            if use_csrf:
                token = self.csrf if csrf_token is _MISSING else csrf_token
                if token:
                    headers["X-OpenShop-CSRF"] = str(token)
            data = b"" if body is _MISSING else json.dumps(
                body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=headers
        )
        try:
            with self.opener.open(req, timeout=timeout) as response:
                status = int(response.getcode())
                raw = response.read()
                header_pairs = _header_pairs(response.headers)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read()
            header_pairs = _header_pairs(exc.headers)
        return Resp(status, header_pairs, raw.decode("utf-8", "replace"))

    def _sync_csrf(self) -> None:
        data = None
        # Caller passes the parsed response for success paths.
    def apply_session_payload(self, resp: Resp) -> dict[str, Any]:
        if resp.status == 200 and isinstance(resp.data, dict):
            data = resp.data
            token = data.get("csrf")
            if isinstance(token, str) and token:
                self.csrf = token
            return data
        return {}

    def session(self) -> Resp:
        resp = self.request("GET", "/api/session")
        self.apply_session_payload(resp)
        return resp

    def login_demo(self, role: str = "buyer") -> Resp:
        if not self.csrf:
            self.session()
        resp = self.request("POST", "/api/auth/demo", {"role": role})
        self.apply_session_payload(resp)
        return resp

    def register(self, display_name: str, email: str, password: str, **extra: Any) -> Resp:
        if not self.csrf:
            self.session()
        body = {"display_name": display_name, "email": email, "password": password}
        body.update(extra)
        resp = self.request("POST", "/api/auth/register", body)
        self.apply_session_payload(resp)
        return resp

    def login_password(self, identifier: str, password: str) -> Resp:
        if not self.csrf:
            self.session()
        resp = self.request(
            "POST", "/api/auth/login", {"identifier": identifier, "password": password}
        )
        self.apply_session_payload(resp)
        return resp

    def logout(self) -> Resp:
        resp = self.request("POST", "/api/auth/logout", {})
        self.apply_session_payload(resp)
        return resp

    def cookie_token(self) -> str:
        for cookie in self.jar:
            if cookie.name == "openshop_session":
                return cookie.value
        return ""


def raw_request(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: Any = _MISSING,
    *,
    timeout: float = 30,
) -> Resp:
    """One-shot HTTP request with no redirect, proxy or cookie-jar magic."""
    request_headers = {"Accept": "application/json", "Connection": "close"}
    data: bytes | None = None
    if method.upper() not in SAFE_METHODS or body is not _MISSING:
        data = b"" if body is _MISSING else json.dumps(
            body, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    try:
        conn.request(method.upper(), path, body=data, headers=request_headers)
        response = conn.getresponse()
        status = int(response.status)
        raw = response.read()
        response_headers = list(response.getheaders())
    finally:
        conn.close()
    return Resp(status, [(str(k), str(v)) for k, v in response_headers], raw.decode("utf-8", "replace"))


def raw_with_cookie(
    token: str,
    method: str,
    path: str,
    *,
    csrf: str | None = None,
    body: Any = _MISSING,
    extra_headers: dict[str, str] | None = None,
) -> Resp:
    headers = {"Cookie": f"openshop_session={token}"}
    if csrf:
        headers["X-OpenShop-CSRF"] = csrf
    if extra_headers:
        headers.update(extra_headers)
    return raw_request(method, path, headers, body)


def _run_health() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(BASE_URL + "/api/health", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


class RunningServer:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.data_dir = run_dir / "data"
        self.log_path = run_dir / "server.log"
        self.log_handle: Any = None
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", PORT))
        finally:
            probe.close()
        self.log_handle = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [
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
            ],
            cwd=str(ROOT),
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            if _run_health().get("ok") is True:
                return
            time.sleep(0.2)
        self.proc.wait(timeout=2) if self.proc.poll() is not None else None
        try:
            tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            tail = ""
        print("AUDIT_SETUP verdict=FAIL reason=server_not_ready", flush=True)
        if tail:
            print(tail, flush=True)
        self.stop()
        raise RuntimeError("audit server did not become ready")

    def stop(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
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
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    pass
        if self.log_handle is not None:
            try:
                self.log_handle.close()
            except OSError:
                pass
            self.log_handle = None


class Audit:
    def __init__(self, server: RunningServer) -> None:
        self.server = server
        self.run_dir = server.run_dir
        self.db_path = server.data_dir / "openshop.sqlite3"
        self.log_path = server.log_path
        self.accounts: dict[str, dict[str, Any]] = {}
        self.offers: dict[str, dict[str, Any]] = {}
        self.results: list[dict[str, Any]] = []
        self.secret_values: set[str] = set()
        self.products: list[dict[str, Any]] = []
        self.essential_registration_successes = 0
        self.registration_limit_owner = "Lead (core.py/auth.py)"

    # ------------------------------------------------------------------ I/O
    def db_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()

    def db_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()

    def db_exec(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        probe: str,
        ok: bool,
        detail: str,
        *,
        kind: str = "control",
        finding: str | None = None,
        severity: str | None = None,
        owner: str | None = None,
        coords: str | None = None,
    ) -> None:
        verdict = "PASS" if ok else "FAIL"
        extra = ""
        if finding:
            extra += f" finding={finding}"
        if severity:
            extra += f" severity={severity}"
        if coords:
            extra += f" coords={coords}"
        line = f"PROBE {probe} verdict={verdict} kind={kind}{extra} detail={detail}"
        print(line, flush=True)
        self.results.append(
            {
                "probe": probe,
                "verdict": verdict,
                "kind": kind,
                "finding": finding,
                "severity": severity,
                "owner": owner,
                "coords": coords,
                "detail": detail,
            }
        )

    def expect_probe(
        self,
        probe: str,
        ok: bool,
        detail: str,
        *,
        finding: str,
        severity: str,
        owner: str,
        coords: str,
    ) -> None:
        self.record(
            probe,
            ok,
            detail,
            kind="finding",
            finding=finding,
            severity=severity,
            owner=owner,
            coords=coords,
        )

    def remember(self, value: Any) -> None:
        if isinstance(value, str) and value:
            self.secret_values.add(value)

    # ------------------------------------------------------------- fixtures
    def _register_fixture(self, tag: str, display: str, phone_suffix: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        client = Client()
        first = client.session()
        if first.status != 200:
            raise RuntimeError(f"guest session failed for {tag}")
        email = f"pr-{self.stamp.lower()}-{tag}-{secrets.token_hex(3)}@openshop.test"
        password = "A9!" + secrets.token_urlsafe(18)
        body_extra = dict(extra or {})
        resp = client.register(display, email, password, **body_extra)
        if resp.status != 200 or not isinstance(resp.data, dict):
            raise RuntimeError(f"fixture registration failed for {tag}: http={resp.status} code={resp.error_code()}")
        user = resp.data.get("user") or {}
        user_id = int(user.get("id") or 0)
        if not user_id:
            raise RuntimeError(f"fixture user missing id for {tag}")
        addr = client.request(
            "POST",
            "/api/addresses",
            {
                "label": "审计",
                "receiver": display,
                "phone": f"1380000{phone_suffix:04d}",
                "province": "上海市",
                "city": "上海市",
                "district": "徐汇区",
                "detail": f"审计路 {phone_suffix} 号 3 层",
                "is_default": True,
            },
        )
        if addr.status != 200 or not isinstance(addr.data, dict):
            raise RuntimeError(f"fixture address failed for {tag}: http={addr.status} code={addr.error_code()}")
        self.essential_registration_successes += 1
        data = {
            "tag": tag,
            "client": client,
            "email": email,
            "password": password,
            "user": user,
            "user_id": user_id,
            "address_id": int(addr.data["id"]),
            "register_response": resp,
            "cookie": client.cookie_token(),
            "csrf": client.csrf,
        }
        self.accounts[tag] = data
        self.remember(password)
        self.remember(client.cookie_token())
        self.remember(client.csrf)
        return data

    def _load_offers(self) -> None:
        query = """
            SELECT o.id AS offer_id, o.price_cents, o.stock, o.group_size, o.group_price_cents,
                   o.kind, o.shop_id, p.id AS product_id, p.slug, p.title,
                   s.name AS shop_name,
                   k.id AS sku_id, k.name AS sku_name, k.price_delta_cents
            FROM offers o JOIN products p ON p.id=o.product_id JOIN shops s ON s.id=o.shop_id
            JOIN skus k ON k.product_id=o.product_id
            WHERE o.status='active' AND p.status='active' AND k.sort_order=0
            ORDER BY o.id, k.id
        """
        rows = [dict(row) for row in self.db_all(query)]
        if not rows:
            raise RuntimeError("no active offers in fixture")
        self.all_offers = rows
        def pick(slug: str, kind: str) -> dict[str, Any]:
            candidates = [row for row in rows if row["slug"] == slug and row["kind"] == kind]
            if not candidates:
                raise RuntimeError(f"offer fixture missing: {slug}/{kind}")
            return candidates[0]
        self.offers["order"] = pick("hush-headphones", "self")
        self.offers["group"] = pick("trail-pack", "self")
        self.offers["stock"] = pick("morning-kettle", "self")
        self.offers["cheap"] = pick("morning-kettle", "factory")
        alt_sku = self.db_one(
            "SELECT id FROM skus WHERE product_id=? AND id<>? ORDER BY sort_order,id LIMIT 1",
            (
                int(self.offers["group"]["product_id"]),
                int(self.offers["group"]["sku_id"]),
            ),
        )
        if alt_sku is None:
            raise RuntimeError("group fixture needs a second sku")
        alt_group = dict(self.offers["group"])
        alt_group["sku_id"] = int(alt_sku["id"])
        self.offers["second_group_sku"] = alt_group

    def bootstrap(self) -> None:
        self.stamp = _now_stamp()
        self.product_resp = Client().request("GET", _url("/api/products", page_size=24))
        data = self.product_resp.data
        if self.product_resp.status != 200 or not isinstance(data, dict):
            raise RuntimeError("catalog fixture unavailable")
        self.products = list(data.get("items") or [])
        if not self.products:
            raise RuntimeError("catalog fixture has no products")
        self.slug_primary = str(self.products[0]["slug"])
        self.slug_secondary = str(self.products[1]["slug"]) if len(self.products) > 1 else self.slug_primary
        self._load_offers()
        self.accounts["a"] = self._register_fixture("a", "审计甲", 11, {"role": "seller", "shop_id": 1})
        self.accounts["b"] = self._register_fixture("b", "审计乙", 12)
        self.accounts["c"] = self._register_fixture("c", "审计丙", 13)
        self.demo = {"client": Client()}
        self.demo["client"].session()
        demo_login = self.demo["client"].login_demo("buyer")
        if demo_login.status != 200:
            raise RuntimeError("demo buyer login failed")
        self.demo["user"] = demo_login.data.get("user") if isinstance(demo_login.data, dict) else {}
        self.remember(self.demo["client"].cookie_token())
        self.remember(self.demo["client"].csrf)
        self.seller = {"client": Client()}
        self.seller["client"].session()
        seller_login = self.seller["client"].login_demo("seller")
        if seller_login.status != 200:
            raise RuntimeError("demo seller login failed")
        self.seller["user"] = seller_login.data.get("user") if isinstance(seller_login.data, dict) else {}
        self.remember(self.seller["client"].cookie_token())
        self.remember(self.seller["client"].csrf)
        print(
            "AUDIT_FIXTURE verdict=PASS "
            f"products={len(self.products)} essential_accounts=3 "
            f"offers={','.join(str(v['offer_id']) for v in self.offers.values() if isinstance(v, dict))} "
            f"run_dir={self.run_dir}",
            flush=True,
        )

    # --------------------------------------------------------------- helpers
    def client(self, tag: str) -> Client:
        return self.accounts[tag]["client"]

    def user_id(self, tag: str) -> int:
        return int(self.accounts[tag]["user_id"])

    def address_id(self, tag: str) -> int:
        return int(self.accounts[tag]["address_id"])

    def add_cart(
        self,
        client: Client,
        offer: dict[str, Any],
        *,
        quantity: int = 1,
        sku_id: int | None = None,
        buy_mode: str = "standard",
        group_code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Resp:
        body = {
            "offer_id": int(offer["offer_id"]),
            "sku_id": int(sku_id if sku_id is not None else offer["sku_id"]),
            "quantity": quantity,
            "buy_mode": buy_mode,
        }
        if group_code:
            body["group_code"] = group_code
        if extra:
            body.update(extra)
        return client.request("POST", "/api/cart/items", body)

    def clear_cart(self, client: Client) -> None:
        cart = client.request("GET", "/api/cart")
        items = (cart.data or {}).get("items") if isinstance(cart.data, dict) else []
        for item in items or []:
            client.request("DELETE", f"/api/cart/items/{int(item['id'])}")

    def order_body(self, address_id: int, key: str, **extra: Any) -> dict[str, Any]:
        body = {
            "address_id": int(address_id),
            "coupon_code": None,
            "shipping_method": "standard",
            "note": "",
            "payment_method": "demo",
            "idempotency_key": key,
        }
        body.update(extra)
        return body

    def new_key(self, prefix: str) -> str:
        return f"audit-{self.stamp.lower()}-{prefix}-{secrets.token_hex(5)}"

    def parse_cookie_header(self, header: str) -> http.cookies.Morsel | None:
        try:
            jar = http.cookies.SimpleCookie()
            jar.load(header)
            return next(iter(jar.values())) if jar else None
        except http.cookies.CookieError:
            return None

    # ---------------------------------------------------------------- auth
    def probe_cookie_attributes(self) -> None:
        resp = raw_request("GET", "/api/session")
        cookies = resp.set_cookie_values()
        morsel = self.parse_cookie_header(cookies[0]) if cookies else None
        attrs_ok = bool(
            resp.status == 200
            and morsel is not None
            and morsel["httponly"]
            and morsel["samesite"].lower() == "lax"
            and morsel["path"] == "/"
            and int(morsel["max-age"] or "0") > 0
            and not morsel["domain"]
        )
        secure_on_http = bool(morsel and morsel["secure"])
        self.record(
            "cookie_httponly_samesite_path",
            attrs_ok,
            f"HttpOnly={bool(morsel and morsel['httponly'])} "
            f"SameSite={morsel['samesite'] if morsel else None} "
            f"Path={morsel['path'] if morsel else None} "
            f"Domain={morsel['domain'] if morsel else None} "
            f"MaxAge={morsel['max-age'] if morsel else None} "
            f"SecureOnPlainHTTP={secure_on_http}",
        )
        secure_resp = raw_request("GET", "/api/session", {"X-Forwarded-Proto": "https"})
        secure_cookies = secure_resp.set_cookie_values()
        secure_morsel = self.parse_cookie_header(secure_cookies[0]) if secure_cookies else None
        self.record(
            "cookie_secure_behind_https_proxy",
            bool(secure_resp.status == 200 and secure_morsel is not None and secure_morsel["secure"]),
            f"http={secure_resp.status} secure={bool(secure_morsel and secure_morsel['secure'])}; "
            "plain-HTTP test intentionally cannot set Secure; production TLS env not merged",
        )

    def probe_session_discriminator(self) -> None:
        g1 = Client()
        g2 = Client()
        g1.session()
        g2.session()
        t1, t2 = g1.cookie_token(), g2.cookie_token()
        self.remember(t1)
        self.remember(t2)
        self.record(
            "control_two_guest_sessions_have_distinct_tokens",
            bool(t1 and t2 and t1 != t2),
            f"token_present={bool(t1 and t2)} tokens_differ={bool(t1 and t2 and t1 != t2)}",
        )

    def probe_session_fixation(self) -> None:
        victim = Client()
        victim.session()
        token_before = victim.cookie_token()
        csrf_before = victim.csrf
        old_lookup = raw_with_cookie(token_before, "GET", "/api/session")
        old_before = (old_lookup.data or {}) if isinstance(old_lookup.data, dict) else {}
        login = victim.login_demo("buyer")
        token_after = victim.cookie_token()
        csrf_after = victim.csrf
        self.remember(token_before)
        self.remember(token_after)
        self.remember(csrf_before)
        self.remember(csrf_after)
        old_after = raw_with_cookie(token_before, "GET", "/api/session")
        old_data = (old_after.data or {}) if isinstance(old_after.data, dict) else {}
        old_user = old_data.get("user") or {}
        old_authenticated = bool(isinstance(old_user, dict) and old_user.get("handle"))
        rotation_ok = bool(token_before and token_after and token_before != token_after and not old_authenticated)
        self.expect_probe(
            "auth_session_identifier_rotation",
            rotation_ok,
            f"demo_login={login.status} token_rotated={bool(token_after != token_before)} "
            f"csrf_rotated={bool(csrf_after != csrf_before)} "
            f"old_cookie_before_is_guest={old_before.get('is_guest')} "
            f"old_cookie_after_is_guest={old_data.get('is_guest')} "
            f"old_cookie_authenticated_as={old_user.get('handle') if isinstance(old_user, dict) else None}",
            finding="F-01",
            severity="Low",
            owner="Lead (core.py: auth login/register/demo)",
            coords="apps/storefront/routes/core.py:95-99,121-126,148-151; packages/commerce/auth.py:136-152",
        )

    def probe_csrf(self) -> None:
        client = Client()
        client.session()
        random_token = secrets.token_urlsafe(32)
        good = client.request("POST", "/api/events/view", {"product_slug": self.slug_primary})
        missing = client.request(
            "POST", "/api/events/view", {"product_slug": self.slug_primary}, use_csrf=False
        )
        wrong = client.request(
            "POST",
            "/api/events/view",
            {"product_slug": self.slug_primary},
            csrf_token=random_token,
        )
        control_ok = bool(
            good.status == 200
            and missing.status == 403
            and missing.error_code() == "CSRF_FAILED"
            and wrong.status == 403
            and wrong.error_code() == "CSRF_FAILED"
        )
        self.record(
            "csrf_known_control_positive_and_negative",
            control_ok,
            f"valid={good.status} missing={missing.status}/{missing.error_code()} "
            f"wrong={wrong.status}/{wrong.error_code()}",
        )
        old_csrf = client.csrf
        login = client.login_demo("buyer")
        new_csrf = client.csrf
        old_rejected = client.request(
            "POST",
            "/api/events/view",
            {"product_slug": self.slug_primary},
            csrf_token=old_csrf,
        )
        new_accepted = client.request("POST", "/api/events/view", {"product_slug": self.slug_primary})
        self.remember(old_csrf)
        self.remember(new_csrf)
        self.remember(client.cookie_token())
        rotation_ok = bool(
            login.status == 200
            and old_csrf != new_csrf
            and old_rejected.status == 403
            and old_rejected.error_code() == "CSRF_FAILED"
            and new_accepted.status == 200
        )
        self.record(
            "csrf_rotation_on_authenticated_session",
            rotation_ok,
            f"login={login.status} rotated={bool(old_csrf != new_csrf)} "
            f"old_token={old_rejected.status}/{old_rejected.error_code()} new_token={new_accepted.status}",
        )

    def probe_logout(self) -> None:
        client = Client()
        client.session()
        login = client.login_demo("buyer")
        token = client.cookie_token()
        logout = client.logout()
        old_resp = raw_with_cookie(token, "GET", "/api/orders")
        data = logout.data if isinstance(logout.data, dict) else {}
        control_ok = bool(
            login.status == 200
            and logout.status == 200
            and data.get("is_guest") is True
            and old_resp.status == 401
            and old_resp.error_code() == "AUTH_REQUIRED"
        )
        self.remember(token)
        self.record(
            "session_logout_revokes_old_cookie",
            control_ok,
            f"login={login.status} logout={logout.status} "
            f"logout_is_guest={data.get('is_guest')} old_cookie_orders={old_resp.status}/{old_resp.error_code()}",
        )

    def probe_register_role_injection(self) -> None:
        user = self.accounts["a"]["user"]
        self.record(
            "authz_registration_ignores_injected_role",
            user.get("role") == "buyer" and user.get("shop_id") is None,
            f"requested role=seller shop_id=1 stored_role={user.get('role')} stored_shop_id={user.get('shop_id')}",
        )

    def probe_login_errors(self) -> None:
        probe_client = Client()
        probe_client.session()
        existing = self.accounts["b"]
        missing_id = f"missing-{secrets.token_hex(4)}@openshop.test"
        wrong_password = probe_client.request(
            "POST",
            "/api/auth/login",
            {"identifier": existing["email"], "password": "definitely-wrong-1"},
        )
        unknown_user = probe_client.request(
            "POST",
            "/api/auth/login",
            {"identifier": missing_id, "password": "definitely-wrong-1"},
        )
        same = bool(
            wrong_password.status == unknown_user.status == 401
            and wrong_password.error_code() == unknown_user.error_code() == "INVALID_CREDENTIALS"
            and wrong_password.error_message() == unknown_user.error_message()
        )
        self.record(
            "auth_login_uniform_failure",
            same,
            f"existing={wrong_password.status}/{wrong_password.error_code()} "
            f"missing={unknown_user.status}/{unknown_user.error_code()} same_message={wrong_password.error_message() == unknown_user.error_message()}",
        )

    def probe_password_and_token_storage(self) -> None:
        a = self.accounts["a"]
        b = self.accounts["b"]
        row = self.db_one(
            "SELECT password_hash,password_salt FROM users WHERE id=?", (a["user_id"],)
        )
        b_row = self.db_one(
            "SELECT password_salt FROM users WHERE id=?", (b["user_id"],)
        )
        ok = False
        detail = "user_row_missing"
        if row is not None:
            stored_hash = str(row["password_hash"] or "")
            salt = str(row["password_salt"] or "")
            try:
                derived = base64.b64encode(
                    hashlib.pbkdf2_hmac("sha256", a["password"].encode("utf-8"), bytes.fromhex(salt), 210_000)
                ).decode("ascii")
            except (TypeError, ValueError):
                derived = ""
            salt_ok = bool(re.fullmatch(r"[0-9a-f]{32}", salt))
            ok = bool(
                len(stored_hash) == 44
                and salt_ok
                and stored_hash != a["password"]
                and derived == stored_hash
                and b_row is not None
                and str(b_row["password_salt"] or "") != salt
            )
            detail = f"hash_len={len(stored_hash)} salt_len={len(salt)} salt_hex={salt_ok} plaintext_equal={stored_hash == a['password']} pbkdf2_210k_matches={derived == stored_hash} salts_unique={b_row is not None and str(b_row['password_salt'] or '') != salt}"
        self.record("auth_password_pbkdf2_at_rest", ok, detail)

        token = a["client"].cookie_token()
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        hashed_row = self.db_one(
            "SELECT id FROM sessions WHERE token_hash=?", (token_hash,)
        )
        raw_rows = self.db_all(
            "SELECT id FROM sessions WHERE token_hash=?", (token,)
        )
        registration_text = a["register_response"].text
        forbidden_keys = ("password_hash", "password_salt", "token_hash", "session_id")
        key_leaks = [key for key in forbidden_keys if key in registration_text]
        self.record(
            "auth_session_token_hashed_at_rest",
            bool(hashed_row is not None and not raw_rows and a["password"] not in registration_text and not key_leaks),
            f"sha256_row={hashed_row is not None} raw_token_rows={len(raw_rows)} "
            f"registration_leaks={key_leaks} password_in_response={a['password'] in registration_text}",
        )

    # -------------------------------------------------------------- malformed
    def probe_query_field_flood(self) -> None:
        query = "&".join(f"x{i}=1" for i in range(101))
        resp = raw_request("GET", "/api/products?" + query)
        ok = resp.status < 500
        self.expect_probe(
            "input_query_field_limit",
            ok,
            f"http={resp.status} code={resp.error_code()} expected_4xx_got_server_error={resp.status == 500}",
            finding="F-04",
            severity="Low",
            owner="Lead (packages/commerce/web.py + apps/storefront/server.py)",
            coords="packages/commerce/web.py:77; apps/storefront/server.py:257-258",
        )
        alive = _run_health().get("ok") is True
        self.record(
            "input_query_flood_server_alive",
            alive,
            f"health_ok={alive}",
        )

    def probe_nul_byte_path(self) -> None:
        status: int | None = None
        transport_error = ""
        try:
            resp = raw_request("GET", "/web/%00")
            status = resp.status
        except Exception as exc:
            transport_error = type(exc).__name__
        alive = _run_health().get("ok") is True
        self.expect_probe(
            "input_nul_byte_static_path",
            status is not None and status < 500,
            f"http={status} transport_error={transport_error or None} health_after={alive}",
            finding="F-05",
            severity="Low",
            owner="Lead (apps/storefront/server.py static-file handler)",
            coords="apps/storefront/server.py:264-297 (unquote/path resolve), traceback at line 282",
        )

    # ---------------------------------------------------------- controls/inj
    # ------------------------------------------------------------- IDOR
    def _order_items_from_seed(self) -> tuple[dict[str, Any], dict[str, Any]]:
        client = self.demo["client"]
        listing = client.request("GET", _url("/api/orders", tab="all", page=1, page_size=24))
        items = ((listing.data or {}).get("items") if isinstance(listing.data, dict) else []) or []
        completed = None
        other = None
        for summary in items:
            if summary.get("status") == "COMPLETED" and completed is None:
                completed = summary
            elif summary.get("status") != "COMPLETED" and other is None:
                other = summary
        if completed is None or other is None:
            raise RuntimeError("seed orders missing completed/other")
        comp_detail = client.request("GET", f"/api/orders/{completed['order_no']}")
        other_detail = client.request("GET", f"/api/orders/{other['order_no']}")
        return comp_detail.data or {}, other_detail.data or {}

    def probe_seller_scope(self) -> None:
        seller_client = self.seller["client"]
        resp = seller_client.request("GET", "/api/seller/overview")
        self.record(
            "scope_seller_api_not_merged",
            resp.status == 404,
            f"seller_overview_http={resp.status} code={resp.error_code()} stub=apps/storefront/routes/seller.py:7-9",
            kind="scope",
        )

    def probe_idor(self) -> None:
        a = self.client("a")
        b = self.client("b")
        product_slugs = [str(item.get("slug")) for item in self.products]
        baseline = a.request("GET", "/api/favorites")
        baseline_slugs = {
            str(item.get("slug"))
            for item in ((baseline.data or {}).get("items") if isinstance(baseline.data, dict) else []) or []
        }
        favorite_slug = next(
            (slug for slug in product_slugs if slug not in baseline_slugs),
            product_slugs[0],
        )
        toggled = a.request(
            "POST", "/api/favorites/" + _quote_path(favorite_slug) + "/toggle", {}
        )
        a_favs = a.request("GET", "/api/favorites")
        b_favs = b.request("GET", "/api/favorites")
        a_slugs = {
            str(item.get("slug"))
            for item in ((a_favs.data or {}).get("items") if isinstance(a_favs.data, dict) else []) or []
        }
        b_slugs = {
            str(item.get("slug"))
            for item in ((b_favs.data or {}).get("items") if isinstance(b_favs.data, dict) else []) or []
        }
        if toggled.status == 200:
            a.request("POST", "/api/favorites/" + _quote_path(favorite_slug) + "/toggle", {})
        self.record(
            "authz_favorites_session_scope",
            toggled.status == 200 and favorite_slug in a_slugs and favorite_slug not in b_slugs,
            f"toggle={toggled.status} a_has={favorite_slug in a_slugs} b_has={favorite_slug in b_slugs}",
        )

        guest1 = Client()
        guest2 = Client()
        guest1.session()
        guest2.session()
        self.remember(guest1.cookie_token())
        self.remember(guest2.cookie_token())
        self.remember(guest1.csrf)
        self.remember(guest2.csrf)
        event = guest1.request("POST", "/api/events/view", {"product_slug": self.slug_secondary})
        h1 = guest1.request("GET", "/api/home")
        h2 = guest2.request("GET", "/api/home")
        recent1 = ((h1.data or {}).get("recently_viewed") if isinstance(h1.data, dict) else []) or []
        recent2 = ((h2.data or {}).get("recently_viewed") if isinstance(h2.data, dict) else []) or []
        r1_slugs = {str(item.get("slug")) for item in recent1}
        r2_slugs = {str(item.get("slug")) for item in recent2}
        self.record(
            "authz_browse_events_session_scope",
            event.status == 200 and self.slug_secondary in r1_slugs and self.slug_secondary not in r2_slugs,
            f"view={event.status} viewer_has={self.slug_secondary in r1_slugs} other_has={self.slug_secondary in r2_slugs}",
        )

        address_id = self.address_id("a")
        foreign_patch = b.request("PATCH", f"/api/addresses/{address_id}", {"label": "越权"})
        foreign_delete = b.request("DELETE", f"/api/addresses/{address_id}")
        a_addresses = a.request("GET", "/api/addresses")
        address_list = ((a_addresses.data or {}).get("items") if isinstance(a_addresses.data, dict) else []) or []
        self.clear_cart(b)
        add_for_address = self.add_cart(b, self.offers["order"], quantity=1)
        wrong_address_key = self.new_key("foreign-address")
        foreign_address_order = b.request(
            "POST",
            "/api/orders",
            self.order_body(address_id, wrong_address_key),
        )
        foreign_address_rows = self.db_one(
            "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
            (self.user_id("b"), wrong_address_key),
        )
        self.record(
            "authz_addresses_owner_only",
            foreign_patch.status == 404
            and foreign_delete.status == 404
            and any(int(item.get("id") or 0) == address_id for item in address_list)
            and foreign_address_order.status in {400, 404}
            and int(foreign_address_rows["n"] if foreign_address_rows else -1) == 0,
            f"foreign_patch={foreign_patch.status}/{foreign_patch.error_code()} "
            f"foreign_delete={foreign_delete.status}/{foreign_delete.error_code()} "
            f"foreign_address_order={foreign_address_order.status}/{foreign_address_order.error_code()} "
            f"order_rows={int(foreign_address_rows['n']) if foreign_address_rows else None} add={add_for_address.status}",
        )
        self.clear_cart(b)

        self.clear_cart(a)
        add_cart = self.add_cart(a, self.offers["order"], quantity=1)
        cart_data = add_cart.data if isinstance(add_cart.data, dict) else {}
        cart_items = cart_data.get("items") or []
        if not cart_items:
            self.record(
                "authz_cart_items_owner_only",
                False,
                f"cart_fixture_failed http={add_cart.status}/{add_cart.error_code()}",
            )
        else:
            cart_item_id = int(cart_items[0]["id"])
            foreign_cart_patch = b.request(
                "PATCH", f"/api/cart/items/{cart_item_id}", {"quantity": 2}
            )
            foreign_cart_delete = b.request("DELETE", f"/api/cart/items/{cart_item_id}")
            a_cart = a.request("GET", "/api/cart")
            a_cart_items = ((a_cart.data or {}).get("items") if isinstance(a_cart.data, dict) else []) or []
            self.record(
                "authz_cart_items_owner_only",
                foreign_cart_patch.status == 404
                and foreign_cart_delete.status == 404
                and any(int(item.get("id") or 0) == cart_item_id for item in a_cart_items),
                f"foreign_patch={foreign_cart_patch.status}/{foreign_cart_patch.error_code()} "
                f"foreign_delete={foreign_cart_delete.status}/{foreign_cart_delete.error_code()} "
                f"owner_item_still_present={any(int(item.get('id') or 0) == cart_item_id for item in a_cart_items)}",
            )

        self.clear_cart(a)
        a_add = self.add_cart(a, self.offers["order"], quantity=1)
        order_key = self.new_key("idor-order")
        a_order = a.request("POST", "/api/orders", self.order_body(self.address_id("a"), order_key))
        order_data = a_order.data if isinstance(a_order.data, dict) else {}
        order_no = str(order_data.get("order_no") or "")
        review_entry = {
            "item_id": 1,
            "rating": 5,
            "title": "越权",
            "body": "这是跨用户订单评价的探针内容，不应写入。",
            "tags": [],
        }
        b_get = b.request("GET", f"/api/orders/{order_no}") if order_no else Resp(0, [], "")
        b_pay = b.request("POST", f"/api/orders/{order_no}/pay", {"payment_method": "demo"}) if order_no else Resp(0, [], "")
        b_cancel = b.request("POST", f"/api/orders/{order_no}/cancel", {"reason": "越权"}) if order_no else Resp(0, [], "")
        b_refund = b.request("POST", f"/api/orders/{order_no}/refund", {"reason": "越权", "kind": "refund_only"}) if order_no else Resp(0, [], "")
        b_review = b.request("POST", f"/api/orders/{order_no}/reviews", {"reviews": [review_entry]}) if order_no else Resp(0, [], "")
        b_list = b.request("GET", _url("/api/orders", tab="all", page=1, page_size=24))
        b_order_numbers = {
            str(summary.get("order_no"))
            for summary in ((b_list.data or {}).get("items") if isinstance(b_list.data, dict) else []) or []
        }
        a_get = a.request("GET", f"/api/orders/{order_no}") if order_no else Resp(0, [], "")
        self.record(
            "authz_orders_and_actions_owner_only",
            bool(
                a_add.status == 200
                and a_order.status == 200
                and a_get.status == 200
                and b_get.status == 404
                and b_pay.status == 404
                and b_cancel.status == 404
                and b_refund.status == 404
                and b_review.status == 404
                and order_no not in b_order_numbers
            ),
            f"create={a_order.status} b_get={b_get.status} b_pay={b_pay.status} "
            f"b_cancel={b_cancel.status} b_refund={b_refund.status} b_review={b_review.status} "
            f"b_list_contains={order_no in b_order_numbers}",
        )

        completed, other = self._order_items_from_seed()
        completed_no = str(completed.get("order_no") or "")
        completed_items = [
            item
            for package in completed.get("packages") or []
            for item in package.get("items") or []
        ]
        other_items = [
            item
            for package in other.get("packages") or []
            for item in package.get("items") or []
        ]
        if completed_no and completed_items and other_items:
            completed_item_id = int(completed_items[0]["id"])
            review_valid = {
                "item_id": completed_item_id,
                "rating": 5,
                "title": "越权评价",
                "body": "这是针对他人订单提交的越权评价探针内容，不应成功。",
                "tags": [],
            }
            foreign_review = b.request(
                "POST", f"/api/orders/{completed_no}/reviews", {"reviews": [review_valid]}
            )
            wrong_item = {
                "item_id": int(other_items[0]["id"]),
                "rating": 5,
                "title": "错单商品",
                "body": "这是把另一订单商品塞进当前订单的越权评价探针内容。",
                "tags": [],
            }
            wrong_item_review = self.demo["client"].request(
                "POST", f"/api/orders/{completed_no}/reviews", {"reviews": [wrong_item]}
            )
            self.record(
                "authz_reviews_order_and_item_scope",
                foreign_review.status == 404
                and wrong_item_review.status == 400
                and wrong_item_review.error_code() == "VALIDATION_ERROR",
                f"foreign_order_review={foreign_review.status}/{foreign_review.error_code()} "
                f"foreign_item_review={wrong_item_review.status}/{wrong_item_review.error_code()}",
            )
        else:
            self.record(
                "authz_reviews_order_and_item_scope",
                False,
                f"seed_orders_incomplete completed={bool(completed_no)} completed_items={len(completed_items)} other_items={len(other_items)}",
            )

    def probe_coupon_owner(self) -> None:
        a = self.client("a")
        b = self.client("b")
        claim = a.request("POST", "/api/coupons/WEEKEND30/claim", {})
        self.clear_cart(b)
        add = self.add_cart(b, self.offers["order"], quantity=1)
        quote = b.request(
            "POST",
            "/api/checkout/quote",
            {"shipping_method": "standard", "coupon_code": "WEEKEND30"},
        )
        quote_data = quote.data if isinstance(quote.data, dict) else {}
        create_key = self.new_key("foreign-coupon")
        create = b.request(
            "POST",
            "/api/orders",
            self.order_body(self.address_id("b"), create_key, coupon_code="WEEKEND30"),
        )
        coupon_error = bool(quote_data.get("coupon") is None and quote_data.get("coupon_error"))
        rows = self.db_one(
            "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
            (self.user_id("b"), create_key),
        )
        self.record(
            "authz_coupon_owner_only",
            claim.status in {200, 409}
            and add.status == 200
            and quote.status == 200
            and coupon_error
            and create.status == 422
            and create.error_code() == "COUPON_INVALID"
            and int(rows["n"] if rows else -1) == 0,
            f"claim={claim.status}/{claim.error_code()} add={add.status} quote={quote.status} "
            f"coupon_error={coupon_error} create={create.status}/{create.error_code()} order_rows={int(rows['n']) if rows else None}",
        )
        self.clear_cart(b)

    # -------------------------------------------------------- trade authority
    def _raw_account_order(self, tag: str, body: dict[str, Any]) -> Resp:
        acct = self.accounts[tag]
        token = acct["client"].cookie_token()
        csrf = acct["client"].csrf
        if not token or not csrf:
            raise RuntimeError(f"missing authenticated session for {tag}")
        return raw_with_cookie(token, "POST", "/api/orders", csrf=csrf, body=body)

    def _concurrent_account_order(self, tag: str, body: dict[str, Any], barrier: threading.Barrier) -> Resp | Exception:
        try:
            barrier.wait(timeout=15)
            return self._raw_account_order(tag, body)
        except Exception as exc:
            return exc

    @staticmethod
    def _as_result(result: Resp | Exception) -> tuple[int, str, str]:
        if isinstance(result, Resp):
            return result.status, result.error_code(), str((result.data or {}).get("order_no") or "") if isinstance(result.data, dict) else ""
        return 0, type(result).__name__, ""

    def _my_coupon(self, client: Client, code: str) -> dict[str, Any] | None:
        resp = client.request("GET", "/api/coupons")
        mine = ((resp.data or {}).get("mine") if isinstance(resp.data, dict) else []) or []
        return next((entry for entry in mine if entry.get("code") == code), None)

    def probe_forged_price_and_quantity(self) -> None:
        b = self.client("b")
        self.clear_cart(b)
        offer = self.offers["order"]
        quantity = 2
        expected_unit = int(offer["price_cents"]) + int(offer["price_delta_cents"])
        stock_before_row = self.db_one("SELECT stock FROM offers WHERE id=?", (int(offer["offer_id"]),))
        stock_before = int(stock_before_row["stock"]) if stock_before_row else -1
        add = self.add_cart(
            b,
            offer,
            quantity=quantity,
            extra={
                "price_cents": 1,
                "unit_price_cents": 1,
                "line_price_cents": 1,
                "subtotal_cents": 1,
                "total_cents": 1,
                "discount_cents": 9_999_999,
            },
        )
        add_data = add.data if isinstance(add.data, dict) else {}
        items = add_data.get("items") or []
        if add.status != 200 or not items:
            self.record(
                "server_price_authority",
                False,
                f"cart_fixture_http={add.status} code={add.error_code()}",
            )
            return
        server_unit = int(items[0]["offer"]["unit_price_cents"])
        server_line = int(items[0]["line_price_cents"])
        quote = b.request(
            "POST", "/api/checkout/quote", {"shipping_method": "standard"}
        )
        quote_data = quote.data if isinstance(quote.data, dict) else {}
        quote_total = int(quote_data.get("total_cents") or -1)
        key = self.new_key("price-forge")
        create = b.request(
            "POST",
            "/api/orders",
            self.order_body(
                self.address_id("b"),
                key,
                subtotal_cents=1,
                total_cents=1,
                discount_cents=9_999_999,
                unit_price_cents=1,
                line_price_cents=1,
            ),
        )
        create_data = create.data if isinstance(create.data, dict) else {}
        reported_total = int(create_data.get("total_cents") or -1)
        db_order = self.db_one(
            "SELECT total_cents,subtotal_cents FROM orders WHERE user_id=? AND idempotency_key=?",
            (self.user_id("b"), key),
        )
        stock_after_row = self.db_one("SELECT stock FROM offers WHERE id=?", (int(offer["offer_id"]),))
        stock_after = int(stock_after_row["stock"]) if stock_after_row else -1
        ok = bool(
            add.status == 200
            and server_unit == expected_unit
            and server_line == expected_unit * quantity
            and quote.status == 200
            and create.status == 200
            and reported_total == quote_total
            and db_order is not None
            and int(db_order["total_cents"]) == quote_total
            and reported_total != 1
            and stock_before - stock_after == quantity
        )
        self.record(
            "server_price_authority",
            ok,
            f"add={add.status} unit={server_unit} expected={expected_unit} line={server_line} "
            f"quote={quote.status}/{quote_total} create={create.status}/{reported_total} "
            f"db_total={int(db_order['total_cents']) if db_order else None} "
            f"stock_delta={stock_before - stock_after if stock_before >= 0 and stock_after >= 0 else None}",
        )

        self.clear_cart(b)
        negative = self.add_cart(b, offer, quantity=-1)
        zero = self.add_cart(b, offer, quantity=0)
        current_stock_row = self.db_one("SELECT stock FROM offers WHERE id=?", (int(offer["offer_id"]),))
        current_stock = int(current_stock_row["stock"]) if current_stock_row else 9999
        oversell = self.add_cart(b, offer, quantity=min(9999, max(2, current_stock + 1)))
        mismatch = self.add_cart(b, offer, sku_id=int(self.offers["cheap"]["sku_id"]))
        added = self.add_cart(b, offer, quantity=1)
        added_items = ((added.data or {}).get("items") if isinstance(added.data, dict) else []) or []
        patch_negative = Resp(0, [], "")
        if added_items:
            patch_negative = b.request(
                "PATCH", f"/api/cart/items/{int(added_items[0]['id'])}", {"quantity": -1}
            )
        self.record(
            "server_quantity_and_sku_rejection",
            negative.status == 400
            and zero.status == 400
            and oversell.status == 409
            and oversell.error_code() == "STOCK_CHANGED"
            and mismatch.status == 404
            and patch_negative.status == 400,
            f"negative_qty={negative.status}/{negative.error_code()} zero_qty={zero.status}/{zero.error_code()} "
            f"over_stock={oversell.status}/{oversell.error_code()} mismatched_sku={mismatch.status}/{mismatch.error_code()} "
            f"patch_negative={patch_negative.status}/{patch_negative.error_code()}",
        )
        self.clear_cart(b)

    def probe_idempotency(self) -> None:
        c = self.client("c")
        self.clear_cart(c)
        offer = self.offers["order"]
        added = self.add_cart(c, offer, quantity=1)
        stock_before_row = self.db_one("SELECT stock FROM offers WHERE id=?", (int(offer["offer_id"]),))
        stock_before = int(stock_before_row["stock"]) if stock_before_row else -1
        key = self.new_key("idempotency")
        body = self.order_body(self.address_id("c"), key, note="idempotency-audit")
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_one = pool.submit(self._concurrent_account_order, "c", body, barrier)
            future_two = pool.submit(self._concurrent_account_order, "c", body, barrier)
            result_one = future_one.result(timeout=45)
            result_two = future_two.result(timeout=45)
        status_one, error_one, order_one = self._as_result(result_one)
        status_two, error_two, order_two = self._as_result(result_two)
        rows = self.db_one(
            "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
            (self.user_id("c"), key),
        )
        stock_after_row = self.db_one("SELECT stock FROM offers WHERE id=?", (int(offer["offer_id"]),))
        stock_after = int(stock_after_row["stock"]) if stock_after_row else -1
        conflict_body = dict(body)
        conflict_body["note"] = "idempotency-audit-mutated"
        conflict = c.request("POST", "/api/orders", conflict_body)
        ok = bool(
            added.status == 200
            and status_one == status_two == 200
            and order_one
            and order_one == order_two
            and rows is not None
            and int(rows["n"]) == 1
            and stock_before - stock_after == 1
            and conflict.status == 409
            and conflict.error_code() == "IDEMPOTENCY_CONFLICT"
        )
        self.record(
            "idempotency_replay_conflict_and_stock_once",
            ok,
            f"add={added.status} concurrent={status_one},{status_two} errors={error_one or None},{error_two or None} "
            f"same_order={order_one == order_two and bool(order_one)} ordered_rows={int(rows['n']) if rows else None} "
            f"stock_delta={stock_before - stock_after if stock_before >= 0 and stock_after >= 0 else None} "
            f"conflict={conflict.status}/{conflict.error_code()}",
        )

    def probe_coupon_controls(self) -> None:
        a = self.client("a")
        b = self.client("b")
        mine_weekend = self._my_coupon(a, "WEEKEND30")
        if mine_weekend is None:
            claim = a.request("POST", "/api/coupons/WEEKEND30/claim", {})
            WEEKEND_CLAIM = claim.status
        else:
            WEEKEND_CLAIM = 0
        mine_weekend = self._my_coupon(a, "WEEKEND30")
        self.clear_cart(a)
        cheap_add = self.add_cart(a, self.offers["cheap"], quantity=1)
        cheap_data = cheap_add.data if isinstance(cheap_add.data, dict) else {}
        cheap_subtotal = int(((cheap_data.get("summary") or {}) if isinstance(cheap_data, dict) else {}).get("subtotal_cents") or -1)
        threshold_quote = a.request(
            "POST",
            "/api/checkout/quote",
            {"shipping_method": "standard", "coupon_code": "WEEKEND30"},
        )
        threshold_quote_data = threshold_quote.data if isinstance(threshold_quote.data, dict) else {}
        threshold_key = self.new_key("coupon-threshold")
        threshold_create = a.request(
            "POST",
            "/api/orders",
            self.order_body(self.address_id("a"), threshold_key, coupon_code="WEEKEND30"),
        )
        threshold_rows = self.db_one(
            "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
            (self.user_id("a"), threshold_key),
        )
        threshold_ok = bool(
            mine_weekend is not None
            and cheap_add.status == 200
            and cheap_subtotal >= 0
            and cheap_subtotal < 29900
            and threshold_quote.status == 200
            and threshold_quote_data.get("coupon") is None
            and threshold_quote_data.get("coupon_error")
            and threshold_create.status == 422
            and threshold_create.error_code() == "COUPON_INVALID"
            and int(threshold_rows["n"] if threshold_rows else -1) == 0
        )
        self.record(
            "coupon_threshold_enforced",
            threshold_ok,
            f"claim={WEEKEND_CLAIM} subtotal={cheap_subtotal} quote={threshold_quote.status} "
            f"coupon_null={threshold_quote_data.get('coupon') is None} "
            f"coupon_error={bool(threshold_quote_data.get('coupon_error'))} "
            f"create={threshold_create.status}/{threshold_create.error_code()} "
            f"order_rows={int(threshold_rows['n']) if threshold_rows else None}",
        )
        self.clear_cart(a)

        mine_hello = self._my_coupon(a, "HELLO20")
        if mine_hello is None:
            first_claim = a.request("POST", "/api/coupons/HELLO20/claim", {})
        else:
            first_claim = Resp(0, [], "")
        self.clear_cart(a)
        first_add = self.add_cart(a, self.offers["order"], quantity=1)
        first_key = self.new_key("coupon-first")
        first_order = a.request(
            "POST",
            "/api/orders",
            self.order_body(self.address_id("a"), first_key, coupon_code="HELLO20"),
        )
        self.clear_cart(a)
        second_add = self.add_cart(a, self.offers["order"], quantity=1)
        used_quote = a.request(
            "POST",
            "/api/checkout/quote",
            {"shipping_method": "standard", "coupon_code": "HELLO20"},
        )
        used_quote_data = used_quote.data if isinstance(used_quote.data, dict) else {}
        second_key = self.new_key("coupon-second")
        second_order = a.request(
            "POST",
            "/api/orders",
            self.order_body(self.address_id("a"), second_key, coupon_code="HELLO20"),
        )
        second_rows = self.db_one(
            "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
            (self.user_id("a"), second_key),
        )
        used_row = self.db_one(
            """SELECT uc.status,uc.order_id FROM user_coupons uc JOIN users u ON u.id=uc.user_id
               WHERE u.id=? AND uc.coupon_code=?""",
            (self.user_id("a"), "HELLO20"),
        )
        sequential_ok = bool(
            first_add.status == 200
            and first_order.status == 200
            and second_add.status == 200
            and used_quote.status == 200
            and used_quote_data.get("coupon") is None
            and used_quote_data.get("coupon_error")
            and second_order.status == 422
            and second_order.error_code() == "COUPON_INVALID"
            and used_row is not None
            and str(used_row["status"]) == "USED"
            and used_row["order_id"] is not None
            and int(second_rows["n"] if second_rows else -1) == 0
        )
        self.record(
            "coupon_single_use_sequential",
            sequential_ok,
            f"first_claim={first_claim.status} first_create={first_order.status} second_add={second_add.status} "
            f"used_quote={used_quote.status} coupon_error={bool(used_quote_data.get('coupon_error'))} "
            f"second_create={second_order.status}/{second_order.error_code()} "
            f"coupon_status={used_row['status'] if used_row else None} order_rows={int(second_rows['n']) if second_rows else None}",
        )
        self.clear_cart(a)

        b_hello = self._my_coupon(b, "HELLO20")
        self.clear_cart(b)
        add = self.add_cart(b, self.offers["order"], quantity=1)
        key_one = self.new_key("coupon-race-a")
        key_two = self.new_key("coupon-race-b")
        body_one = self.order_body(self.address_id("b"), key_one, coupon_code="HELLO20", note="coupon-race-a")
        body_two = self.order_body(self.address_id("b"), key_two, coupon_code="HELLO20", note="coupon-race-b")
        coupon_stock_before_row = self.db_one(
            "SELECT stock FROM offers WHERE id=?", (int(self.offers["order"]["offer_id"]),)
        )
        cs_before = int(coupon_stock_before_row["stock"]) if coupon_stock_before_row else -1
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._concurrent_account_order, "b", body_one, barrier),
                pool.submit(self._concurrent_account_order, "b", body_two, barrier),
            ]
            results = [future.result(timeout=45) for future in futures]
        summaries = [self._as_result(result) for result in results]
        db_rows = self.db_one(
            "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key IN (?,?)",
            (self.user_id("b"), key_one, key_two),
        )
        b_coupon = self.db_one(
            """SELECT uc.status,uc.order_id FROM user_coupons uc JOIN users u ON u.id=uc.user_id
               WHERE u.id=? AND uc.coupon_code=?""",
            (self.user_id("b"), "HELLO20"),
        )
        cs_after_row = self.db_one(
            "SELECT stock FROM offers WHERE id=?", (int(self.offers["order"]["offer_id"]),)
        )
        cs_after = int(cs_after_row["stock"]) if cs_after_row else -1
        race_ok = bool(
            add.status == 200
            and b_hello is not None
            and b_hello.get("status") == "AVAILABLE"
            and sum(1 for summary in summaries if summary[0] == 200) == 1
            and db_rows is not None
            and int(db_rows["n"]) == 1
            and b_coupon is not None
            and str(b_coupon["status"]) == "USED"
            and b_coupon["order_id"] is not None
            and cs_before - cs_after == 1
        )
        self.record(
            "coupon_concurrent_single_use",
            race_ok,
            f"add={add.status} statuses={[summary[0] for summary in summaries]} "
            f"errors={[summary[1] for summary in summaries]} ordered_rows={int(db_rows['n']) if db_rows else None} "
            f"coupon_status={b_coupon['status'] if b_coupon else None} "
            f"coupon_order_linked={bool(b_coupon and b_coupon['order_id'] is not None)} stock_delta={cs_before - cs_after}",
        )
        self.clear_cart(b)

    def probe_order_state(self) -> None:
        a = self.client("a")
        self.clear_cart(a)
        add = self.add_cart(a, self.offers["order"], quantity=1)
        key = self.new_key("state")
        created = a.request(
            "POST", "/api/orders", self.order_body(self.address_id("a"), key)
        )
        data = created.data if isinstance(created.data, dict) else {}
        order_no = str(data.get("order_no") or "")
        item_id = 0
        for package in data.get("packages") or []:
            for item in package.get("items") or []:
                item_id = int(item.get("id") or 0)
                break
            if item_id:
                break
        if created.status != 200 or not order_no or not item_id:
            self.record(
                "order_state_machine_rejects_illegal_transitions",
                False,
                f"order_fixture_http={created.status} code={created.error_code()} order_no={bool(order_no)} item_id={item_id}",
            )
            return
        confirm_pre = a.request("POST", f"/api/orders/{order_no}/confirm")
        refund_pre = a.request(
            "POST",
            f"/api/orders/{order_no}/refund",
            {"reason": "未支付售后探针", "kind": "refund_only"},
        )
        pay_cash = a.request(
            "POST", f"/api/orders/{order_no}/pay", {"payment_method": "cash"}
        )
        pay_ok = a.request(
            "POST", f"/api/orders/{order_no}/pay", {"payment_method": "demo"}
        )
        pay_again = a.request(
            "POST", f"/api/orders/{order_no}/pay", {"payment_method": "demo"}
        )
        cancel_paid = a.request(
            "POST", f"/api/orders/{order_no}/cancel", {"reason": "已支付取消探针"}
        )
        confirm_unshipped = a.request("POST", f"/api/orders/{order_no}/confirm")
        review_pre = a.request(
            "POST",
            f"/api/orders/{order_no}/reviews",
            {
                "reviews": [
                    {
                        "item_id": item_id,
                        "rating": 5,
                        "title": "未完成评价",
                        "body": "订单尚未完成时提交评价的探针内容，不应成功。",
                        "tags": [],
                    }
                ]
            },
        )
        detail = a.request("GET", f"/api/orders/{order_no}")
        final_status = (detail.data or {}).get("status") if isinstance(detail.data, dict) else None
        ok = bool(
            add.status == 200
            and confirm_pre.status == 409
            and confirm_pre.error_code() == "ORDER_STATE"
            and refund_pre.status == 409
            and refund_pre.error_code() == "ORDER_STATE"
            and pay_cash.status == 400
            and pay_cash.error_code() == "VALIDATION_ERROR"
            and pay_ok.status == 200
            and pay_again.status == 409
            and pay_again.error_code() == "ORDER_STATE"
            and cancel_paid.status == 409
            and cancel_paid.error_code() == "ORDER_STATE"
            and confirm_unshipped.status == 409
            and confirm_unshipped.error_code() == "ORDER_STATE"
            and review_pre.status == 409
            and review_pre.error_code() == "ORDER_STATE"
            and final_status == "PAID"
        )
        self.record(
            "order_state_machine_rejects_illegal_transitions",
            ok,
            f"add={add.status} confirm_pre={confirm_pre.status}/{confirm_pre.error_code()} "
            f"refund_pre={refund_pre.status}/{refund_pre.error_code()} pay_cash={pay_cash.status}/{pay_cash.error_code()} "
            f"pay={pay_ok.status} pay_again={pay_again.status}/{pay_again.error_code()} "
            f"cancel_paid={cancel_paid.status}/{cancel_paid.error_code()} "
            f"confirm_unshipped={confirm_unshipped.status}/{confirm_unshipped.error_code()} "
            f"review_pre={review_pre.status}/{review_pre.error_code()} final={final_status}",
        )

    def probe_stock_concurrency(self) -> None:
        a = self.client("a")
        b = self.client("b")
        offer = self.offers["stock"]
        original_row = self.db_one("SELECT stock FROM offers WHERE id=?", (int(offer["offer_id"]),))
        original_stock = int(original_row["stock"]) if original_row else None
        self.db_exec("UPDATE offers SET stock=? WHERE id=?", (1, int(offer["offer_id"])))
        self.clear_cart(a)
        self.clear_cart(b)
        add_a = self.add_cart(a, offer, quantity=1)
        add_b = self.add_cart(b, offer, quantity=1)
        key_a = self.new_key("stock-race-a")
        key_b = self.new_key("stock-race-b")
        body_a = self.order_body(self.address_id("a"), key_a)
        body_b = self.order_body(self.address_id("b"), key_b)
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._concurrent_account_order, "a", body_a, barrier),
                pool.submit(self._concurrent_account_order, "b", body_b, barrier),
            ]
            results = [future.result(timeout=45) for future in futures]
        summaries = [self._as_result(result) for result in results]
        stock_after_row = self.db_one("SELECT stock FROM offers WHERE id=?", (int(offer["offer_id"]),))
        stock_after = int(stock_after_row["stock"]) if stock_after_row else -1
        order_rows = self.db_one(
            "SELECT COUNT(*) AS n FROM orders WHERE idempotency_key IN (?,?)",
            (key_a, key_b),
        )
        one_success = sum(1 for summary in summaries if summary[0] == 200) == 1
        loser_code = next((summary[1] for summary in summaries if summary[0] != 200), "")
        ok = bool(
            add_a.status == 200
            and add_b.status == 200
            and one_success
            and loser_code == "STOCK_CHANGED"
            and stock_after == 0
            and order_rows is not None
            and int(order_rows["n"]) == 1
        )
        self.record(
            "stock_concurrent_no_oversell",
            ok,
            f"fixture_offer={int(offer['offer_id'])} stock_before=1 seed_stock={original_stock} "
            f"adds={add_a.status},{add_b.status} order_statuses={[summary[0] for summary in summaries]} "
            f"errors={[summary[1] for summary in summaries]} stock_after={stock_after} order_rows={int(order_rows['n']) if order_rows else None}",
        )

    # --------------------------------------------------------------- groups
    def group_data(self, code: str) -> tuple[Resp, dict[str, Any]]:
        resp = raw_request("GET", "/api/groups/" + urllib.parse.quote(code, safe=""))
        return resp, (resp.data if isinstance(resp.data, dict) else {})

    def demo_address_id(self) -> int:
        resp = self.demo["client"].request("GET", "/api/addresses")
        items = ((resp.data or {}).get("items") if isinstance(resp.data, dict) else []) or []
        if not items:
            raise RuntimeError("demo buyer has no seed address")
        return int(items[0]["id"])

    def create_group_order(self, tag: str, tag_label: str) -> tuple[str, Resp]:
        client = self.client(tag) if tag != "demo" else self.demo["client"]
        address_id = self.demo_address_id() if tag == "demo" else self.address_id(tag)
        self.clear_cart(client)
        added = self.add_cart(client, self.offers["group"], quantity=1, buy_mode="group")
        key = self.new_key(tag_label)
        created = client.request(
            "POST", "/api/orders", self.order_body(address_id, key)
        )
        data = created.data if isinstance(created.data, dict) else {}
        group = data.get("group") or {}
        return str(group.get("code") or ""), created

    def probe_groups(self) -> None:
        a = self.client("a")
        b = self.client("b")
        c = self.client("c")
        group_offer = self.offers["group"]
        for client in (a, b, c):
            self.clear_cart(client)
        self.clear_cart(self.demo["client"])

        a_code, a_created = self.create_group_order("a", "group-a")
        a_data = a_created.data if isinstance(a_created.data, dict) else {}
        a_order_no = str(a_data.get("order_no") or "")
        initial_resp, initial = self.group_data(a_code) if a_code else (Resp(0, [], ""), {})
        if a_created.status != 200 or not a_code or not a_order_no:
            self.record(
                "group_capacity_race_and_paid_progress",
                False,
                f"group_fixture_http={a_created.status}/{a_created.error_code()} code={bool(a_code)} order={bool(a_order_no)}",
            )
            return
        pay_a = a.request("POST", f"/api/orders/{a_order_no}/pay", {"payment_method": "demo"})
        after_a_resp, after_a = self.group_data(a_code)
        self_join = self.add_cart(a, group_offer, quantity=1, buy_mode="group", group_code=a_code)
        if self_join.status == 200:
            self.clear_cart(a)

        demo_code, demo_created = self.create_group_order("demo", "group-demo")
        demo_data_ok = demo_created.status == 200 and bool(demo_code)

        b_add = self.add_cart(b, group_offer, quantity=1, buy_mode="group", group_code=a_code)
        cross_group = (
            self.add_cart(b, group_offer, quantity=1, buy_mode="group", group_code=demo_code)
            if demo_code
            else None
        )
        cross_sku = self.add_cart(
            b,
            self.offers["second_group_sku"],
            quantity=1,
            buy_mode="group",
            group_code=a_code,
        )
        c_add = self.add_cart(c, group_offer, quantity=1, buy_mode="group", group_code=a_code)

        b_key = self.new_key("group-b")
        c_key = self.new_key("group-c")
        b_body = self.order_body(self.address_id("b"), b_key)
        c_body = self.order_body(self.address_id("c"), c_key)
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self._concurrent_account_order, "b", b_body, barrier),
                pool.submit(self._concurrent_account_order, "c", c_body, barrier),
            ]
            results = [future.result(timeout=45) for future in futures]
        summaries = [self._as_result(result) for result in results]
        success_indexes = [index for index, summary in enumerate(summaries) if summary[0] == 200]
        winner_tag = "b" if success_indexes == [0] else "c" if success_indexes == [1] else ""
        winner_result = results[success_indexes[0]] if len(success_indexes) == 1 else None
        winner_data = winner_result.data if isinstance(winner_result, Resp) and isinstance(winner_result.data, dict) else {}
        winner_order_no = str(winner_data.get("order_no") or "")
        pending_public_resp, pending_public = self.group_data(a_code)
        winner_pay = (
            self.client(winner_tag).request(
                "POST", f"/api/orders/{winner_order_no}/pay", {"payment_method": "demo"}
            )
            if winner_tag and winner_order_no
            else Resp(0, [], "")
        )
        final_public_resp, final_public = self.group_data(a_code)
        a_final = a.request("GET", f"/api/orders/{a_order_no}")
        a_final_status = (a_final.data or {}).get("status") if isinstance(a_final.data, dict) else None
        db_counts = self.db_one(
            """SELECT COUNT(*) AS members,
                      SUM(CASE WHEN gm.paid_at IS NOT NULL THEN 1 ELSE 0 END) AS paid
               FROM groups g LEFT JOIN group_members gm ON gm.group_id=g.id WHERE g.code=?""",
            (a_code,),
        )
        db_group = self.db_one("SELECT id,status FROM groups WHERE code=?", (a_code,))
        winner_price = None
        for package in winner_data.get("packages") or []:
            for item in package.get("items") or []:
                winner_price = int(item.get("unit_price_cents") or 0)
                break
            if winner_price is not None:
                break
        expected_group_unit = int(group_offer["group_price_cents"]) + int(group_offer["price_delta_cents"])
        progress_ok = bool(
            a_created.status == 200
            and initial.get("joined_count") == 0
            and initial.get("members") == []
            and pay_a.status == 200
            and after_a.get("joined_count") == 1
            and b_add.status == 200
            and c_add.status == 200
            and len(success_indexes) == 1
            and pending_public.get("joined_count") == 1
            and winner_pay.status == 200
            and final_public.get("status") == "FULL"
            and final_public.get("joined_count") == 2
            and len(final_public.get("members") or []) == 2
            and (final_public.get("actions") or {}).get("can_join") is False
            and a_final_status == "PAID"
            and db_group is not None
            and str(db_group["status"]) == "FULL"
            and db_counts is not None
            and int(db_counts["members"] or 0) == 2
            and int(db_counts["paid"] or 0) == 2
            and winner_price == expected_group_unit
        )
        self.record(
            "group_capacity_race_and_paid_progress",
            progress_ok,
            f"initial_joined={initial.get('joined_count')} after_a_joined={after_a.get('joined_count')} "
            f"b_c_statuses={[summary[0] for summary in summaries]} b_c_errors={[summary[1] for summary in summaries]} "
            f"pending_joined={pending_public.get('joined_count')} winner={winner_tag or None} "
            f"winner_pay={winner_pay.status}/{winner_pay.error_code()} final_status={final_public.get('status')} "
            f"final_joined={final_public.get('joined_count')} members={int(db_counts['members']) if db_counts else None} "
            f"paid={int(db_counts['paid']) if db_counts and db_counts['paid'] is not None else None} "
            f"price_ok={winner_price == expected_group_unit}",
        )
        binding_ok = bool(
            self_join.status == 409
            and self_join.error_code() == "GROUP_UNAVAILABLE"
            and cross_sku.status == 409
            and (cross_group is not None and cross_group.status == 422)
            and (cross_group.error_code() == "MIXED_BUY_MODE" if cross_group is not None else False)
            and demo_data_ok
        )
        self.record(
            "group_offer_sku_and_single_cart_binding",
            binding_ok,
            f"self_join={self_join.status}/{self_join.error_code()} "
            f"cross_sku={cross_sku.status}/{cross_sku.error_code()} "
            f"cross_group={cross_group.status if cross_group else None}/"
            f"{cross_group.error_code() if cross_group else 'fixture_missing'} demo_group_fixture={demo_data_ok}",
        )

    def probe_static_and_headers(self) -> None:
        paths = [
            "/web/%2e%2e/%2e%2e/docs/api-contract.md",
            "/web/../docs/api-contract.md",
            "/web/assets/../../../../docs/api-contract.md",
            "/docs/api-contract.md",
            "/data/catalog.json",
        ]
        blocked = True
        details = []
        for path in paths:
            try:
                resp = raw_request("GET", path)
                leaked = "API" in resp.text and "NO_ACCESS" in resp.text
                blocked = blocked and resp.status == 404 and not leaked
                details.append(f"{path}={resp.status}")
            except Exception as exc:
                blocked = False
                details.append(f"{path}={type(exc).__name__}")
        favicon = raw_request("GET", "/web/assets/brand/favicon.svg")
        details.append(f"asset={favicon.status}")
        self.record(
            "static_traversal_blocked",
            blocked and favicon.status == 200,
            " ".join(details),
        )

        home = raw_request("GET", "/")
        csp = home.header("Content-Security-Policy") or ""
        script_src = ""
        for part in csp.split(";"):
            if part.strip().startswith("script-src"):
                script_src = part.strip()
        header_ok = bool(
            home.status == 200
            and "default-src 'self'" in csp
            and script_src == "script-src 'self'"
            and "object-src 'none'" in csp
            and "frame-ancestors 'none'" in csp
            and home.header("X-Frame-Options") == "DENY"
            and home.header("X-Content-Type-Options") == "nosniff"
        )
        script_summary = "script-src-self" if script_src == "script-src 'self'" else (script_src or "missing")
        self.record(
            "security_headers_csp_baseline",
            header_ok,
            f"http={home.status} csp={script_summary} "
            f"xfo={home.header('X-Frame-Options')} nosniff={home.header('X-Content-Type-Options')} "
            f"hsts={home.header('Strict-Transport-Security')}",
        )

        origin = "https://evil.example"
        get_resp = raw_request("GET", "/api/session", {"Origin": origin})
        opt_resp = raw_request(
            "OPTIONS",
            "/api/session",
            {
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-openshop-csrf",
            },
        )
        acao = (get_resp.header("Access-Control-Allow-Origin"), opt_resp.header("Access-Control-Allow-Origin"))
        cors_ok = bool(acao == (None, None))
        self.record(
            "cors_no_cross_origin_grant",
            cors_ok,
            f"GET_acao={acao[0]} OPTIONS_status={opt_resp.status} OPTIONS_acao={acao[1]}",
        )

        xss_payload = "<script>alert(1)</script>"
        reflected = raw_request(
            "GET", "/search?q=" + urllib.parse.quote(xss_payload, safe="")
        )
        api_reflected = raw_request(
            "GET", "/api/products?q=" + urllib.parse.quote(xss_payload, safe="")
        )
        ct = (api_reflected.header("Content-Type") or "").lower()
        xss_ok = bool(
            reflected.status in {200, 404}
            and xss_payload not in reflected.text
            and api_reflected.status == 200
            and "application/json" in ct
            and "text/html" not in ct
        )
        self.record(
            "reflected_xss_api_json_only",
            xss_ok,
            f"search_html={reflected.status} payload_in_html={xss_payload in reflected.text} "
            f"api={api_reflected.status} api_content_type={ct.split(';')[0]}",
        )

    def probe_sql_and_like(self) -> None:
        client = Client()
        client.session()
        base = client.request("GET", _url("/api/products", page_size=24))
        base_total = int(((base.data or {}) if isinstance(base.data, dict) else {}).get("total") or 0)
        token = "".join(ch for ch in str(self.products[0]["title"]) if not ch.isspace())
        known = client.request("GET", _url("/api/search/suggest", q=token[:2]))
        known_products = (known.data or {}).get("products") if isinstance(known.data, dict) else []
        like_ok = bool(known.status == 200 and known_products)
        self.record(
            "like_positive_reference",
            like_ok,
            f"suggest_http={known.status} matched_products={len(known_products or [])}",
        )
        false_positive = []
        for term in ("%", "_", "' OR 1=1 --"):
            resp = client.request("GET", _url("/api/products", q=term, page_size=24))
            total = int(((resp.data or {}) if isinstance(resp.data, dict) else {}).get("total") or 0)
            false_positive.append(f"{term!r}:{resp.status}/{total}")
            if resp.status != 200 or total >= base_total:
                like_ok = False
        self.record(
            "like_special_characters_escaped",
            like_ok,
            f"base_total={base_total} " + " ".join(false_positive),
        )

        login_inj = client.request(
            "POST",
            "/api/auth/login",
            {"identifier": "' OR 1=1 --", "password": "not-a-real-password"},
        )
        details = []
        ok = login_inj.status == 401 and login_inj.error_code() == "INVALID_CREDENTIALS"
        details.append(f"login={login_inj.status}/{login_inj.error_code()}")
        group_inj = client.request(
            "GET", "/api/groups/" + urllib.parse.quote("' OR 1=1 --", safe="")
        )
        ok = ok and group_inj.status == 404
        details.append(f"group={group_inj.status}/{group_inj.error_code()}")
        order_inj = self.client("a").request(
            "GET", "/api/orders/" + urllib.parse.quote("' OR 1=1 --", safe="")
        )
        ok = ok and order_inj.status == 404
        details.append(f"order={order_inj.status}/{order_inj.error_code()}")
        coupon_inj = self.client("a").request(
            "POST", "/api/coupons/" + urllib.parse.quote("' OR 1=1 --", safe="") + "/claim", {}
        )
        ok = ok and coupon_inj.status in {400, 404}
        details.append(f"coupon={coupon_inj.status}/{coupon_inj.error_code()}")
        leaked_text = (login_inj.text + group_inj.text + order_inj.text + coupon_inj.text).lower()
        ok = ok and all(marker not in leaked_text for marker in ("traceback", "sqlite", "syntax error"))
        self.record("sql_injection_surfaces_rejected", ok, " ".join(details))

    def probe_login_rate_controls(self) -> None:
        c = self.accounts["c"]
        client = Client()
        client.session()
        failures = []
        for _ in range(8):
            resp = client.request(
                "POST",
                "/api/auth/login",
                {"identifier": c["email"], "password": "wrong-password-xyz"},
            )
            failures.append(resp.status)
        ninth = client.request(
            "POST",
            "/api/auth/login",
            {"identifier": c["email"], "password": c["password"]},
        )
        self.record(
            "rate_login_per_identifier_failure_control",
            failures == [401] * 8 and ninth.status == 429 and ninth.error_code() == "RATE_LIMITED",
            f"first8={failures} ninth_correct={ninth.status}/{ninth.error_code()}",
        )

        spray = Client()
        spray.session()
        spray_statuses = []
        spray_codes = []
        for index in range(10):
            target = f"nonexistent-{self.stamp.lower()}-{index}@openshop.test"
            resp = spray.request(
                "POST",
                "/api/auth/login",
                {"identifier": target, "password": "not-a-real-password"},
            )
            spray_statuses.append(resp.status)
            spray_codes.append(resp.error_code())
        rate_limited = sum(1 for status in spray_statuses if status == 429)
        self.expect_probe(
            "auth_login_identifier_rotation_spray",
            rate_limited > 0,
            f"attempts=10 distinct_identifiers=10 statuses={spray_statuses} codes={sorted(set(spray_codes))} rate_limited={rate_limited}",
            finding="F-03",
            severity="Low",
            owner="Lead (core.py/auth.py rate limiter)",
            coords="apps/storefront/routes/core.py:74; packages/commerce/auth.py:155-168",
        )

    def probe_register_success_reset(self) -> None:
        statuses = []
        for index in range(3):
            client = Client()
            client.session()
            email = f"pr-extra-{self.stamp.lower()}-{index}-{secrets.token_hex(3)}@openshop.test"
            password = "A9!" + secrets.token_urlsafe(18)
            resp = client.register(f"附加{index}", email, password)
            statuses.append(resp.status)
            self.remember(password)
            self.remember(client.cookie_token())
            self.remember(client.csrf)
        successes = self.essential_registration_successes + sum(1 for value in statuses if value == 200)
        self.expect_probe(
            "rate_register_successful_signup_bypass",
            successes <= 5,
            f"successful_registrations_same_ip_window={successes} extra_statuses={statuses} "
            "successful signup calls reset bucket at core.py:60 before the next call",
            finding="F-02",
            severity="Low",
            owner="Lead (core.py registration limiter)",
            coords="apps/storefront/routes/core.py:37,60; packages/commerce/auth.py:155-168",
        )

    def probe_register_failure_control(self) -> None:
        client = Client()
        client.session()
        failures = []
        for index in range(5):
            resp = client.request(
                "POST",
                "/api/auth/register",
                {"display_name": "无效", "email": "not-an-email", "password": "12345678"},
            )
            failures.append(resp.status)
        sixth_client = Client()
        sixth_client.session()
        sixth = sixth_client.request(
            "POST",
            "/api/auth/register",
            {
                "display_name": "限速终检",
                "email": f"pr-final-{self.stamp.lower()}@openshop.test",
                "password": "A9!" + secrets.token_urlsafe(18),
            },
        )
        self.record(
            "rate_register_five_failure_control",
            failures == [400] * 5 and sixth.status == 429 and sixth.error_code() == "RATE_LIMITED",
            f"first5={failures} sixth={sixth.status}/{sixth.error_code()}",
        )

    def probe_logs_and_finish(self) -> None:
        try:
            log_text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        secret_names = []
        leaked = []
        for index, value in enumerate(sorted(self.secret_values)):
            if value and value in log_text:
                leaked.append(index)
        self.record(
            "server_log_no_credentials_or_tokens",
            not leaked,
            f"server_log={self.log_path.name} secret_values_checked={len(self.secret_values)} leaked={len(leaked)}",
        )

        failures = [row for row in self.results if row["verdict"] == "FAIL"]
        controls = [row for row in self.results if row["kind"] == "control"]
        findings = sorted({str(row["finding"]) for row in failures if row["finding"]})
        result_path = self.run_dir / "probes.jsonl"
        try:
            with open(result_path, "w", encoding="utf-8") as handle:
                for row in self.results:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
        print(
            f"AUDIT_FINDINGS verdict={'FAIL' if findings else 'PASS'} ids={','.join(findings) or None} "
            f"fail_probes={len(failures)}",
            flush=True,
        )
        print(
            f"PEER_REVIEW verdict={'FAIL' if failures else 'PASS'} probes={len(self.results)} "
            f"controls={len(controls)} failures={len(failures)} findings={len(findings)} "
            f"run_dir={self.run_dir} server_log={self.log_path.name} results={result_path.name}",
            flush=True,
        )

    def run(self) -> None:
        self.probe_session_discriminator()
        self.probe_cookie_attributes()
        self.probe_csrf()
        self.probe_session_fixation()
        self.probe_logout()
        self.probe_register_role_injection()
        self.probe_login_errors()
        self.probe_password_and_token_storage()
        self.probe_seller_scope()
        self.probe_idor()
        self.probe_coupon_owner()
        self.probe_forged_price_and_quantity()
        self.probe_idempotency()
        self.probe_coupon_controls()
        self.probe_order_state()
        self.probe_groups()
        self.probe_stock_concurrency()
        self.probe_static_and_headers()
        self.probe_sql_and_like()
        self.probe_query_field_flood()
        self.probe_nul_byte_path()
        self.probe_register_success_reset()
        self.probe_login_rate_controls()
        self.probe_register_failure_control()
        self.probe_logs_and_finish()


def main() -> int:
    run_dir = EVIDENCE_ROOT / ("run-" + _now_stamp())
    server = RunningServer(run_dir)
    exit_code = 2
    try:
        server.start()
        print(
            f"AUDIT_SETUP verdict=PASS url={BASE_URL} data_dir={server.data_dir} log={server.log_path}",
            flush=True,
        )
        audit = Audit(server)
        audit.bootstrap()
        audit.run()
        failures = [row for row in audit.results if row["verdict"] == "FAIL"]
        exit_code = 1 if failures else 0
    except Exception as exc:
        print(f"AUDIT_EXCEPTION verdict=FAIL type={type(exc).__name__} detail={exc}", flush=True)
        traceback.print_exc(limit=8)
        exit_code = 2
    finally:
        server.stop()
        print(f"AUDIT_TEARDOWN verdict=DONE run_dir={run_dir}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
