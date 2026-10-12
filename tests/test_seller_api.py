#!/usr/bin/env python3
"""Real-HTTP acceptance tests for the seller workbench API.

Run from the repository root:
  sudo -n -u infra env PYTHONDONTWRITEBYTECODE=1 \
    python3 -B tests/test_seller_api.py -v

The suite boots its own server on 127.0.0.1:8865 with an isolated data
directory under /hf3fs-jd/data/tmp/openshop/. It never touches another seat's
database or the committed seed. The data directory and server log are left in
place as evidence and the exact paths are printed at the end.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import unittest
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.commerce import auth as authmod  # noqa: E402
from packages.commerce import db as dbutil  # noqa: E402
from packages.commerce.models import CARRIERS  # noqa: E402

HOST = "127.0.0.1"
PORT = 8865
TMP_BASE = Path("/hf3fs-jd/data/tmp/openshop")
CST = timezone(timedelta(hours=8))
FORBIDDEN_RESPONSE_KEYS = {
    "address",
    "address_json",
    "receiver",
    "phone",
    "province",
    "district",
}


class Reply:
    def __init__(self, status: int, text: str, headers: dict[str, str], body: Any):
        self.status = status
        self.text = text
        self.headers = headers
        self.body = body

    @property
    def data(self) -> Any:
        if not isinstance(self.body, dict):
            return None
        return self.body.get("data")

    @property
    def error(self) -> dict[str, Any]:
        if isinstance(self.body, dict) and isinstance(self.body.get("error"), dict):
            return self.body["error"]
        return {}

    @property
    def code(self) -> str:
        return str(self.error.get("code", ""))

    def label(self) -> str:
        return f"HTTP {self.status} {self.code}"


class Client:
    def __init__(self) -> None:
        self.cookie = ""
        self.csrf = ""

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        csrf: str | None = "auto",
    ) -> Reply:
        method = method.upper()
        headers = {"Accept": "application/json", "Connection": "close"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        token = self.csrf if csrf == "auto" else csrf
        if token:
            headers["X-OpenShop-CSRF"] = str(token)
        payload = None
        if method not in {"GET", "HEAD"}:
            payload = json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection(HOST, PORT, timeout=15)
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            text = raw.decode("utf-8", "replace")
            for cookie in response.headers.get_all("Set-Cookie") or []:
                first = cookie.split(";", 1)[0]
                if first.startswith("openshop_session="):
                    self.cookie = first
            parsed = None
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            return Reply(response.status, text, dict(response.headers.items()), parsed)
        finally:
            conn.close()

    def get(self, path: str) -> Reply:
        return self.request("GET", path)

    def post(self, path: str, body: Any, *, csrf: str | None = "auto") -> Reply:
        return self.request("POST", path, body, csrf=csrf)

    def patch(self, path: str, body: Any, *, csrf: str | None = "auto") -> Reply:
        return self.request("PATCH", path, body, csrf=csrf)

    def ensure_session(self) -> dict[str, Any]:
        reply = self.get("/api/session")
        if reply.status != 200 or not isinstance(reply.data, dict):
            raise AssertionError(f"/api/session failed: {reply.label()} {reply.text[:300]}")
        self.csrf = str(reply.data.get("csrf") or "")
        if not self.csrf:
            raise AssertionError("session response carried no csrf")
        return reply.data

    def login_demo(self, role: str) -> dict[str, Any]:
        self.ensure_session()
        reply = self.post("/api/auth/demo", {"role": role})
        if reply.status != 200 or not isinstance(reply.data, dict):
            raise AssertionError(f"demo {role} login failed: {reply.label()} {reply.text[:300]}")
        self.csrf = str(reply.data.get("csrf") or "")
        if (reply.data.get("user") or {}).get("role") != role:
            raise AssertionError(f"demo login returned wrong role: {reply.text[:300]}")
        return reply.data

    def login_password(self, identifier: str, password: str) -> dict[str, Any]:
        self.ensure_session()
        reply = self.post("/api/auth/login", {"identifier": identifier, "password": password})
        if reply.status != 200 or not isinstance(reply.data, dict):
            raise AssertionError(f"password login failed: {reply.label()} {reply.text[:300]}")
        self.csrf = str(reply.data.get("csrf") or "")
        return reply.data


class SellerApiTests(unittest.TestCase):
    maxDiff = 4000
    tmp_dir: Path
    db_path: Path
    server_log: Path
    proc: subprocess.Popen | None = None
    log_handle: Any = None

    # ---------- server lifecycle ----------

    @classmethod
    def _wait_health(cls, timeout_seconds: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            if cls.proc is not None and cls.proc.poll() is not None:
                raise AssertionError(f"server exited early with code {cls.proc.returncode}")
            try:
                conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
                conn.request("GET", "/api/health")
                response = conn.getresponse()
                body = response.read().decode("utf-8", "replace")
                conn.close()
                if response.status == 200 and '"ok":true' in body.replace(" ", ""):
                    return
                last_error = f"health status={response.status} body={body[:200]}"
            except (OSError, http.client.HTTPException) as exc:
                last_error = repr(exc)
            time.sleep(0.2)
        raise AssertionError(f"server did not become healthy within {timeout_seconds}s: {last_error}")

    @classmethod
    def _stop_server(cls) -> None:
        if cls.proc is not None and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait(timeout=5)
        if cls.log_handle is not None:
            cls.log_handle.flush()
            cls.log_handle.close()
            cls.log_handle = None

    @classmethod
    def _log_tail(cls, limit: int = 4000) -> str:
        try:
            return cls.server_log.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return "<no server log>"

    @classmethod
    def setUpClass(cls) -> None:
        TMP_BASE.mkdir(parents=True, exist_ok=True)
        suffix = f"{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        cls.tmp_dir = TMP_BASE / f"merchant-api-{suffix}"
        cls.tmp_dir.mkdir()
        data_dir = cls.tmp_dir / "data"
        cls.db_path = data_dir / "openshop.sqlite3"
        cls.server_log = cls.tmp_dir / "server.log"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, PORT))
            except OSError as exc:
                raise AssertionError(f"port {PORT} is already in use; refusing to reuse another server") from exc

        cls.log_handle = cls.server_log.open("w", encoding="utf-8")
        command = [
            sys.executable,
            "-B",
            str(REPO_ROOT / "apps/storefront/server.py"),
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--data-dir",
            str(data_dir),
            "--log-level",
            "INFO",
        ]
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cls.proc = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            stdout=cls.log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            cls._wait_health()
            cls.seller = Client()
            cls.seller_session = cls.seller.login_demo("seller")
            cls.seller_user_id = int(cls.seller_session["user"]["id"])
            cls.shop_id = int(cls.seller_session["user"]["shop_id"])

            cls.buyer = Client()
            cls.buyer_session = cls.buyer.login_demo("buyer")

            with closing(dbutil.connect(cls.db_path)) as conn:
                buyer_row = dbutil.one(conn, "SELECT id FROM users WHERE handle='demo_buyer'")
                cls.buyer_id = int(buyer_row["id"])
                buyer_address = dbutil.one(
                    conn,
                    "SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC,id LIMIT 1",
                    (cls.buyer_id,),
                )
                assert buyer_address is not None, "seed buyer address missing"
                cls.buyer_full_name = str(buyer_address["receiver"])
                cls.buyer_phone = str(buyer_address["phone"])
                cls.buyer_detail = str(buyer_address["detail"])
                self_offer = dbutil.one(
                    conn,
                    """SELECT o.id, o.product_id, o.price_cents, o.market_price_cents, o.shop_id,
                              p.title AS product_title
                       FROM offers o JOIN products p ON p.id=o.product_id
                       WHERE o.shop_id=? ORDER BY o.is_primary DESC, o.id LIMIT 1""",
                    (cls.shop_id,),
                )
                assert self_offer is not None, "seed has no offer for demo seller"
                cls.self_offer_id = int(self_offer["id"])
                cls.self_product_id = int(self_offer["product_id"])
                cls.self_product_title = str(self_offer["product_title"])

                brand_offer = dbutil.one(
                    conn,
                    """SELECT o.id, o.product_id, o.shop_id, s.name AS shop_name
                       FROM offers o JOIN shops s ON s.id=o.shop_id
                       WHERE o.shop_id!=? ORDER BY o.id LIMIT 1""",
                    (cls.shop_id,),
                )
                assert brand_offer is not None, "seed has no other-shop offer"
                cls.brand_offer_id = int(brand_offer["id"])
                cls.brand_product_id = int(brand_offer["product_id"])
                cls.brand_shop_id = int(brand_offer["shop_id"])

            cls.zero_seller_email, cls.zero_seller_password, cls.zero_shop_id = cls._create_zero_seller()
            cls.zero_seller = Client()
            zero_session = cls.zero_seller.login_password(cls.zero_seller_email, cls.zero_seller_password)
            if (zero_session.get("user") or {}).get("role") != "seller":
                raise AssertionError("zero-data seller login did not produce a seller session")
        except Exception:
            cls._stop_server()
            print("SERVER STARTUP/TEST SETUP FAILURE", cls._log_tail(), sep="\n", file=sys.stderr)
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls._stop_server()
        finally:
            print(f"SELLER_API_EVIDENCE data_dir={cls.tmp_dir / 'data'} server_log={cls.server_log}")

    @classmethod
    def _create_zero_seller(cls) -> tuple[str, str, int]:
        suffix = uuid.uuid4().hex[:10]
        email = f"zero-seller-{suffix}@openshop.test"
        password = f"ZeroSeller-{suffix}-Aa1"
        password_hash, salt = authmod.hash_password(password)
        now = dbutil.iso()
        with closing(dbutil.connect(cls.db_path)) as conn:
            with dbutil.transaction(conn):
                cur = conn.execute(
                    """INSERT INTO shops(slug,name,kind,tagline,story,rating,followers_count,since,verified,
                                         response_rate,ship_from,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"merchant-zero-{suffix}",
                        "零单测试铺",
                        "factory",
                        "用于验证空数据经营概览",
                        "只存在于隔离验收库中的测试店铺。",
                        4.6,
                        0,
                        "2026",
                        1,
                        "99%",
                        "杭州 · 测试仓",
                        now,
                    ),
                )
                shop_id = int(cur.lastrowid)
                conn.execute(
                    """INSERT INTO users(handle,display_name,email,role,password_hash,password_salt,avatar,
                                         shop_id,member_since,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"zero_seller_{suffix}",
                        "零单掌柜",
                        email,
                        "seller",
                        password_hash,
                        salt,
                        "/web/assets/brand/avatar-zhou.svg",
                        shop_id,
                        "2026-01-01",
                        now,
                    ),
                )
        return email, password, shop_id

    # ---------- helpers ----------

    def db(self) -> sqlite3.Connection:
        return dbutil.connect(self.db_path)

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with closing(self.db()) as conn:
            return dbutil.one(conn, sql, params)

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        with closing(self.db()) as conn:
            return dbutil.scalar(conn, sql, params)

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with closing(self.db()) as conn:
            return dbutil.all_rows(conn, sql, params)

    def assert_ok(self, reply: Reply, message: str = "") -> Any:
        self.assertEqual(reply.status, 200, f"{message} expected 200, got {reply.label()}: {reply.text[:600]}")
        self.assertIsInstance(reply.body, dict, f"{message} response must be a JSON object")
        self.assertIn("data", reply.body, f"{message} missing data envelope")
        return reply.data

    def assert_error(self, reply: Reply, status: int, code: str, message: str = "") -> dict[str, Any]:
        self.assertEqual(reply.status, status, f"{message} expected {status}/{code}, got {reply.label()}: {reply.text[:600]}")
        self.assertEqual(reply.code, code, f"{message} expected code {code}, got {reply.label()}: {reply.text[:600]}")
        self.assertIsInstance(reply.error, dict)
        return reply.error

    def assert_no_address_leak(self, reply: Reply, message: str = "") -> None:
        self.assertNotIn(self.buyer_phone, reply.text, f"{message}: buyer phone leaked")
        self.assertNotIn(self.buyer_full_name, reply.text, f"{message}: full buyer name leaked")
        self.assertNotIn(self.buyer_detail, reply.text, f"{message}: buyer street address leaked")
        self.assertNotIn("漕溪北路", reply.text, f"{message}: buyer street leaked")

        def walk(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key, FORBIDDEN_RESPONSE_KEYS, f"{message}: forbidden key {path}{key}")
                    walk(item, f"{path}{key}.")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}].")

        walk(reply.body, "")

    def offer_row(self, offer_id: int) -> dict[str, Any]:
        with closing(self.db()) as conn:
            row = dbutil.one(
                conn,
                """SELECT o.*, p.title AS product_title, p.cover AS cover, p.slug AS product_slug,
                          p.id AS prod_id, s.name AS shop_name, s.kind AS shop_kind,
                          (SELECT id FROM skus WHERE product_id=o.product_id ORDER BY sort_order,id LIMIT 1) AS sku_id,
                          (SELECT name FROM skus WHERE product_id=o.product_id ORDER BY sort_order,id LIMIT 1) AS sku_name,
                          (SELECT price_delta_cents FROM skus WHERE product_id=o.product_id ORDER BY sort_order,id LIMIT 1) AS sku_delta
                   FROM offers o JOIN products p ON p.id=o.product_id JOIN shops s ON s.id=o.shop_id
                   WHERE o.id=?""",
                (int(offer_id),),
            )
        if row is None:
            raise AssertionError(f"offer {offer_id} not found")
        return dict(row)

    def make_offer(
        self,
        *,
        shop_id: int,
        product_id: int,
        stock: int,
        name: str | None = None,
        status: str = "active",
        price_cents: int = 18800,
        market_price_cents: int = 28800,
    ) -> int:
        token = uuid.uuid4().hex[:8].upper()
        now = dbutil.iso()
        with closing(self.db()) as conn:
            with dbutil.transaction(conn):
                cur = conn.execute(
                    """INSERT INTO offers(product_id,shop_id,kind,name,price_cents,market_price_cents,
                                          shipping_fee_cents,stock,delivery_eta_days,delivery_note,services_json,
                                          group_size,group_price_cents,is_primary,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(product_id),
                        int(shop_id),
                        "self" if int(shop_id) == self.shop_id else "brand",
                        name or f"验收报价 {token}",
                        int(price_cents),
                        int(market_price_cents),
                        0,
                        int(stock),
                        2,
                        "验收仓 · 标准配送",
                        '["正品保障"]',
                        None,
                        None,
                        0,
                        status,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)

    def make_order(
        self,
        *,
        status: str = "PAID",
        packages: list[dict[str, Any]],
        items: list[dict[str, Any]],
        last_state_before_refund: str | None = None,
    ) -> dict[str, Any]:
        assert packages, "at least one package is required"
        now = dbutil.utc_now().replace(microsecond=0)
        created = now - timedelta(minutes=3)
        paid_at = dbutil.iso(created + timedelta(minutes=1)) if status not in {"PENDING_PAYMENT", "CANCELLED"} else None
        order_no = "OST" + uuid.uuid4().hex[:14].upper()
        idempotency = "test-" + uuid.uuid4().hex
        with closing(self.db()) as conn:
            buyer = dbutil.one(conn, "SELECT * FROM users WHERE id=?", (self.buyer_id,))
            address = dbutil.one(
                conn,
                "SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC,id LIMIT 1",
                (self.buyer_id,),
            )
            assert buyer is not None and address is not None, "seed buyer/address missing"
            snapshot = {
                "id": int(address["id"]),
                "label": address["label"],
                "receiver": address["receiver"],
                "phone": address["phone"],
                "province": address["province"],
                "city": address["city"],
                "district": address["district"],
                "detail": address["detail"],
            }
            loaded_items = []
            subtotal = 0
            market_subtotal = 0
            for item in items:
                offer = self.offer_row(int(item["offer_id"]))
                sku = dbutil.one(
                    conn,
                    "SELECT * FROM skus WHERE id=? AND product_id=?",
                    (int(offer["sku_id"]), int(offer["product_id"])),
                )
                if sku is None:
                    raise AssertionError(f"sku missing for offer {offer['id']}")
                quantity = int(item.get("quantity", 1))
                unit = int(offer["price_cents"]) + int(sku["price_delta_cents"])
                market_unit = int(offer["market_price_cents"]) + int(sku["price_delta_cents"])
                package_index = int(item.get("package_index", 0))
                package = packages[package_index]
                if int(offer["shop_id"]) != int(package["shop_id"]):
                    raise AssertionError("fixture item shop does not match fixture package shop")
                subtotal += unit * quantity
                market_subtotal += market_unit * quantity
                loaded_items.append(
                    {
                        "package_index": package_index,
                        "product_id": int(offer["product_id"]),
                        "offer_id": int(offer["id"]),
                        "sku_id": int(sku["id"]),
                        "shop_id": int(offer["shop_id"]),
                        "title": offer["product_title"],
                        "cover": offer["cover"],
                        "sku_label": sku["name"],
                        "unit_price_cents": unit,
                        "quantity": quantity,
                        "offer_kind": offer["kind"],
                        "services_json": offer["services_json"],
                    }
                )
            with dbutil.transaction(conn):
                shipped_at = dbutil.iso(created + timedelta(minutes=5)) if status in {"SHIPPED", "COMPLETED"} else None
                completed_at = dbutil.iso(created + timedelta(hours=2)) if status == "COMPLETED" else None
                refunded_at = dbutil.iso(created + timedelta(hours=3)) if status == "REFUNDED" else None
                cur = conn.execute(
                    """INSERT INTO orders(order_no,user_id,status,subtotal_cents,discount_cents,shipping_cents,
                                          total_cents,market_subtotal_cents,coupon_code,user_coupon_id,address_json,
                                          shipping_method,payment_method,note,created_at,updated_at,paid_at,shipped_at,
                                          completed_at,refunded_at,idempotency_key,last_state_before_refund)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        order_no, self.buyer_id, status, subtotal, 0, 0, subtotal, market_subtotal,
                        None, None, json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                        "standard", "demo", "", dbutil.iso(created), dbutil.iso(created), paid_at, shipped_at,
                        completed_at, refunded_at, idempotency, last_state_before_refund,
                    ),
                )
                order_id = int(cur.lastrowid)
                package_ids: list[int] = []
                for index, package in enumerate(packages):
                    package_status = package.get("status", "PENDING")
                    pkg_shipped_at = shipped_at if package_status in {"SHIPPED", "DELIVERED"} else None
                    carrier = package.get("carrier", CARRIERS[0] if package_status in {"SHIPPED", "DELIVERED"} else None)
                    tracking = package.get("tracking_no")
                    if package_status in {"SHIPPED", "DELIVERED"} and not tracking:
                        tracking = "TEST" + uuid.uuid4().hex[:10].upper()
                    cur = conn.execute(
                        """INSERT INTO order_packages(order_id,shop_id,status,carrier,tracking_no,shipping_method,
                                                     shipped_at,delivered_at,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (
                            order_id, int(package["shop_id"]), package_status, carrier, tracking, "standard",
                            pkg_shipped_at, pkg_shipped_at if package_status == "DELIVERED" else None,
                            dbutil.iso(created), dbutil.iso(created),
                        ),
                    )
                    package_ids.append(int(cur.lastrowid))
                for item in loaded_items:
                    conn.execute(
                        """INSERT INTO order_items(order_id,package_id,product_id,offer_id,sku_id,shop_id,title,
                                                   cover,sku_label,unit_price_cents,quantity,buy_mode,offer_kind,
                                                   services_json)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            order_id, package_ids[item["package_index"]], item["product_id"], item["offer_id"],
                            item["sku_id"], item["shop_id"], item["title"], item["cover"], item["sku_label"],
                            item["unit_price_cents"], item["quantity"], "standard", item["offer_kind"],
                            item["services_json"],
                        ),
                    )
                conn.execute(
                    "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
                    (order_id, None, "订单已创建", "验收夹具", dbutil.iso(created)),
                )
                if paid_at:
                    conn.execute(
                        "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
                        (order_id, None, "支付成功", "验收夹具模拟支付", paid_at),
                    )
            return {
                "order_id": order_id,
                "order_no": order_no,
                "package_ids": package_ids,
                "items": loaded_items,
                "subtotal_cents": subtotal,
                "total_cents": subtotal,
            }

    def attach_used_coupon(self, order_id: int, *, expired: bool = False) -> dict[str, Any]:
        suffix = uuid.uuid4().hex[:8].upper()
        code = "TS" + suffix
        now = dbutil.utc_now().replace(microsecond=0)
        now_iso = dbutil.iso(now)
        expires_iso = dbutil.iso(now - timedelta(hours=2) if expired else now + timedelta(days=20))
        discount = 500
        with closing(self.db()) as conn:
            with dbutil.transaction(conn):
                conn.execute(
                    """INSERT INTO coupons(code,title,description,threshold_cents,discount_cents,scope,valid_days,
                                           auto_claim_new_user,quantity,claimed_count,created_at)
                       VALUES(?,?,?,?,?,'all',30,0,20,1,?)""",
                    (code, "验收用券", "验收夹具优惠券", 0, discount, now_iso),
                )
                cur = conn.execute(
                    """INSERT INTO user_coupons(user_id,coupon_code,status,claimed_at,expires_at,used_at,order_id)
                       VALUES(?,?,'USED',?,?,?,?)""",
                    (self.buyer_id, code, now_iso, expires_iso, now_iso, int(order_id)),
                )
                coupon_id = int(cur.lastrowid)
                conn.execute(
                    "UPDATE orders SET coupon_code=?,user_coupon_id=?,discount_cents=?,total_cents=? WHERE id=?",
                    (code, coupon_id, discount, max(0, int(self.scalar("SELECT subtotal_cents FROM orders WHERE id=?", (int(order_id),)) or 0) - discount), int(order_id)),
                )
        return {"coupon_id": coupon_id, "code": code, "status": "EXPIRED" if expired else "AVAILABLE", "discount_cents": discount}

    def add_refund(
        self,
        order_id: int,
        *,
        shop_id: int,
        status: str = "REQUESTED",
        reason: str = "验收退款原因",
        kind: str = "refund_only",
    ) -> int:
        order = self.fetch_one("SELECT * FROM orders WHERE id=?", (int(order_id),))
        if order is None:
            raise AssertionError(f"order {order_id} missing")
        now = dbutil.iso()
        with closing(self.db()) as conn:
            with dbutil.transaction(conn):
                cur = conn.execute(
                    """INSERT INTO refunds(order_id,order_no,user_id,shop_id,kind,reason,detail,amount_cents,status,
                                           decision_note,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(order_id), order["order_no"], int(order["user_id"]), int(shop_id), kind, reason,
                        "验收售后明细", int(order["total_cents"]), status, None, now, now,
                    ),
                )
                refund_id = int(cur.lastrowid)
                conn.execute(
                    "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
                    (int(order_id), None, "买家申请售后", f"售后 #{refund_id}", now),
                )
        return refund_id

    def make_refund_case(
        self,
        *,
        stock: int,
        previous_status: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        offer_id = self.make_offer(shop_id=self.shop_id, product_id=self.self_product_id, stock=stock)
        order = self.make_order(
            status="REFUNDING",
            last_state_before_refund=previous_status,
            packages=[{"shop_id": self.shop_id, "status": "SHIPPED"}],
            items=[{"offer_id": offer_id, "quantity": quantity}],
        )
        refund_id = self.add_refund(order["order_id"], shop_id=self.shop_id)
        return {"offer_id": offer_id, "order": order, "refund_id": refund_id, "coupon": None}

    # ---------- tests ----------

    def test_01_seller_routes_reject_guest_and_buyer(self) -> None:
        guest = Client()
        guest.ensure_session()
        seller_gets = [
            "/api/seller/overview",
            "/api/seller/products",
            "/api/seller/orders",
            "/api/seller/refunds",
        ]
        for path in seller_gets:
            with self.subTest(role="guest", path=path):
                self.assert_error(guest.get(path), 401, "AUTH_REQUIRED", "guest seller read")
        for path in seller_gets:
            with self.subTest(role="buyer", path=path):
                self.assert_error(self.buyer.get(path), 403, "FORBIDDEN", "buyer seller read")

        write_cases = [
            ("PATCH", "/api/seller/products/1", {"price_cents": 100}),
            ("POST", "/api/seller/orders/1/ship", {"carrier": CARRIERS[0], "tracking_no": "ABC123"}),
            ("POST", "/api/seller/refunds/1/decision", {"action": "approve", "note": "x"}),
        ]
        for method, path, body in write_cases:
            with self.subTest(role="guest", path=path):
                reply = guest.request(method, path, body)
                self.assert_error(reply, 401, "AUTH_REQUIRED", "guest seller write")
            with self.subTest(role="buyer", path=path):
                reply = self.buyer.request(method, path, body)
                self.assert_error(reply, 403, "FORBIDDEN", "buyer seller write")
        self.assert_error(self.seller.patch("/api/seller/products/1", {"price_cents": 100}, csrf=None), 403, "CSRF_FAILED", "missing CSRF")
        self.assert_ok(self.seller.get("/api/seller/overview"), "seller positive control")


    def test_02_overview_metrics_are_real_isolated_and_masked(self) -> None:
        before_reply = self.seller.get("/api/seller/overview")
        before = self.assert_ok(before_reply, "overview baseline")
        self.assertEqual(before["range_days"], 7)
        self.assertEqual(int(before["shop"]["id"]), self.shop_id)
        metric_by_key = {metric["key"]: metric for metric in before["metrics"]}
        self.assertIn("gmv", metric_by_key)
        labels = before["series"]["labels"]
        self.assertEqual(len(labels), 7)
        self.assertEqual(len(before["series"]["gmv_cents"]), 7)
        self.assertEqual(len(before["series"]["order_counts"]), 7)
        for metric in before["metrics"]:
            self.assertEqual(len(metric["spark"]), 7, metric)

        own_order = self.make_order(
            status="PAID",
            packages=[{"shop_id": self.shop_id, "status": "PENDING"}],
            items=[{"offer_id": self.self_offer_id, "quantity": 2}],
        )
        own_refunding = self.make_order(
            status="REFUNDING",
            last_state_before_refund="SHIPPED",
            packages=[{"shop_id": self.shop_id, "status": "SHIPPED"}],
            items=[{"offer_id": self.self_offer_id, "quantity": 1}],
        )
        self.add_refund(own_refunding["order_id"], shop_id=self.shop_id)

        brand_order = self.make_order(
            status="REFUNDING",
            last_state_before_refund="SHIPPED",
            packages=[{"shop_id": self.brand_shop_id, "status": "SHIPPED"}],
            items=[{"offer_id": self.brand_offer_id, "quantity": 4}],
        )
        brand_refund_id = self.add_refund(brand_order["order_id"], shop_id=self.brand_shop_id)

        own_low_token = "LOW" + uuid.uuid4().hex[:8].upper()
        low_own_offer = self.make_offer(
            shop_id=self.shop_id,
            product_id=self.self_product_id,
            stock=2,
            name=f"低库存验收 {own_low_token}",
        )
        low_brand_offer = self.make_offer(
            shop_id=self.brand_shop_id,
            product_id=self.brand_product_id,
            stock=0,
            name="他店低库存不应出现",
        )
        with closing(self.db()) as conn:
            conn.execute(
                "INSERT INTO browse_events(owner_key,product_id,event_type,query,created_at) VALUES(?,?,?,?,?)",
                (f"u:{self.buyer_id}", self.self_product_id, "view", "", dbutil.iso()),
            )
            conn.commit()

        after_reply = self.seller.get("/api/seller/overview")
        after = self.assert_ok(after_reply, "overview after real fixtures")
        after_metrics = {metric["key"]: metric for metric in after["metrics"]}
        self.assertEqual(after["range_days"], 7)
        self.assertEqual(len(after["series"]["labels"]), 7)
        self.assertEqual(after["series"]["labels"], labels)
        for metric in after["metrics"]:
            self.assertEqual(len(metric["spark"]), 7, metric)
        self.assertEqual(
            after_metrics["gmv"]["value_cents"],
            sum(after["series"]["gmv_cents"]),
            "GMV metric and real series must agree",
        )
        self.assertEqual(
            after_metrics["orders"]["value"],
            sum(after["series"]["order_counts"]),
            "order metric and real series must agree",
        )

        own_delta_gmv = int(own_order["total_cents"]) + int(own_refunding["total_cents"])
        before_gmv = int(metric_by_key["gmv"]["value_cents"])
        after_gmv = int(after_metrics["gmv"]["value_cents"])
        self.assertEqual(after_gmv - before_gmv, own_delta_gmv, "other shop GMV leaked or own GMV missing")
        before_orders = int(metric_by_key["orders"]["value"])
        after_orders = int(after_metrics["orders"]["value"])
        self.assertEqual(after_orders - before_orders, 2, "other shop orders leaked or own orders missing")
        self.assertEqual(
            sum(after["series"]["gmv_cents"]) - sum(before["series"]["gmv_cents"]),
            own_delta_gmv,
            "GMV series delta",
        )
        self.assertEqual(
            sum(after["series"]["order_counts"]) - sum(before["series"]["order_counts"]),
            2,
            "order series delta",
        )
        self.assertEqual(
            sum(after_metrics["views"]["spark"]) - sum(metric_by_key["views"]["spark"]),
            1,
            "browse_events must feed the real views metric",
        )
        self.assertEqual(after["todos"]["to_ship"] - before["todos"]["to_ship"], 1)
        self.assertEqual(after["todos"]["refunds"] - before["todos"]["refunds"], 1)

        own_low_ids = {int(item["offer_id"]) for item in after["low_stock"]}
        self.assertIn(low_own_offer, own_low_ids)
        self.assertNotIn(low_brand_offer, own_low_ids)
        expected_low_count = int(
            self.scalar(
                "SELECT COUNT(*) FROM offers WHERE shop_id=? AND status='active' AND stock<=5",
                (self.shop_id,),
            )
            or 0
        )
        self.assertEqual(after["todos"]["low_stock"], expected_low_count)
        self.assertEqual(after["low_stock_threshold"], 5)
        for item in after["low_stock"]:
            self.assertLessEqual(int(item["stock"]), 5)
            self.assertIn("sku_hint", item)

        recent_order_nos = {item["order_no"] for item in after["recent_packages"]}
        self.assertIn(own_order["order_no"], recent_order_nos)
        self.assertNotIn(brand_order["order_no"], recent_order_nos, "other shop recent package leaked")
        for item in after["recent_packages"]:
            self.assertEqual(item["buyer_display"], "林**")
            self.assertIn("package_id", item)
            self.assertIn("item_titles", item)
            self.assertIn("total_cents", item)
        self.assert_no_address_leak(after_reply, "overview")

    def test_03_overview_for_zero_data_shop_is_real_zero(self) -> None:
        reply = self.zero_seller.get("/api/seller/overview")
        data = self.assert_ok(reply, "zero overview")
        self.assertEqual(int(data["shop"]["id"]), self.zero_shop_id)
        self.assertEqual(data["range_days"], 7)
        self.assertEqual(len(data["series"]["labels"]), 7)
        self.assertEqual(data["series"]["gmv_cents"], [0] * 7)
        self.assertEqual(data["series"]["order_counts"], [0] * 7)
        for metric in data["metrics"]:
            self.assertEqual(len(metric["spark"]), 7)
            self.assertTrue(all(value == 0 for value in metric["spark"]), metric)
            if metric["key"] == "gmv":
                self.assertEqual(metric["value_cents"], 0)
                self.assertEqual(metric["previous_cents"], 0)
            else:
                self.assertEqual(metric["value"], 0)
                self.assertEqual(metric["previous"], 0)
            self.assertEqual(metric["delta_pct"], 0.0)
        self.assertEqual(data["todos"], {"to_ship": 0, "refunds": 0, "low_stock": 0})
        self.assertEqual(data["low_stock"], [])
        self.assertEqual(data["recent_packages"], [])

        products = self.assert_ok(self.zero_seller.get("/api/seller/products"), "zero products")
        orders = self.assert_ok(self.zero_seller.get("/api/seller/orders?status=ALL"), "zero orders")
        refunds = self.assert_ok(self.zero_seller.get("/api/seller/refunds"), "zero refunds")
        self.assertEqual(products["items"], [])
        self.assertEqual(products["total"], 0)
        self.assertEqual(orders["items"], [])
        self.assertEqual(orders["total"], 0)
        self.assertEqual(refunds["items"], [])
        self.assertEqual(refunds["total"], 0)

    def test_04_products_owner_scope_search_patch_and_ranges(self) -> None:
        listed = self.assert_ok(self.seller.get("/api/seller/products?page_size=24"), "seller products")
        self.assertGreaterEqual(listed["total"], 1)
        own_ids = self.offer_ids_from_list(listed)
        self.assertNotIn(self.brand_offer_id, own_ids, "other shop offer leaked into products")
        foreign_count = int(
            self.scalar(
                "SELECT COUNT(*) FROM offers WHERE shop_id!=? AND id IN (%s)"
                % ",".join("?" for _ in own_ids),
                (self.shop_id, *own_ids),
            )
            or 0
        )
        self.assertEqual(foreign_count, 0)

        token = "QX" + uuid.uuid4().hex[:8].upper()
        patch_offer_id = self.make_offer(
            shop_id=self.shop_id,
            product_id=self.self_product_id,
            stock=9,
            name=f"调价验收 {token}",
        )
        found = self.assert_ok(self.seller.get(f"/api/seller/products?q={token}"), "search own offer")
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["items"][0]["offer_id"], patch_offer_id)
        item = found["items"][0]
        self.assertEqual(item["stock"], 9)
        self.assertTrue(item["sku_hint"])
        self.assertGreaterEqual(len(item["skus"]), 1)
        self.assertIn("min_price_cents", item)
        self.assertIn("max_price_cents", item)

        valid_price = int(item["max_price_cents"]) - 100
        updated = self.assert_ok(
            self.seller.patch(
                f"/api/seller/products/{patch_offer_id}",
                {"price_cents": valid_price, "stock": 17, "status": "paused"},
            ),
            "valid offer patch",
        )
        self.assertEqual(updated["offer_id"], patch_offer_id)
        self.assertEqual(updated["price_cents"], valid_price)
        self.assertEqual(updated["stock"], 17)
        self.assertEqual(updated["status"], "paused")
        db_offer = self.fetch_one("SELECT price_cents,stock,status FROM offers WHERE id=?", (patch_offer_id,))
        self.assertEqual(int(db_offer["price_cents"]), valid_price)
        self.assertEqual(int(db_offer["stock"]), 17)
        self.assertEqual(db_offer["status"], "paused")

        for payload in (
            {"price_cents": 0},
            {"price_cents": int(item["max_price_cents"]) + 1},
            {"stock": -1},
            {"stock": 1_000_001},
            {"status": "hidden"},
            {},
        ):
            with self.subTest(payload=payload):
                reply = self.seller.patch(f"/api/seller/products/{patch_offer_id}", payload)
                self.assert_error(reply, 400, "VALIDATION_ERROR", f"invalid offer patch {payload}")
        group_offer = self.fetch_one(
            "SELECT id,group_price_cents FROM offers WHERE shop_id=? AND group_size IS NOT NULL LIMIT 1",
            (self.shop_id,),
        )
        if group_offer is not None:
            reply = self.seller.patch(
                f"/api/seller/products/{int(group_offer['id'])}",
                {"price_cents": int(group_offer["group_price_cents"])},
            )
            self.assert_error(reply, 400, "VALIDATION_ERROR", "price at/below group floor")

        cross_reply = self.seller.patch(
            f"/api/seller/products/{self.brand_offer_id}",
            {"price_cents": 100},
        )
        self.assert_error(cross_reply, 404, "NOT_FOUND", "other shop offer patch")
        self.assert_error(
            self.seller.patch("/api/seller/products/999999999", {"stock": 1}),
            404,
            "NOT_FOUND",
            "unknown offer patch",
        )
        before_csrf = self.scalar("SELECT price_cents FROM offers WHERE id=?", (patch_offer_id,))
        self.assert_error(
            self.seller.patch(f"/api/seller/products/{patch_offer_id}", {"stock": 3}, csrf=None),
            403,
            "CSRF_FAILED",
            "offer patch without CSRF",
        )
        self.assertEqual(int(self.scalar("SELECT price_cents FROM offers WHERE id=?", (patch_offer_id,))), int(before_csrf))

        page_one = self.assert_ok(self.seller.get("/api/seller/products?page=1&page_size=2"), "products page 1")
        page_two = self.assert_ok(self.seller.get("/api/seller/products?page=2&page_size=2"), "products page 2")
        self.assertEqual(len(page_one["items"]), 1 if page_one["total"] == 1 else 2)
        self.assertEqual(page_one["page"], 1)
        self.assertEqual(page_two["page"], 2)
        ids_one = self.offer_ids_from_list(page_one)
        ids_two = self.offer_ids_from_list(page_two)
        self.assertFalse(ids_one & ids_two, "pagination returned duplicate offers")
        self.assertGreaterEqual(page_one["total"], 3)
        self.assert_error(
            self.seller.get("/api/seller/products?page_size=25"),
            400,
            "VALIDATION_ERROR",
            "page_size above maximum",
        )

    def test_05_order_lists_shipping_and_negative_controls(self) -> None:
        seed_pending = self.fetch_one(
            """SELECT p.id AS package_id, p.status AS package_status, o.id AS order_id, o.order_no,
                      o.status AS order_status
               FROM order_packages p JOIN orders o ON o.id=p.order_id
               WHERE p.shop_id=? AND p.status='PENDING' AND o.status='PAID'
               ORDER BY o.created_at, p.id LIMIT 1""",
            (self.shop_id,),
        )
        self.assertIsNotNone(seed_pending, "seed must contain a PAID order package for the demo seller")
        seed_package_id = int(seed_pending["package_id"])
        seed_order_id = int(seed_pending["order_id"])
        package_count = int(
            self.scalar("SELECT COUNT(*) FROM order_packages WHERE order_id=?", (seed_order_id,)) or 0
        )

        own_paid_extra = self.make_order(
            status="PAID",
            packages=[{"shop_id": self.shop_id, "status": "PENDING"}],
            items=[{"offer_id": self.self_offer_id, "quantity": 1}],
        )
        cross_shop_order = self.make_order(
            status="PAID",
            packages=[{"shop_id": self.brand_shop_id, "status": "PENDING"}],
            items=[{"offer_id": self.brand_offer_id, "quantity": 1}],
        )
        cross_package_id = cross_shop_order["package_ids"][0]
        self.make_order(
            status="PAID",
            packages=[{"shop_id": self.shop_id, "status": "PENDING"}],
            items=[{"offer_id": self.self_offer_id, "quantity": 1}],
        )
        group_order = self.make_order(
            status="GROUP_FORMING",
            packages=[{"shop_id": self.shop_id, "status": "PENDING"}],
            items=[{"offer_id": self.self_offer_id, "quantity": 1}],
        )
        unpaid_order = self.make_order(
            status="PENDING_PAYMENT",
            packages=[{"shop_id": self.shop_id, "status": "PENDING"}],
            items=[{"offer_id": self.self_offer_id, "quantity": 1}],
        )

        for payload in (
            {"carrier": "不存在的快递", "tracking_no": "ABC123"},
            {"carrier": CARRIERS[0], "tracking_no": "ABC12"},
            {"carrier": CARRIERS[0], "tracking_no": "A" * 25},
            {"carrier": CARRIERS[0], "tracking_no": "ABC-123"},
            {"carrier": CARRIERS[0], "tracking_no": ""},
            {"carrier": CARRIERS[0]},
        ):
            with self.subTest(payload=payload):
                reply = self.seller.post(f"/api/seller/orders/{seed_package_id}/ship", payload)
                self.assert_error(reply, 400, "VALIDATION_ERROR", f"invalid shipment {payload}")
        self.assertEqual(
            self.scalar("SELECT status FROM order_packages WHERE id=?", (seed_package_id,)),
            "PENDING",
        )

        cross_ship = self.seller.post(
            f"/api/seller/orders/{cross_package_id}/ship",
            {"carrier": CARRIERS[0], "tracking_no": "CROSS123"},
        )
        self.assert_error(cross_ship, 404, "NOT_FOUND", "cross-shop shipment")
        group_ship = self.seller.post(
            f"/api/seller/orders/{group_order['package_ids'][0]}/ship",
            {"carrier": CARRIERS[1], "tracking_no": "GROUP123"},
        )
        self.assert_error(group_ship, 409, "ORDER_STATE", "group-forming shipment")
        unpaid_ship = self.seller.post(
            f"/api/seller/orders/{unpaid_order['package_ids'][0]}/ship",
            {"carrier": CARRIERS[1], "tracking_no": "UNPAID12"},
        )
        self.assert_error(unpaid_ship, 409, "ORDER_STATE", "unpaid shipment")
        self.assertEqual(self.scalar("SELECT status FROM orders WHERE id=?", (group_order["order_id"],)), "GROUP_FORMING")
        self.assertEqual(self.scalar("SELECT status FROM orders WHERE id=?", (unpaid_order["order_id"],)), "PENDING_PAYMENT")

        pending_page_1 = self.assert_ok(
            self.seller.get("/api/seller/orders?status=PENDING&page=1&page_size=2"),
            "pending orders page 1",
        )
        pending_page_2 = self.assert_ok(
            self.seller.get("/api/seller/orders?status=PENDING&page=2&page_size=2"),
            "pending orders page 2",
        )
        self.assertGreaterEqual(pending_page_1["total"], 2)
        self.assertGreaterEqual(pending_page_1["pages"], 2)
        self.assertEqual(len(pending_page_1["items"]), 2)
        ids_1 = {item["package_id"] for item in pending_page_1["items"]}
        ids_2 = {item["package_id"] for item in pending_page_2["items"]}
        self.assertFalse(ids_1 & ids_2)
        for item in pending_page_1["items"] + pending_page_2["items"]:
            self.assertEqual(item["package_status"], "PENDING")
            self.assertEqual(item["order_status"], "PAID")
            self.assertTrue(item["can_ship"])
            self.assertEqual(item["buyer_display"], "林**")
            self.assertNotIn(cross_package_id, ids_1 | ids_2)
        pending_reply = self.seller.get("/api/seller/orders?status=ALL&page_size=24")
        pending_all = self.assert_ok(pending_reply, "all seller orders")
        self.assertIn("carriers", pending_all)
        self.assertEqual(pending_all["carriers"], CARRIERS)
        all_package_ids = {item["package_id"] for item in pending_all["items"]}
        self.assertNotIn(cross_package_id, all_package_ids)
        self.assertIn(seed_package_id, all_package_ids)
        self.assert_no_address_leak(pending_reply, "seller orders")
        foreign_package_count = int(
            self.scalar(
                "SELECT COUNT(*) FROM order_packages WHERE shop_id!=? AND id IN (%s)"
                % ",".join("?" for _ in all_package_ids),
                (self.shop_id, *all_package_ids),
            )
            or 0
        )
        self.assertEqual(foreign_package_count, 0)

        shipped = self.seller.post(
            f"/api/seller/orders/{seed_package_id}/ship",
            {"carrier": CARRIERS[0], "tracking_no": "QB20261012001"},
        )
        shipped_data = self.assert_ok(shipped, "seed PAID package ships")
        self.assertEqual(shipped_data["package_id"], seed_package_id)
        self.assertEqual(shipped_data["status"], "SHIPPED")
        self.assertEqual(shipped_data["carrier"], CARRIERS[0])
        self.assertEqual(shipped_data["order_status"], "SHIPPED" if package_count == 1 else "PAID")
        pkg_row = self.fetch_one("SELECT * FROM order_packages WHERE id=?", (seed_package_id,))
        self.assertEqual(pkg_row["status"], "SHIPPED")
        self.assertEqual(pkg_row["carrier"], CARRIERS[0])
        self.assertEqual(pkg_row["tracking_no"], "QB20261012001")
        if package_count == 1:
            self.assertEqual(self.scalar("SELECT status FROM orders WHERE id=?", (seed_order_id,)), "SHIPPED")
        event_count = int(
            self.scalar(
                "SELECT COUNT(*) FROM order_events WHERE order_id=? AND label='商家已发货'",
                (seed_order_id,),
            )
            or 0
        )
        self.assertGreaterEqual(event_count, 1)

        duplicate = self.seller.post(
            f"/api/seller/orders/{seed_package_id}/ship",
            {"carrier": CARRIERS[1], "tracking_no": "DUPLICATE1"},
        )
        self.assert_error(duplicate, 409, "ORDER_STATE", "duplicate shipment")

        multi_offer = self.make_offer(shop_id=self.shop_id, product_id=self.self_product_id, stock=30)
        multi_order = self.make_order(
            status="PAID",
            packages=[
                {"shop_id": self.shop_id, "status": "PENDING"},
                {"shop_id": self.shop_id, "status": "PENDING"},
            ],
            items=[
                {"package_index": 0, "offer_id": multi_offer, "quantity": 1},
                {"package_index": 1, "offer_id": multi_offer, "quantity": 1},
            ],
        )
        first_ship = self.assert_ok(
            self.seller.post(
                f"/api/seller/orders/{multi_order['package_ids'][0]}/ship",
                {"carrier": CARRIERS[2], "tracking_no": "MULTI00001"},
            ),
            "first multi-package shipment",
        )
        self.assertFalse(first_ship["all_shipped"])
        self.assertEqual(first_ship["order_status"], "PAID", "order must wait for all packages")
        self.assertEqual(self.scalar("SELECT status FROM orders WHERE id=?", (multi_order["order_id"],)), "PAID")
        second_ship = self.assert_ok(
            self.seller.post(
                f"/api/seller/orders/{multi_order['package_ids'][1]}/ship",
                {"carrier": CARRIERS[1], "tracking_no": "MULTI00002"},
            ),
            "second multi-package shipment",
        )
        self.assertTrue(second_ship["all_shipped"])
        self.assertEqual(second_ship["order_status"], "SHIPPED")
        self.assertEqual(self.scalar("SELECT status FROM orders WHERE id=?", (multi_order["order_id"],)), "SHIPPED")
        self.assertGreaterEqual(
            int(
                self.scalar(
                    "SELECT COUNT(*) FROM order_events WHERE order_id=? AND label='订单已全部发货'",
                    (multi_order["order_id"],),
                )
                or 0
            ),
            1,
        )

        shipped_filter = self.assert_ok(self.seller.get("/api/seller/orders?status=SHIPPED"), "shipped filter")
        for item in shipped_filter["items"]:
            self.assertIn(item["package_status"], {"SHIPPED", "DELIVERED"})
        shipped_nos = {item["order_no"] for item in shipped_filter["items"]}
        self.assertIn(seed_pending["order_no"], shipped_nos)
        self.assert_error(
            self.seller.get("/api/seller/orders?status=BOGUS"),
            400,
            "VALIDATION_ERROR",
            "invalid order filter",
        )
        self.assert_ok(self.seller.get("/api/seller/orders?status=ALL"), "final orders read")

    def offer_ids_from_list(self, payload: dict[str, Any]) -> set[int]:
        return {int(item["offer_id"]) for item in payload.get("items", [])}

    def test_06_refunds_are_scoped_and_decide_real_inventory_and_coupon(self) -> None:
        approve_case = self.make_refund_case(stock=20, previous_status="SHIPPED", quantity=2)
        approve_coupon = self.attach_used_coupon(approve_case["order"]["order_id"], expired=False)
        approve_case["coupon"] = approve_coupon

        reject_offer = self.make_offer(shop_id=self.shop_id, product_id=self.self_product_id, stock=15)
        reject_order = self.make_order(
            status="REFUNDING",
            last_state_before_refund="COMPLETED",
            packages=[{"shop_id": self.shop_id, "status": "SHIPPED"}],
            items=[{"offer_id": reject_offer, "quantity": 1}],
        )
        reject_refund = self.add_refund(reject_order["order_id"], shop_id=self.shop_id)

        expired_case = self.make_refund_case(stock=8, previous_status="SHIPPED", quantity=1)
        expired_coupon = self.attach_used_coupon(expired_case["order"]["order_id"], expired=True)
        expired_case["coupon"] = expired_coupon

        inconsistent_order = self.make_order(
            status="PAID",
            packages=[{"shop_id": self.shop_id, "status": "PENDING"}],
            items=[{"offer_id": self.self_offer_id, "quantity": 1}],
        )
        inconsistent_refund = self.add_refund(inconsistent_order["order_id"], shop_id=self.shop_id)

        brand_order = self.make_order(
            status="REFUNDING",
            last_state_before_refund="SHIPPED",
            packages=[{"shop_id": self.brand_shop_id, "status": "SHIPPED"}],
            items=[{"offer_id": self.brand_offer_id, "quantity": 1}],
        )
        brand_refund = self.add_refund(brand_order["order_id"], shop_id=self.brand_shop_id)

        listed_reply = self.seller.get("/api/seller/refunds?status=ALL&page_size=24")
        listed = self.assert_ok(listed_reply, "seller refunds")
        listed_ids = {int(item["id"]) for item in listed["items"]}
        self.assertIn(approve_case["refund_id"], listed_ids)
        self.assertIn(reject_refund, listed_ids)
        self.assertIn(expired_case["refund_id"], listed_ids)
        self.assertIn(inconsistent_refund, listed_ids)
        self.assertNotIn(brand_refund, listed_ids, "other shop refund leaked")
        for item in listed["items"]:
            self.assertEqual(item["buyer_display"], "林**")
            self.assertIn("items", item)
            self.assertIn("can_decide", item)
        self.assert_no_address_leak(listed_reply, "seller refunds")

        requested_page_1 = self.assert_ok(
            self.seller.get("/api/seller/refunds?status=REQUESTED&page=1&page_size=1"),
            "requested refunds page 1",
        )
        requested_page_2 = self.assert_ok(
            self.seller.get("/api/seller/refunds?status=REQUESTED&page=2&page_size=1"),
            "requested refunds page 2",
        )
        self.assertGreaterEqual(requested_page_1["total"], 4)
        self.assertGreaterEqual(requested_page_1["pages"], 4)
        self.assertEqual(len(requested_page_1["items"]), 1)
        self.assertEqual(len(requested_page_2["items"]), 1)
        self.assertNotEqual(requested_page_1["items"][0]["id"], requested_page_2["items"][0]["id"])
        for item in requested_page_1["items"] + requested_page_2["items"]:
            self.assertEqual(item["status"], "REQUESTED")

        self.assert_error(
            self.buyer.post(
                f"/api/seller/refunds/{approve_case['refund_id']}/decision",
                {"action": "approve", "note": "buyer cannot"},
            ),
            403,
            "FORBIDDEN",
            "buyer refund decision",
        )
        self.assert_error(
            self.seller.post(
                f"/api/seller/refunds/{approve_case['refund_id']}/decision",
                {"action": "maybe", "note": "invalid"},
            ),
            400,
            "VALIDATION_ERROR",
            "invalid refund action",
        )
        self.assert_error(
            self.seller.post(
                f"/api/seller/refunds/{brand_refund}/decision",
                {"action": "approve", "note": "cross shop"},
            ),
            404,
            "NOT_FOUND",
            "cross-shop refund decision",
        )
        stock_before_inconsistent = int(self.scalar("SELECT stock FROM offers WHERE id=?", (self.self_offer_id,)))
        self.assert_error(
            self.seller.post(
                f"/api/seller/refunds/{inconsistent_refund}/decision",
                {"action": "approve", "note": "order not REFUNDING"},
            ),
            409,
            "ORDER_STATE",
            "refund on non-REFUNDING order",
        )
        self.assertEqual(
            self.scalar("SELECT status FROM refunds WHERE id=?", (inconsistent_refund,)),
            "REQUESTED",
        )
        self.assertEqual(int(self.scalar("SELECT stock FROM offers WHERE id=?", (self.self_offer_id,))), stock_before_inconsistent)

        coupon_before = int(self.scalar("SELECT status='USED' FROM user_coupons WHERE id=?", (approve_coupon["coupon_id"],)))
        stock_before = int(self.scalar("SELECT stock FROM offers WHERE id=?", (approve_case["offer_id"],)))
        approve_reply = self.seller.post(
            f"/api/seller/refunds/{approve_case['refund_id']}/decision",
            {"action": "approve", "note": "验收同意退款"},
        )
        approved = self.assert_ok(approve_reply, "approve refund")
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(approved["order_status"], "REFUNDED")
        self.assertEqual(approved["inventory_restored_units"], 2)
        self.assertEqual(approved["coupon_restored_status"], "AVAILABLE")
        self.assert_no_address_leak(approve_reply, "approve refund response")
        refund_row = self.fetch_one("SELECT * FROM refunds WHERE id=?", (approve_case["refund_id"],))
        self.assertEqual(refund_row["status"], "APPROVED")
        self.assertEqual(refund_row["decision_note"], "验收同意退款")
        order_row = self.fetch_one("SELECT * FROM orders WHERE id=?", (approve_case["order"]["order_id"],))
        self.assertEqual(order_row["status"], "REFUNDED")
        self.assertEqual(order_row["last_state_before_refund"], "SHIPPED")
        self.assertIsNotNone(order_row["refunded_at"])
        self.assertEqual(int(self.scalar("SELECT stock FROM offers WHERE id=?", (approve_case["offer_id"],))), stock_before + 2)
        coupon_row = self.fetch_one("SELECT * FROM user_coupons WHERE id=?", (approve_coupon["coupon_id"],))
        self.assertEqual(coupon_row["status"], "AVAILABLE")
        self.assertIsNone(coupon_row["used_at"])
        self.assertIsNone(coupon_row["order_id"])
        self.assertEqual(coupon_before, 1)
        self.assertGreaterEqual(
            int(self.scalar("SELECT COUNT(*) FROM order_events WHERE order_id=? AND label='商家同意退款'", (approve_case["order"]["order_id"],)) or 0),
            1,
        )
        duplicate_approve = self.seller.post(
            f"/api/seller/refunds/{approve_case['refund_id']}/decision",
            {"action": "approve", "note": "again"},
        )
        self.assert_error(duplicate_approve, 409, "ORDER_STATE", "duplicate refund decision")

        stock_before_reject = int(self.scalar("SELECT stock FROM offers WHERE id=?", (reject_offer,)))
        reject_reply = self.seller.post(
            f"/api/seller/refunds/{reject_refund}/decision",
            {"action": "reject", "note": "验收驳回退款"},
        )
        rejected = self.assert_ok(reject_reply, "reject refund")
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["order_status"], "COMPLETED")
        self.assertEqual(int(self.scalar("SELECT stock FROM offers WHERE id=?", (reject_offer,))), stock_before_reject)
        reject_order_row = self.fetch_one("SELECT * FROM orders WHERE id=?", (reject_order["order_id"],))
        self.assertEqual(reject_order_row["status"], "COMPLETED")
        self.assertIsNone(reject_order_row["last_state_before_refund"])
        self.assertIsNone(reject_order_row["refunded_at"])
        self.assertGreaterEqual(
            int(self.scalar("SELECT COUNT(*) FROM order_events WHERE order_id=? AND label='商家驳回退款'", (reject_order["order_id"],)) or 0),
            1,
        )
        duplicate_reject = self.seller.post(
            f"/api/seller/refunds/{reject_refund}/decision",
            {"action": "reject", "note": "again"},
        )
        self.assert_error(duplicate_reject, 409, "ORDER_STATE", "duplicate reject decision")

        stock_before_expired = int(self.scalar("SELECT stock FROM offers WHERE id=?", (expired_case["offer_id"],)))
        expired_reply = self.seller.post(
            f"/api/seller/refunds/{expired_case['refund_id']}/decision",
            {"action": "approve", "note": "验收过期券"},
        )
        expired_approved = self.assert_ok(expired_reply, "approve expired coupon refund")
        self.assertEqual(expired_approved["coupon_restored_status"], "EXPIRED")
        self.assertEqual(int(self.scalar("SELECT stock FROM offers WHERE id=?", (expired_case["offer_id"],))), stock_before_expired + 1)
        expired_coupon_row = self.fetch_one("SELECT * FROM user_coupons WHERE id=?", (expired_coupon["coupon_id"],))
        self.assertEqual(expired_coupon_row["status"], "EXPIRED")

        approved_filter = self.assert_ok(self.seller.get("/api/seller/refunds?status=APPROVED&page_size=24"), "approved refunds")
        approved_ids = {int(item["id"]) for item in approved_filter["items"]}
        self.assertIn(approve_case["refund_id"], approved_ids)
        self.assertIn(expired_case["refund_id"], approved_ids)
        self.assertNotIn(brand_refund, approved_ids)
        self.assertNotIn(reject_refund, approved_ids)
        rejected_filter = self.assert_ok(self.seller.get("/api/seller/refunds?status=REJECTED"), "rejected refunds")
        self.assertIn(reject_refund, {int(item["id"]) for item in rejected_filter["items"]})
        self.assert_error(
            self.seller.get("/api/seller/refunds?status=BOGUS"),
            400,
            "VALIDATION_ERROR",
            "invalid refund filter",
        )
        self.assertEqual(CARRIERS, ["青鸟速运", "江海快递", "晨风同城"])


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(SellerApiTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence = getattr(SellerApiTests, "tmp_dir", None)
    verdict = "PASS" if result.wasSuccessful() else "FAIL"
    print(
        f"SELLER_API_VERDICT {verdict} run={result.testsRun} failures={len(result.failures)} "
        f"errors={len(result.errors)} skipped={len(result.skipped)} evidence={evidence}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
