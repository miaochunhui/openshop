#!/usr/bin/env python3
"""Real-HTTP trade acceptance suite.

Runs the actual stdlib server on the dedicated port 8862 with a fresh data
directory under /hf3fs-jd/data/tmp/openshop/. No in-process WSGI shortcut is
used. Fixture-only DB edits are limited to addresses, stock and package/order
shipping state where the contract has no buyer-controlled endpoint.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.commerce import db as dbutil  # noqa: E402

PORT = 8862
BASE_URL = f"http://127.0.0.1:{PORT}"
TMP_ROOT = Path("/hf3fs-jd/data/tmp/openshop/backend-trade")
_UNSET = object()


class HttpClient:
    """Cookie-jar JSON client. It never prints cookies or CSRF values."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.csrf = ""

    def request(self, method: str, path: str, body=_UNSET, *, use_csrf: bool = True):
        headers = {"Accept": "application/json", "Connection": "close"}
        data = None
        if body is not _UNSET and body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if method.upper() not in {"GET", "HEAD"} and use_csrf and self.csrf:
            headers["X-OpenShop-CSRF"] = self.csrf
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            response = self.opener.open(req, timeout=10)
            status = response.status
            raw = response.read()
            response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            response_headers = dict(exc.headers.items()) if exc.headers else {}
        text = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            payload = None
        return {"status": status, "payload": payload, "text": text, "headers": response_headers}

    def get_session(self):
        response = self.request("GET", "/api/session")
        if response["status"] == 200 and response["payload"]:
            self.csrf = (response["payload"].get("data") or {}).get("csrf", "")
        return response


def _res_data(response) -> dict:
    payload = response.get("payload") or {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _res_error(response) -> tuple[str, str]:
    payload = response.get("payload") or {}
    error = payload.get("error") or {}
    return str(error.get("code", "")), str(error.get("message", ""))


class TradeApiAcceptance(unittest.TestCase):
    _any_failure = False

    @classmethod
    def setUpClass(cls) -> None:
        cls._any_failure = False
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        cls.stamp = stamp
        cls.run_dir = TMP_ROOT / f"run-{stamp}"
        cls.data_dir = cls.run_dir / "data"
        cls.log_path = cls.run_dir / "server.log"
        cls.run_dir.mkdir(parents=True, exist_ok=False)
        cls.data_dir.mkdir(parents=True, exist_ok=False)
        cls.proc = None
        cls._log_handle = open(cls.log_path, "wb")
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
            str(cls.data_dir),
            "--log-level",
            "INFO",
        ]
        try:
            cls.proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                stdout=cls._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + 30
            ready = False
            while time.monotonic() < deadline:
                if cls.proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(BASE_URL + "/api/health", timeout=1.5) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    if health.get("ok") is True:
                        ready = True
                        break
                except Exception:
                    time.sleep(0.2)
            if not ready:
                detail = cls.log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                print(f"TRADE_SETUP verdict=FAIL reason=server_not_ready log={cls.log_path}", flush=True)
                print(detail, flush=True)
                raise RuntimeError("server did not become ready")
            cls.db_path = cls.data_dir / "openshop.sqlite3"
            print(
                f"TRADE_SETUP verdict=PASS url={BASE_URL} data_dir={cls.data_dir} log={cls.log_path}",
                flush=True,
            )
            cls.standard_offer, cls.group_offer, cls.shop_offers = cls._choose_offers()
            cls.demo, cls.demo_user = cls._login_demo()
            cls.alpha, cls.alpha_user = cls._register("alpha", "验收甲")
            cls.beta, cls.beta_user = cls._register("beta", "验收乙")
            cls.addresses = {
                "demo": cls._address_for_user(int(cls.demo_user["id"])),
                "alpha": cls._insert_address(int(cls.alpha_user["id"]), "验收甲", 1),
                "beta": cls._insert_address(int(cls.beta_user["id"]), "验收乙", 2),
            }
            print(
                "TRADE_FIXTURE verdict=PASS "
                f"demo_role={cls.demo_user.get('role')} standard_offer={cls.standard_offer['offer_id']} "
                f"group_offer={cls.group_offer['offer_id']} shops={len(cls.shop_offers)}",
                flush=True,
            )
        except Exception:
            cls._stop_server()
            cls._log_handle.close()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stop_server()
        try:
            cls._log_handle.close()
        except Exception:
            pass
        verdict = "FAIL" if cls._any_failure else "PASS"
        print(
            f"TRADE_ACCEPTANCE verdict={verdict} port={PORT} data_dir={cls.data_dir} log={cls.log_path}",
            flush=True,
        )

    def run(self, result=None):
        outcome = super().run(result)
        if outcome is not None and not outcome.wasSuccessful():
            type(self)._any_failure = True
        return outcome

    @classmethod
    def _stop_server(cls) -> None:
        proc = getattr(cls, "proc", None)
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=4)

    @classmethod
    def _login_demo(cls):
        client = HttpClient(BASE_URL)
        session = client.get_session()
        if session["status"] != 200:
            raise RuntimeError(f"guest session failed: {session['status']}")
        response = client.request("POST", "/api/auth/demo", {"role": "buyer"})
        if response["status"] != 200:
            raise RuntimeError(f"demo login failed: {response}")
        data = _res_data(response)
        client.csrf = data.get("csrf", "")
        user = data.get("user") or {}
        if user.get("handle") != "demo_buyer":
            raise RuntimeError(f"unexpected demo buyer: {user.get('handle')}")
        print(f"TRADE_CHECK demo_buyer_login verdict=PASS handle={user.get('handle')}", flush=True)
        return client, user

    @classmethod
    def _register(cls, tag: str, display_name: str):
        client = HttpClient(BASE_URL)
        if client.get_session()["status"] != 200:
            raise RuntimeError("guest session failed before register")
        email = f"trade-{cls.stamp.lower()}-{tag}@openshop.test"
        password = secrets.token_urlsafe(18)
        response = client.request(
            "POST",
            "/api/auth/register",
            {"display_name": display_name, "email": email, "password": password},
        )
        if response["status"] != 200:
            raise RuntimeError(f"register {tag} failed status={response['status']} body={response['text'][:400]}")
        data = _res_data(response)
        client.csrf = data.get("csrf", "")
        user = data.get("user") or {}
        if not user.get("id"):
            raise RuntimeError("register response missing user")
        return client, user

    @classmethod
    def _address_for_user(cls, user_id: int) -> int:
        with dbutil.connect(cls.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM addresses WHERE user_id=? ORDER BY is_default DESC,id LIMIT 1", (int(user_id),)
            ).fetchone()
        if row is None:
            raise RuntimeError(f"no address for user {user_id}")
        return int(row["id"])

    @classmethod
    def _insert_address(cls, user_id: int, receiver: str, suffix: int) -> int:
        now = dbutil.iso()
        with dbutil.connect(cls.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO addresses(user_id,label,receiver,phone,province,city,district,detail,is_default,
                     created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(user_id),
                    "家",
                    receiver,
                    f"13800001{suffix:03d}",
                    "上海市",
                    "上海市",
                    "徐汇区",
                    "验收路 88 号 6 栋 502",
                    1,
                    now,
                    now,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    @classmethod
    def _choose_offers(cls):
        query = """
            SELECT o.id AS offer_id, o.shop_id, o.stock, o.price_cents, o.group_size,
                   o.group_price_cents, o.kind AS offer_kind, o.name AS offer_name,
                   p.id AS product_id, p.slug, p.title,
                   s.name AS shop_name,
                   k.id AS sku_id, k.name AS sku_name, k.price_delta_cents
            FROM offers o
            JOIN products p ON p.id=o.product_id
            JOIN shops s ON s.id=o.shop_id
            JOIN skus k ON k.product_id=o.product_id
            WHERE o.status='active' AND p.status='active' AND o.stock>0
            ORDER BY o.id, k.sort_order, k.id
        """
        with dbutil.connect(cls.db_path) as conn:
            rows = [dict(row) for row in conn.execute(query).fetchall()]
        unique: list[dict] = []
        seen: set[int] = set()
        for row in rows:
            if int(row["offer_id"]) in seen:
                continue
            seen.add(int(row["offer_id"]))
            unique.append(row)
        if not unique:
            raise RuntimeError("no sellable offer fixture")
        group_candidates = [row for row in unique if row["group_size"] is not None]
        group_offer = next((row for row in group_candidates if int(row["group_size"]) == 2), None)
        if group_offer is None and group_candidates:
            group_offer = group_candidates[0]
        if group_offer is None:
            raise RuntimeError("no group offer fixture")
        standard_offer = next((row for row in unique if int(row["stock"]) >= 5), unique[0])
        by_shop: dict[int, dict] = {}
        for row in unique:
            by_shop.setdefault(int(row["shop_id"]), row)
        if len(by_shop) < 2:
            raise RuntimeError("need at least two shops for package-split fixture")
        return standard_offer, group_offer, list(by_shop.values())[:2]

    @classmethod
    def _open_db(cls):
        return dbutil.connect(cls.db_path)

    def _new_key(self, label: str) -> str:
        return f"trade-{self.stamp.lower()}-{label}-{secrets.token_hex(4)}"

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        verdict = "PASS" if ok else "FAIL"
        print(f"TRADE_CHECK {name} verdict={verdict} {detail}".rstrip(), flush=True)
        if not ok:
            type(self)._any_failure = True
            self.fail(f"{name}: {detail}")

    def expect_status(self, name: str, response, expected: int):
        code, _message = _res_error(response) if response["status"] >= 400 else ("", "")
        detail = f"http={response['status']}"
        if code:
            detail += f" code={code}"
        self.check(f"{name}.http", response["status"] == expected, detail)
        return response

    def expect_error(self, name: str, response, expected_status: int, expected_code: str):
        self.expect_status(f"{name}", response, expected_status)
        code, message = _res_error(response)
        self.check(
            f"{name}.code",
            code == expected_code,
            f"expected={expected_code} actual={code} message={message[:80]}",
        )

    def _cart(self, client) -> dict:
        response = client.request("GET", "/api/cart")
        if response["status"] != 200:
            raise AssertionError(f"GET cart failed status={response['status']} body={response['text'][:300]}")
        return _res_data(response)

    def _clear_cart(self, client) -> None:
        cart = self._cart(client)
        for item in cart.get("items", []):
            response = client.request("DELETE", f"/api/cart/items/{int(item['id'])}")
            if response["status"] != 200:
                raise AssertionError(f"cleanup cart delete failed status={response['status']}")

    def _add_standard(self, client, quantity: int = 1):
        offer = self.standard_offer
        return client.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(offer["offer_id"]),
                "sku_id": int(offer["sku_id"]),
                "quantity": quantity,
                "buy_mode": "standard",
            },
        )

    def _order_body(self, address_id: int, key: str, *, coupon_code=None, shipping="standard", note="") -> dict:
        return {
            "address_id": int(address_id),
            "coupon_code": coupon_code,
            "shipping_method": shipping,
            "note": note,
            "payment_method": "demo",
            "idempotency_key": key,
        }

    def _available_coupons(self, client) -> dict:
        response = client.request("GET", "/api/coupons")
        if response["status"] != 200:
            raise AssertionError(f"GET coupons failed status={response['status']}")
        return _res_data(response)

    def _stock(self, offer_id: int) -> int:
        with self._open_db() as conn:
            row = conn.execute("SELECT stock FROM offers WHERE id=?", (int(offer_id),)).fetchone()
            return int(row["stock"])

    def _set_stock(self, offer_id: int, stock: int) -> None:
        with self._open_db() as conn:
            conn.execute("UPDATE offers SET stock=?,updated_at=? WHERE id=?", (int(stock), dbutil.iso(), int(offer_id)))
            conn.commit()

    # -------------------------------------------------------------- test 01

    def test_01_guest_cart_csrf_and_401(self):
        offer = self.standard_offer
        client = HttpClient(BASE_URL)
        session = client.get_session()
        self.expect_status("guest.session", session, 200)
        session_data = _res_data(session)
        self.check(
            "guest.session_shape",
            bool(session_data.get("csrf")) and session_data.get("is_guest") is True,
            f"is_guest={session_data.get('is_guest')} has_csrf={bool(session_data.get('csrf'))}",
        )

        no_auth = HttpClient(BASE_URL)
        no_auth.get_session()
        denied = no_auth.request("GET", "/api/orders")
        self.expect_error("auth.guest_orders", denied, 401, "AUTH_REQUIRED")
        denied_quote = no_auth.request("POST", "/api/checkout/quote", {"shipping_method": "standard"})
        self.expect_error("auth.guest_quote", denied_quote, 401, "AUTH_REQUIRED")

        added = client.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(offer["offer_id"]),
                "sku_id": int(offer["sku_id"]),
                "quantity": 1,
                "buy_mode": "standard",
            },
        )
        self.expect_status("guest.cart_add", added, 200)
        added_data = _res_data(added)
        items = added_data.get("items") or []
        self.check(
            "guest.cart_item_shape",
            len(items) == 1
            and items[0].get("offer_id") == int(offer["offer_id"])
            and items[0].get("buy_mode") == "standard"
            and items[0].get("selected") is True,
            f"items={len(items)} offer={items[0].get('offer_id') if items else None}",
        )
        if not items:
            return
        item_id = int(items[0]["id"])
        patched = client.request("PATCH", f"/api/cart/items/{item_id}", {"quantity": 2})
        self.expect_status("guest.cart_patch", patched, 200)
        patched_data = _res_data(patched)
        self.check(
            "guest.cart_quantity_totals",
            (patched_data.get("summary") or {}).get("item_count") == 2
            and (patched_data.get("summary") or {}).get("selected_count") == 2,
            f"item_count={(patched_data.get('summary') or {}).get('item_count')}",
        )
        unselected = client.request("POST", "/api/cart/selection", {"selected": False})
        self.expect_status("guest.selection_off", unselected, 200)
        unselected_data = _res_data(unselected)
        self.check(
            "guest.selection_summary",
            (unselected_data.get("summary") or {}).get("selected_count") == 0
            and (unselected_data.get("summary") or {}).get("total_cents") == 0,
            f"selected={(unselected_data.get('summary') or {}).get('selected_count')}",
        )
        selected = client.request("POST", "/api/cart/selection", {"selected": True})
        self.expect_status("guest.selection_on", selected, 200)
        deleted = client.request("DELETE", f"/api/cart/items/{item_id}")
        self.expect_status("guest.cart_delete", deleted, 200)
        deleted_data = _res_data(deleted)
        self.check("guest.cart_empty", deleted_data.get("items") == [], f"items={len(deleted_data.get('items') or [])}")

        no_csrf = client.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(offer["offer_id"]),
                "sku_id": int(offer["sku_id"]),
                "quantity": 1,
                "buy_mode": "standard",
            },
            use_csrf=False,
        )
        self.expect_error("csrf.cart_add_missing_header", no_csrf, 403, "CSRF_FAILED")

    # -------------------------------------------------------------- test 02

    def test_02_negative_cart_stock_coupon_and_mixed_mode(self):
        offer = self.standard_offer
        offer_id = int(offer["offer_id"])
        self._clear_cart(self.alpha)
        stock = self._stock(offer_id)
        if stock < 2:
            with self._open_db() as conn:
                conn.execute("UPDATE offers SET stock=20 WHERE id=?", (offer_id,))
                conn.commit()
            stock = 20

        negative = self.alpha.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": offer_id,
                "sku_id": int(offer["sku_id"]),
                "quantity": -1,
                "buy_mode": "standard",
            },
        )
        self.expect_error("validation.negative_quantity", negative, 400, "VALIDATION_ERROR")

        oversell_add = self.alpha.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": offer_id,
                "sku_id": int(offer["sku_id"]),
                "quantity": min(stock + 1, 9999),
                "buy_mode": "standard",
            },
        )
        self.expect_error("stock.add_over_stock", oversell_add, 409, "STOCK_CHANGED")

        added = self._add_standard(self.alpha)
        self.expect_status("stock.cart_add_ok", added, 200)
        stock_snapshot = self._stock(offer_id)
        try:
            self._set_stock(offer_id, 0)
            quote_zero = self.alpha.request("POST", "/api/checkout/quote", {"shipping_method": "standard"})
            self.expect_error("stock.quote_zero", quote_zero, 409, "STOCK_CHANGED")
            create_zero_key = self._new_key("stock-zero")
            create_zero = self.alpha.request(
                "POST",
                "/api/orders",
                self._order_body(self.addresses["alpha"], create_zero_key),
            )
            self.expect_error("stock.create_zero", create_zero, 409, "STOCK_CHANGED")
            with self._open_db() as conn:
                no_order = conn.execute(
                    "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
                    (int(self.alpha_user["id"]), create_zero_key),
                ).fetchone()
            self.check("stock.no_partial_order", int(no_order["n"]) == 0, f"orders={int(no_order['n'])}")
        finally:
            self._set_stock(offer_id, stock_snapshot)

        unknown_code = "ZZZ999"
        bad_quote = self.alpha.request(
            "POST", "/api/checkout/quote", {"coupon_code": unknown_code, "shipping_method": "standard"}
        )
        self.expect_status("coupon.quote_bad_http", bad_quote, 200)
        bad_quote_data = _res_data(bad_quote)
        self.check(
            "coupon.quote_bad_error",
            bad_quote_data.get("coupon") is None and bool(bad_quote_data.get("coupon_error")),
            f"coupon={bad_quote_data.get('coupon')} has_error={bool(bad_quote_data.get('coupon_error'))}",
        )
        bad_create_key = self._new_key("bad-coupon")
        bad_create = self.alpha.request(
            "POST",
            "/api/orders",
            self._order_body(self.addresses["alpha"], bad_create_key, coupon_code=unknown_code),
        )
        self.expect_error("coupon.create_bad", bad_create, 422, "COUPON_INVALID")
        with self._open_db() as conn:
            no_bad_order = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
                (int(self.alpha_user["id"]), bad_create_key),
            ).fetchone()
        self.check(
            "coupon.bad_create_no_order",
            int(no_bad_order["n"]) == 0,
            f"orders={int(no_bad_order['n'])}",
        )

        mixed = self.alpha.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(self.group_offer["offer_id"]),
                "sku_id": int(self.group_offer["sku_id"]),
                "quantity": 1,
                "buy_mode": "group",
            },
        )
        self.expect_error("mode.mixed_add", mixed, 422, "MIXED_BUY_MODE")
        self._clear_cart(self.alpha)

    # -------------------------------------------------------------- test 03

    def test_03_coupon_claim_and_quote_authority(self):
        self._clear_cart(self.beta)
        offer = self.standard_offer
        added = self._add_standard(self.beta, quantity=1)
        self.expect_status("coupon.cart_add", added, 200)
        cart = _res_data(added)
        subtotal = int((cart.get("summary") or {}).get("subtotal_cents") or 0)
        self.check("coupon.cart_subtotal", subtotal > 0, f"subtotal_cents={subtotal}")

        inventory = self._available_coupons(self.beta)
        available = inventory.get("available") or []
        mine_before = inventory.get("mine") or []
        self.check(
            "coupon.lists_shape",
            isinstance(available, list) and isinstance(mine_before, list) and bool(available),
            f"available={len(available)} mine={len(mine_before)}",
        )
        unclaimed = [entry for entry in available if not entry.get("claimed")]
        self.check(
            "coupon.unclaimed_expiry_null",
            any(entry.get("expires_at") is None for entry in unclaimed),
            f"unclaimed={len(unclaimed)}",
        )
        claimable = [
            entry
            for entry in available
            if entry.get("claimable") and int(entry.get("threshold_cents", 0)) <= subtotal
        ]
        self.check("coupon.claimable_fixture", bool(claimable), f"claimable_under_subtotal={len(claimable)}")
        if not claimable:
            return
        candidate = claimable[0]
        code = candidate["code"]
        claimed = self.beta.request("POST", f"/api/coupons/{code}/claim", {})
        self.expect_status("coupon.claim", claimed, 200)
        claimed_data = _res_data(claimed)
        coupon = claimed_data.get("coupon") or {}
        self.check(
            "coupon.claim_shape",
            coupon.get("code") == code
            and coupon.get("status") == "AVAILABLE"
            and bool(coupon.get("user_coupon_id"))
            and bool(coupon.get("expires_at")),
            f"code={coupon.get('code')} status={coupon.get('status')} has_expiry={bool(coupon.get('expires_at'))}",
        )
        duplicate = self.beta.request("POST", f"/api/coupons/{code}/claim", {})
        self.expect_status("coupon.claim_duplicate", duplicate, 409)
        code_duplicate, _ = _res_error(duplicate)
        self.check("coupon.claim_duplicate_code", code_duplicate == "COUPON_ALREADY_CLAIMED", f"code={code_duplicate}")

        quote = self.beta.request(
            "POST",
            "/api/checkout/quote",
            {"coupon_code": code, "shipping_method": "standard"},
        )
        self.expect_status("quote.coupon_http", quote, 200)
        quote_data = _res_data(quote)
        quote_coupon = quote_data.get("coupon") or {}
        self.check(
            "quote.coupon_authority",
            quote_coupon.get("code") == code
            and int(quote_coupon.get("discount_cents", 0)) > 0
            and int(quote_data.get("subtotal_cents", 0)) == subtotal
            and int(quote_data.get("total_cents", -1))
            == int(quote_data.get("subtotal_cents", 0))
            - int(quote_data.get("discount_cents", 0))
            + int(quote_data.get("shipping_cents", 0)),
            (
                f"subtotal={quote_data.get('subtotal_cents')} discount={quote_data.get('discount_cents')} "
                f"shipping={quote_data.get('shipping_cents')} total={quote_data.get('total_cents')}"
            ),
        )

        express = self.beta.request(
            "POST",
            "/api/checkout/quote",
            {"coupon_code": code, "shipping_method": "express"},
        )
        self.expect_status("quote.express_http", express, 200)
        express_data = _res_data(express)
        self.check(
            "quote.express_fee",
            (express_data.get("shipping_method") or {}).get("code") == "express"
            and int(express_data.get("shipping_cents", 0)) == 1200
            and int(express_data.get("total_cents", -1))
            == int(express_data.get("subtotal_cents", 0))
            - int(express_data.get("discount_cents", 0))
            + 1200,
            (
                f"shipping_method={(express_data.get('shipping_method') or {}).get('code')} "
                f"shipping={express_data.get('shipping_cents')} total={express_data.get('total_cents')}"
            ),
        )

        unknown = self.beta.request(
            "POST",
            "/api/checkout/quote",
            {"coupon_code": "ZZZ999", "shipping_method": "standard"},
        )
        unknown_data = _res_data(unknown)
        self.check(
            "quote.coupon_foreign_error",
            unknown["status"] == 200
            and unknown_data.get("coupon") is None
            and bool(unknown_data.get("coupon_error")),
            f"http={unknown['status']} has_error={bool(unknown_data.get('coupon_error'))}",
        )
        inventory_after = self._available_coupons(self.beta)
        mine_after = {entry["code"]: entry for entry in inventory_after.get("mine") or []}
        self.check(
            "coupon.claim_visible_in_mine",
            code in mine_after and bool(mine_after[code].get("expires_at")),
            f"code={code} present={code in mine_after}",
        )
        self._clear_cart(self.beta)


    # -------------------------------------------------------------- helpers

    def _pick_coupon_for_order(self, client, subtotal: int, label: str):
        inventory = self._available_coupons(client)
        mine = inventory.get("mine") or []

        def usable(entries):
            return sorted(
                [
                    entry
                    for entry in entries
                    if entry.get("status") == "AVAILABLE"
                    and int(entry.get("threshold_cents", 0)) <= int(subtotal)
                ],
                key=lambda entry: -int(entry.get("discount_cents", 0)),
            )

        existing = usable(mine)
        code = existing[0]["code"] if existing else None
        if code is None:
            for entry in inventory.get("available") or []:
                if (
                    entry.get("claimable")
                    and int(entry.get("threshold_cents", 0)) <= int(subtotal)
                ):
                    claimed = client.request("POST", f"/api/coupons/{entry['code']}/claim", {})
                    if claimed["status"] == 200:
                        code = entry["code"]
                        break
            if code is not None:
                inventory = self._available_coupons(client)
        self.check(
            f"{label}.coupon_fixture",
            code is not None,
            f"subtotal={subtotal} available_mine={len(mine)}",
        )
        return code, inventory

    # -------------------------------------------------------------- test 04

    def test_04_demo_buyer_full_flow_review_and_refund(self):
        self._clear_cart(self.demo)
        offer = self.standard_offer
        offer_id = int(offer["offer_id"])
        added = self._add_standard(self.demo)
        self.expect_status("flow.cart_add", added, 200)
        cart_data = _res_data(added)
        items = cart_data.get("items") or []
        self.check("flow.cart_single", len(items) == 1, f"items={len(items)}")
        if not items:
            return
        cart_item_id = int(items[0]["id"])
        subtotal = int((cart_data.get("summary") or {}).get("subtotal_cents") or 0)
        coupon_code, _inventory = self._pick_coupon_for_order(self.demo, subtotal, "flow")

        quote = self.demo.request(
            "POST",
            "/api/checkout/quote",
            {"coupon_code": coupon_code, "shipping_method": "standard"},
        )
        self.expect_status("flow.quote_http", quote, 200)
        quote_data = _res_data(quote)
        self.check(
            "flow.quote_totals",
            quote_data.get("subtotal_cents") == subtotal
            and int(quote_data.get("total_cents", -1))
            == subtotal
            - int(quote_data.get("discount_cents", 0))
            + int(quote_data.get("shipping_cents", 0)),
            f"subtotal={subtotal} discount={quote_data.get('discount_cents')} total={quote_data.get('total_cents')}",
        )
        self.check(
            "flow.quote_coupon_applied",
            (quote_data.get("coupon") or {}).get("code") == coupon_code,
            f"coupon_code={coupon_code}",
        )

        stock_before = self._stock(offer_id)
        key = self._new_key("flow")
        create_body = self._order_body(
            self.addresses["demo"], key, coupon_code=coupon_code, note="真实 HTTP 验收订单"
        )
        created = self.demo.request("POST", "/api/orders", create_body)
        self.expect_status("flow.order_create_http", created, 200)
        order_data = _res_data(created)
        order_no = order_data.get("order_no", "")
        self.check(
            "flow.order_initial_state",
            bool(order_no)
            and order_data.get("status") == "PENDING_PAYMENT"
            and order_data.get("status_label") == "待付款"
            and len(order_data.get("packages") or []) == 1,
            f"order_no={order_no} status={order_data.get('status')} packages={len(order_data.get('packages') or [])}",
        )
        self.check("flow.stock_decremented", self._stock(offer_id) == stock_before - 1, f"before={stock_before} after={self._stock(offer_id)}")
        package_items = (order_data.get("packages") or [{}])[0].get("items") or []
        self.check("flow.package_item_present", bool(package_items), f"items={len(package_items)}")
        if not order_no or not package_items:
            return
        item_id = int(package_items[0]["id"])
        cart_after = self._cart(self.demo)
        self.check(
            "flow.selected_cart_cleared",
            all(int(item["id"]) != cart_item_id for item in cart_after.get("items") or []),
            f"cart_items={len(cart_after.get('items') or [])}",
        )
        if coupon_code:
            with self._open_db() as conn:
                used = conn.execute(
                    "SELECT status FROM user_coupons WHERE user_id=? AND coupon_code=?",
                    (int(self.demo_user["id"]), coupon_code),
                ).fetchone()
            self.check(
                "flow.coupon_marked_used",
                used is not None and used["status"] == "USED",
                f"status={used['status'] if used else None}",
            )

        premature_review = self.demo.request(
            "POST",
            f"/api/orders/{order_no}/reviews",
            {
                "reviews": [
                    {
                        "item_id": item_id,
                        "rating": 5,
                        "title": "未完成",
                        "body": "订单尚未完成时提交的评价必须被服务端拒绝。",
                        "tags": [],
                    }
                ]
            },
        )
        self.expect_error("flow.review_before_complete", premature_review, 409, "ORDER_STATE")

        wrong_pay = self.demo.request(
            "POST", f"/api/orders/{order_no}/pay", {"payment_method": "cash"}
        )
        self.expect_error("flow.pay_non_demo", wrong_pay, 400, "VALIDATION_ERROR")
        paid = self.demo.request("POST", f"/api/orders/{order_no}/pay", {"payment_method": "demo"})
        self.expect_status("flow.pay_demo_http", paid, 200)
        paid_data = _res_data(paid)
        self.check(
            "flow.pay_state",
            paid_data.get("status") == "PAID" and paid_data.get("paid_at") is not None,
            f"status={paid_data.get('status')} paid_at={bool(paid_data.get('paid_at'))}",
        )
        self.check(
            "flow.no_auto_complete",
            paid_data.get("completed_at") is None
            and (paid_data.get("actions") or {}).get("confirm") is False,
            f"completed_at={paid_data.get('completed_at')} confirm={(paid_data.get('actions') or {}).get('confirm')}",
        )
        confirm_early = self.demo.request("POST", f"/api/orders/{order_no}/confirm", None)
        self.expect_error("flow.confirm_before_ship", confirm_early, 409, "ORDER_STATE")

        with self._open_db() as conn:
            row = conn.execute("SELECT id FROM orders WHERE order_no=?", (order_no,)).fetchone()
            order_id = int(row["id"])
            shop_row = conn.execute(
                "SELECT shop_id FROM order_packages WHERE order_id=? LIMIT 1", (order_id,)
            ).fetchone()
            package_shop_id = int(shop_row["shop_id"])
            shipped_at = dbutil.iso()
            conn.execute(
                """UPDATE order_packages SET status='SHIPPED',carrier='青鸟速运',tracking_no='TRADE8862A',
                     shipped_at=?,updated_at=? WHERE order_id=?""",
                (shipped_at, shipped_at, order_id),
            )
            conn.execute(
                "UPDATE orders SET status='SHIPPED',shipped_at=?,updated_at=? WHERE id=?",
                (shipped_at, shipped_at, order_id),
            )
            conn.commit()
        shipped = self.demo.request("GET", f"/api/orders/{order_no}")
        self.expect_status("flow.shipped_detail", shipped, 200)
        shipped_data = _res_data(shipped)
        self.check(
            "flow.actions_confirm",
            (shipped_data.get("actions") or {}).get("confirm") is True
            and shipped_data.get("status") == "SHIPPED",
            f"status={shipped_data.get('status')} confirm={(shipped_data.get('actions') or {}).get('confirm')}",
        )
        confirmed = self.demo.request("POST", f"/api/orders/{order_no}/confirm", None)
        self.expect_status("flow.confirm_http", confirmed, 200)
        confirmed_data = _res_data(confirmed)
        self.check(
            "flow.completed_state",
            confirmed_data.get("status") == "COMPLETED"
            and bool(confirmed_data.get("completed_at"))
            and (confirmed_data.get("actions") or {}).get("review") is True,
            f"status={confirmed_data.get('status')} review={(confirmed_data.get('actions') or {}).get('review')}",
        )
        first_item = ((confirmed_data.get("packages") or [{}])[0].get("items") or [{}])[0]
        product_id = int(first_item.get("product_id"))
        with self._open_db() as conn:
            before = conn.execute(
                "SELECT rating,review_count FROM products WHERE id=?", (product_id,)
            ).fetchone()
        self.check(
            "flow.item_reviewable",
            first_item.get("reviewable") is True and first_item.get("reviewed") is False,
            f"reviewable={first_item.get('reviewable')} reviewed={first_item.get('reviewed')}",
        )
        review_body = {
            "reviews": [
                {
                    "item_id": item_id,
                    "rating": 5,
                    "title": "验收很好",
                    "body": "这是一条通过真实 HTTP 流程提交的验收评价，质感与描述一致。",
                    "tags": ["验收好物"],
                }
            ]
        }
        reviewed = self.demo.request("POST", f"/api/orders/{order_no}/reviews", review_body)
        self.expect_status("flow.reviews_http", reviewed, 200)
        review_data = _res_data(reviewed)
        reviews = review_data.get("reviews") or []
        self.check(
            "flow.review_shape",
            len(reviews) == 1 and reviews[0].get("mine") is True and reviews[0].get("verified") is True,
            f"reviews={len(reviews)} mine={reviews[0].get('mine') if reviews else None}",
        )
        with self._open_db() as conn:
            after = conn.execute(
                "SELECT rating,review_count FROM products WHERE id=?", (product_id,)
            ).fetchone()
            aggregate = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(AVG(rating),0) AS avg_rating FROM reviews WHERE product_id=?",
                (product_id,),
            ).fetchone()
        self.check(
            "flow.review_aggregate_updated",
            int(after["review_count"]) == int(before["review_count"]) + 1
            and int(after["review_count"]) == int(aggregate["n"])
            and abs(float(after["rating"]) - round(float(aggregate["avg_rating"]), 2)) < 0.001,
            (
                f"before_count={before['review_count']} after_count={after['review_count']} "
                f"authoritative_count={aggregate['n']}"
            ),
        )
        detail_after_review = _res_data(self.demo.request("GET", f"/api/orders/{order_no}"))
        reviewed_item = ((detail_after_review.get("packages") or [{}])[0].get("items") or [{}])[0]
        self.check(
            "flow.reviewed_flags",
            reviewed_item.get("reviewed") is True
            and reviewed_item.get("reviewable") is False
            and (detail_after_review.get("actions") or {}).get("review") is False,
            f"reviewed={reviewed_item.get('reviewed')} action_review={(detail_after_review.get('actions') or {}).get('review')}",
        )
        duplicate_review = self.demo.request("POST", f"/api/orders/{order_no}/reviews", review_body)
        self.expect_error("flow.review_duplicate", duplicate_review, 409, "DUPLICATE_REVIEW")

        orders_list = self.demo.request("GET", "/api/orders?tab=all&page=1&page_size=12")
        self.expect_status("flow.orders_list_http", orders_list, 200)
        list_data = _res_data(orders_list)
        self.check(
            "flow.orders_list_shape",
            isinstance(list_data.get("items"), list)
            and set(list_data.get("counts") or {})
            == {"all", "unpaid", "to_ship", "to_receive", "completed", "after_sale"},
            f"items={len(list_data.get('items') or [])} counts={sorted((list_data.get('counts') or {}).keys())}",
        )

        refund = self.demo.request(
            "POST",
            f"/api/orders/{order_no}/refund",
            {"reason": "验收售后原因", "kind": "refund_only", "detail": "验收单申请售后"},
        )
        self.expect_status("flow.refund_http", refund, 200)
        refund_data = _res_data(refund)
        refunds = refund_data.get("refunds") or []
        self.check(
            "flow.refunding_state",
            refund_data.get("status") == "REFUNDING"
            and len(refunds) == 1
            and refunds[0].get("status") == "REQUESTED"
            and refunds[0].get("amount_cents") == refund_data.get("total_cents"),
            f"status={refund_data.get('status')} refunds={len(refunds)}",
        )
        with self._open_db() as conn:
            db_order = conn.execute(
                "SELECT status,last_state_before_refund FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            db_refund = conn.execute(
                "SELECT shop_id,status FROM refunds WHERE order_id=? ORDER BY id DESC LIMIT 1",
                (order_id,),
            ).fetchone()
        self.check(
            "flow.refund_db_state",
            db_order["status"] == "REFUNDING"
            and db_order["last_state_before_refund"] == "COMPLETED"
            and db_refund is not None
            and int(db_refund["shop_id"]) == package_shop_id
            and db_refund["status"] == "REQUESTED",
            f"order_status={db_order['status']} previous={db_order['last_state_before_refund']}",
        )
        aftersale_list = _res_data(
            self.demo.request("GET", "/api/orders?tab=after_sale&page=1&page_size=24")
        )
        self.check(
            "flow.aftersale_tab_contains_order",
            any(item.get("order_no") == order_no for item in aftersale_list.get("items") or []),
            f"aftersale_total={aftersale_list.get('total')}",
        )
        second_refund = self.demo.request(
            "POST",
            f"/api/orders/{order_no}/refund",
            {"reason": "重复售后", "kind": "refund_only"},
        )
        self.expect_error("flow.refund_duplicate", second_refund, 409, "ORDER_STATE")

    # -------------------------------------------------------------- test 05

    def test_05_group_true_progress(self):
        self._clear_cart(self.demo)
        self._clear_cart(self.beta)
        group_offer = self.group_offer
        add_initiator = self.demo.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(group_offer["offer_id"]),
                "sku_id": int(group_offer["sku_id"]),
                "quantity": 1,
                "buy_mode": "group",
            },
        )
        self.expect_status("group.initiator_cart", add_initiator, 200)
        group_quote = self.demo.request(
            "POST",
            "/api/checkout/quote",
            {"shipping_method": "express"},
        )
        self.expect_status("group.quote_http", group_quote, 200)
        group_quote_data = _res_data(group_quote)
        self.check(
            "group.express_forced_standard",
            group_quote_data.get("has_group") is True
            and (group_quote_data.get("shipping_method") or {}).get("code") == "standard"
            and group_quote_data.get("shipping_cents") == 0,
            (
                f"has_group={group_quote_data.get('has_group')} "
                f"shipping={(group_quote_data.get('shipping_method') or {}).get('code')} "
                f"fee={group_quote_data.get('shipping_cents')}"
            ),
        )
        init_key = self._new_key("group-init")
        init_create = self.demo.request(
            "POST",
            "/api/orders",
            self._order_body(self.addresses["demo"], init_key),
        )
        self.expect_status("group.initiator_create_http", init_create, 200)
        init_data = _res_data(init_create)
        init_order_no = init_data.get("order_no", "")
        initial_group = init_data.get("group") or {}
        self.check(
            "group.initiator_pending_state",
            init_data.get("status") == "PENDING_PAYMENT"
            and initial_group.get("joined_count") == 0
            and initial_group.get("status") == "FORMING",
            (
                f"status={init_data.get('status')} joined={initial_group.get('joined_count')} "
                f"group_status={initial_group.get('status')}"
            ),
        )
        group_code = initial_group.get("code")
        self.check("group.code_present", bool(group_code), f"group_code={group_code}")
        if not init_order_no or not group_code:
            return
        public = HttpClient(BASE_URL)
        public_group = public.request("GET", f"/api/groups/{group_code}")
        self.expect_status("group.public_view_http", public_group, 200)
        public_data = _res_data(public_group)
        self.check(
            "group.public_pending_not_counted",
            public_data.get("joined_count") == 0
            and public_data.get("members") == []
            and public_data.get("status") == "FORMING"
            and (public_data.get("actions") or {}).get("needs_login") is True,
            f"joined={public_data.get('joined_count')} members={len(public_data.get('members') or [])}",
        )
        init_pay = self.demo.request(
            "POST", f"/api/orders/{init_order_no}/pay", {"payment_method": "demo"}
        )
        self.expect_status("group.initiator_pay_http", init_pay, 200)
        init_paid_data = _res_data(init_pay)
        self.check(
            "group.initiator_forming",
            init_paid_data.get("status") == "GROUP_FORMING"
            and (init_paid_data.get("group") or {}).get("joined_count") == 1,
            f"status={init_paid_data.get('status')} joined={(init_paid_data.get('group') or {}).get('joined_count')}",
        )
        joined_view = _res_data(self.demo.request("GET", f"/api/groups/{group_code}"))
        self.check(
            "group.joined_by_me_true",
            joined_view.get("joined_by_me") is True
            and joined_view.get("joined_count") == 1
            and len(joined_view.get("members") or []) == 1,
            f"joined_by_me={joined_view.get('joined_by_me')} joined={joined_view.get('joined_count')}",
        )

        add_joiner = self.beta.request(
            "POST",
            "/api/cart/items",
            {
                "offer_id": int(group_offer["offer_id"]),
                "sku_id": int(group_offer["sku_id"]),
                "quantity": 1,
                "buy_mode": "group",
                "group_code": group_code,
            },
        )
        self.expect_status("group.joiner_cart", add_joiner, 200)
        join_key = self._new_key("group-join")
        join_create = self.beta.request(
            "POST",
            "/api/orders",
            self._order_body(self.addresses["beta"], join_key),
        )
        self.expect_status("group.joiner_create_http", join_create, 200)
        join_data = _res_data(join_create)
        join_order_no = join_data.get("order_no", "")
        self.check(
            "group.joiner_pending_not_counted",
            bool(join_order_no)
            and (join_data.get("group") or {}).get("joined_count") == 1
            and (join_data.get("group") or {}).get("status") == "FORMING",
            f"joined={(join_data.get('group') or {}).get('joined_count')}",
        )
        if not join_order_no:
            return
        pending_public = _res_data(public.request("GET", f"/api/groups/{group_code}"))
        self.check(
            "group.pending_member_public_count",
            pending_public.get("joined_count") == 1,
            f"joined={pending_public.get('joined_count')}",
        )
        join_pay = self.beta.request(
            "POST", f"/api/orders/{join_order_no}/pay", {"payment_method": "demo"}
        )
        self.expect_status("group.joiner_pay_http", join_pay, 200)
        join_paid_data = _res_data(join_pay)
        self.check(
            "group.joiner_paid_full",
            join_paid_data.get("status") == "PAID"
            and (join_paid_data.get("group") or {}).get("status") == "FULL"
            and (join_paid_data.get("group") or {}).get("joined_count") == 2,
            f"status={join_paid_data.get('status')} group={(join_paid_data.get('group') or {}).get('status')}",
        )
        full_public = _res_data(public.request("GET", f"/api/groups/{group_code}"))
        self.check(
            "group.full_is_real",
            full_public.get("status") == "FULL"
            and full_public.get("joined_count") == 2
            and len(full_public.get("members") or []) == 2
            and (full_public.get("actions") or {}).get("can_join") is False,
            (
                f"status={full_public.get('status')} joined={full_public.get('joined_count')} "
                f"members={len(full_public.get('members') or [])}"
            ),
        )
        init_final = _res_data(self.demo.request("GET", f"/api/orders/{init_order_no}"))
        self.check(
            "group.all_paid_members_updated",
            init_final.get("status") == "PAID" and (init_final.get("group") or {}).get("status") == "FULL",
            f"status={init_final.get('status')} group={(init_final.get('group') or {}).get('status')}",
        )
        with self._open_db() as conn:
            group_row = conn.execute("SELECT status FROM groups WHERE code=?", (group_code,)).fetchone()
            paid_row = conn.execute(
                """SELECT COUNT(*) AS n FROM group_members gm JOIN groups g ON g.id=gm.group_id
                   WHERE g.code=? AND gm.paid_at IS NOT NULL""",
                (group_code,),
            ).fetchone()
        self.check(
            "group.db_paid_count",
            group_row is not None and group_row["status"] == "FULL" and int(paid_row["n"]) == 2,
            f"db_status={group_row['status'] if group_row else None} paid={int(paid_row['n'])}",
        )

    # -------------------------------------------------------------- test 06

    def test_06_idempotency_replay_and_order_ownership(self):
        self._clear_cart(self.alpha)
        offer_id = int(self.standard_offer["offer_id"])
        added = self._add_standard(self.alpha)
        self.expect_status("idem.cart_add", added, 200)
        stock_before = self._stock(offer_id)
        key = self._new_key("idem")
        body = self._order_body(self.addresses["alpha"], key, note="idem-original")
        first = self.alpha.request("POST", "/api/orders", body)
        self.expect_status("idem.create_http", first, 200)
        first_data = _res_data(first)
        order_no = first_data.get("order_no", "")
        stock_after_create = self._stock(offer_id)
        self.check(
            "idem.stock_deducted_once",
            bool(order_no) and stock_after_create == stock_before - 1,
            f"before={stock_before} after={stock_after_create}",
        )
        if not order_no:
            return
        replay = self.alpha.request("POST", "/api/orders", body)
        self.expect_status("idem.replay_http", replay, 200)
        replay_data = _res_data(replay)
        self.check(
            "idem.same_order",
            replay_data.get("order_no") == order_no and replay_data.get("status") == "PENDING_PAYMENT",
            f"order_no={replay_data.get('order_no')}",
        )
        self.check(
            "idem.no_double_deduct",
            self._stock(offer_id) == stock_after_create,
            f"stock={self._stock(offer_id)} expected={stock_after_create}",
        )
        conflict_body = dict(body)
        conflict_body["note"] = "idem-mutated"
        conflict = self.alpha.request("POST", "/api/orders", conflict_body)
        self.expect_error("idem.conflict", conflict, 409, "IDEMPOTENCY_CONFLICT")
        with self._open_db() as conn:
            count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND idempotency_key=?",
                (int(self.alpha_user["id"]), key),
            ).fetchone()
        self.check("idem.single_row", int(count_row["n"]) == 1, f"orders={int(count_row['n'])}")

        foreign_get = self.beta.request("GET", f"/api/orders/{order_no}")
        self.expect_error("authz.order_get_404", foreign_get, 404, "NOT_FOUND")
        foreign_pay = self.beta.request(
            "POST", f"/api/orders/{order_no}/pay", {"payment_method": "demo"}
        )
        self.expect_error("authz.order_pay_404", foreign_pay, 404, "NOT_FOUND")

    # -------------------------------------------------------------- test 07

    def test_07_multishop_packages_and_refund_boundary(self):
        self._clear_cart(self.alpha)
        if len(self.shop_offers) < 2:
            self.check("multishop.fixture", False, "need two shops")
            return
        for offer in self.shop_offers[:2]:
            added = self.alpha.request(
                "POST",
                "/api/cart/items",
                {
                    "offer_id": int(offer["offer_id"]),
                    "sku_id": int(offer["sku_id"]),
                    "quantity": 1,
                    "buy_mode": "standard",
                },
            )
            self.expect_status(f"multishop.add_offer_{offer['offer_id']}", added, 200)
        cart = self._cart(self.alpha)
        self.check(
            "multishop.cart_shops",
            len(cart.get("shops") or []) == 2,
            f"shops={len(cart.get('shops') or [])}",
        )
        key = self._new_key("multishop")
        created = self.alpha.request(
            "POST",
            "/api/orders",
            self._order_body(self.addresses["alpha"], key, note="多店铺拆包验收"),
        )
        self.expect_status("multishop.create_http", created, 200)
        create_data = _res_data(created)
        order_no = create_data.get("order_no", "")
        packages = create_data.get("packages") or []
        package_shop_ids = {int(package.get("shop", {}).get("id", 0)) for package in packages}
        self.check(
            "multishop.package_split",
            len(packages) == 2
            and len(package_shop_ids) == 2
            and all(len(package.get("items") or []) == 1 for package in packages),
            f"order_no={order_no} packages={len(packages)} shops={len(package_shop_ids)}",
        )
        if not order_no:
            return
        paid = self.alpha.request("POST", f"/api/orders/{order_no}/pay", {"payment_method": "demo"})
        self.expect_status("multishop.pay_http", paid, 200)
        refund = self.alpha.request(
            "POST",
            f"/api/orders/{order_no}/refund",
            {"reason": "多店售后验收", "kind": "refund_only"},
        )
        self.expect_error("refund.multishop_unsupported", refund, 422, "AFTER_SALES_UNSUPPORTED")

    # -------------------------------------------------------------- test 08

    def test_08_cancel_restores_stock_and_coupon(self):
        self._clear_cart(self.beta)
        offer = self.standard_offer
        offer_id = int(offer["offer_id"])
        added = self._add_standard(self.beta)
        self.expect_status("cancel.cart_add", added, 200)
        cart = _res_data(added)
        subtotal = int((cart.get("summary") or {}).get("subtotal_cents") or 0)
        coupon_code, _inventory = self._pick_coupon_for_order(self.beta, subtotal, "cancel")
        stock_before = self._stock(offer_id)
        key = self._new_key("cancel")
        created = self.beta.request(
            "POST",
            "/api/orders",
            self._order_body(self.addresses["beta"], key, coupon_code=coupon_code, note="取消恢复验收"),
        )
        self.expect_status("cancel.create_http", created, 200)
        order_data = _res_data(created)
        order_no = order_data.get("order_no", "")
        self.check(
            "cancel.created_pending",
            bool(order_no) and order_data.get("status") == "PENDING_PAYMENT",
            f"status={order_data.get('status')}",
        )
        if not order_no:
            return
        self.check(
            "cancel.stock_held",
            self._stock(offer_id) == stock_before - 1,
            f"stock={self._stock(offer_id)} before={stock_before}",
        )
        with self._open_db() as conn:
            row = conn.execute("SELECT id FROM orders WHERE order_no=?", (order_no,)).fetchone()
            order_id = int(row["id"])
            used = conn.execute(
                """SELECT uc.id,uc.status FROM user_coupons uc
                   WHERE uc.user_id=? AND uc.coupon_code=?""",
                (int(self.beta_user["id"]), coupon_code),
            ).fetchone()
        self.check(
            "cancel.coupon_held",
            used is not None and used["status"] == "USED",
            f"coupon_status={used['status'] if used else None}",
        )
        canceled = self.beta.request(
            "POST", f"/api/orders/{order_no}/cancel", {"reason": "验收主动取消"}
        )
        self.expect_status("cancel.http", canceled, 200)
        cancel_data = _res_data(canceled)
        self.check(
            "cancel.state",
            cancel_data.get("status") == "CANCELLED" and bool(cancel_data.get("cancelled_at")),
            f"status={cancel_data.get('status')} cancelled_at={bool(cancel_data.get('cancelled_at'))}",
        )
        self.check(
            "cancel.stock_restored",
            self._stock(offer_id) == stock_before,
            f"stock={self._stock(offer_id)} expected={stock_before}",
        )
        with self._open_db() as conn:
            coupon_after = conn.execute(
                "SELECT status,used_at,order_id FROM user_coupons WHERE id=?", (int(used["id"]),)
            ).fetchone()
            package = conn.execute(
                "SELECT status FROM order_packages WHERE order_id=?", (order_id,)
            ).fetchone()
            cart_row = conn.execute(
                "SELECT COUNT(*) AS n FROM cart_items ci JOIN carts c ON c.id=ci.cart_id WHERE c.owner_key=?",
                (f"u:{int(self.beta_user['id'])}",),
            ).fetchone()
        self.check(
            "cancel.coupon_restored",
            coupon_after is not None
            and coupon_after["status"] == "AVAILABLE"
            and coupon_after["used_at"] is None
            and coupon_after["order_id"] is None,
            f"status={coupon_after['status'] if coupon_after else None}",
        )
        self.check(
            "cancel.package_cancelled",
            package is not None and package["status"] == "CANCELLED",
            f"package_status={package['status'] if package else None}",
        )
        self.check("cancel.cart_not_restored", int(cart_row["n"]) == 0, f"cart_items={int(cart_row['n'])}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
