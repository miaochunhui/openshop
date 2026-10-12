#!/usr/bin/env python3
"""Real-backend HTTP acceptance checks for OpenShop.

Never imports product route code, never mocks an HTTP response. Every request
goes to a freshly started proxy-disabled local server with an isolated SQLite
database. Missing implementations are recorded as FAIL, not skipped.
"""
from __future__ import annotations

import concurrent.futures
import json
import secrets
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from http_client import ApiResponse, QAClient
from qa_harness import (
    CheckFailure,
    CheckRunner,
    eq,
    in_set,
    parse_error,
    parse_success,
    require,
    require_no_secret_fields,
)
from qa_server import runtime_path
from security_oracles import evaluate_csrf_rejection


@dataclass
class OfferChoice:
    product_slug: str
    product_title: str
    offer_id: int
    sku_id: int
    sku_name: str
    unit_price_cents: int
    shop_id: int
    shop_slug: str
    shop_kind: str
    stock: int
    kind: str
    group_size: int | None = None
    group_price_cents: int | None = None


class ApiSuite:
    """All API checks in one ordered, stateful acceptance scenario."""

    def __init__(self, *, base_url: str, data_dir: str | Path, runner: CheckRunner) -> None:
        self.base_url = base_url
        self.data_dir = Path(data_dir).resolve()
        self.runner = runner
        self.worktree = str(Path(__file__).resolve().parents[2])
        self.run_id = secrets.token_hex(4)
        # Populated by checks; None means that a prerequisite check failed.
        self.catalog_client: QAClient | None = None
        self.a: QAClient | None = None
        self.a_user: dict | None = None
        self.b: QAClient | None = None
        self.b_user: dict | None = None
        self.seller2: QAClient | None = None
        self.seller2_user: dict | None = None
        self.seller2_shop: dict | None = None
        self.seller: QAClient | None = None
        self.seller_user: dict | None = None
        self.categories: list | None = None
        self.home: dict | None = None
        self.product_cards: list | None = None
        self.product_details: dict[str, dict] = {}
        self.main_detail: dict | None = None
        self.main_offer: OfferChoice | None = None
        self.group_offer: OfferChoice | None = None
        self.a_address: dict | None = None
        self.b_address: dict | None = None
        self.a_cart_item_id: int | None = None
        self.a_order_no: str | None = None
        self.a_order: dict | None = None
        self.buyer_package_id: int | None = None
        self.tracking_no: str | None = None
        self.a_review_title: str | None = None
        self.main_coupon: dict | None = None
        self.main_quote: dict | None = None
        self.group_code: str | None = None
        self.group_required_size: int | None = None

    # ------------------------------------------------------------------ utils
    def _client(self, label: str) -> QAClient:
        return QAClient(self.base_url, label=label)

    def _new_guest(self, label: str) -> tuple[QAClient, dict]:
        client = self._client(label)
        data = parse_success(client.begin_session(), where=f"{label} GET /api/session")
        require(isinstance(data, dict), f"{label}: session data must be an object")
        require(data.get("user") is None, f"{label}: fresh session must be guest, got user")
        require(data.get("is_guest") is True, f"{label}: is_guest must be true for fresh session")
        require(isinstance(data.get("csrf"), str) and len(data["csrf"]) >= 32, f"{label}: session csrf missing/short")
        require(isinstance(data.get("cart_count"), int), f"{label}: session cart_count must be int")
        require(isinstance(data.get("favorite_count"), int), f"{label}: session favorite_count must be int")
        require_no_secret_fields(data, f"{label} session")
        return client, data

    def _need(self, attr: str, source: str) -> Any:
        value = getattr(self, attr, None)
        if value is None:
            raise CheckFailure(f"BLOCKED fixture {attr} unavailable (prerequisite {source} did not pass)")
        return value

    def _key(self, label: str) -> str:
        key = f"qa{self.run_id}{label}{secrets.token_hex(4)}"
        require(16 <= len(key) <= 64, f"internal QA idempotency key length bug: {len(key)}")
        return key

    def _register_buyer(self, label: str, display_name: str) -> tuple[QAClient, dict]:
        client, _guest = self._new_guest(label)
        email = f"qa-{label}-{self.run_id}@openshop.demo"
        password = secrets.token_urlsafe(14)
        old_csrf = client.csrf
        data = parse_success(
            client.post(
                "/api/auth/register",
                {"display_name": display_name, "email": email, "password": password},
            ),
            where=f"{label} POST /api/auth/register",
        )
        require(isinstance(data, dict), f"{label}: register data missing")
        user = data.get("user")
        require(isinstance(user, dict), f"{label}: register response missing user")
        eq(user.get("role"), "buyer", f"{label}: registered user role")
        eq(str(user.get("email", "")).lower(), email.lower(), f"{label}: registered email")
        require(isinstance(data.get("csrf"), str) and len(data["csrf"]) >= 32, f"{label}: session csrf after register")
        require(bool(client.csrf), f"{label}: client did not receive register csrf")
        require(client.cookie_value("openshop_session"), f"{label}: register did not establish session cookie")
        require_no_secret_fields(data, f"{label} register")
        session = parse_success(client.begin_session(), where=f"{label} refresh session")
        require((session.get("user") or {}).get("id") == user.get("id"), f"{label}: session user does not match registration")
        return client, user

    def _promote_to_seller2(self, user: dict) -> tuple[dict, dict]:
        db_path = self._db_path()
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            shop = conn.execute(
                "SELECT id, slug, name FROM shops WHERE slug != 'openshop-self' ORDER BY id LIMIT 1"
            ).fetchone()
            require(shop is not None, "QA seller2 fixture: no non-self shop exists in isolated DB")
            conn.execute("UPDATE users SET role='seller', shop_id=? WHERE id=?", (int(shop[0]), int(user["id"])))
            conn.commit()
        shop_info = {"id": int(shop[0]), "slug": str(shop[1]), "name": str(shop[2])}
        return user, shop_info

    def _db_path(self) -> Path:
        path = self.data_dir / "openshop.sqlite3"
        require(path.is_file(), f"isolated QA DB not found at {path}")
        return path

    def _db_set_stock(self, offer_id: int, stock: int) -> None:
        with sqlite3.connect(str(self._db_path()), timeout=10.0) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            cur = conn.execute("UPDATE offers SET stock=? WHERE id=?", (int(stock), int(offer_id)))
            require(cur.rowcount == 1, f"QA fixture: offer {offer_id} not found while setting stock")
            conn.commit()

    def _detail(self, slug: str, client: QAClient | None = None) -> dict:
        c = client or self.catalog_client
        require(c is not None, "catalog client not initialized")
        data = parse_success(c.get(f"/api/products/{urllib.parse.quote(slug)}"), where=f"GET product {slug}")
        require(isinstance(data, dict), f"product {slug}: detail must be an object")
        return data

    def _cart(self, client: QAClient, where: str = "") -> dict:
        data = parse_success(client.get("/api/cart"), where=where or "GET /api/cart")
        require(isinstance(data, dict) and isinstance(data.get("items"), list), f"{where}: CartResponse.items missing")
        return data

    def _coupons(self, client: QAClient, where: str = "") -> dict:
        data = parse_success(client.get("/api/coupons"), where=where or "GET /api/coupons")
        require(isinstance(data, dict), "GET /api/coupons: data must be an object")
        require(isinstance(data.get("available"), list), "GET /api/coupons: available must be a list")
        require(isinstance(data.get("mine"), list), "GET /api/coupons: mine must be a list")
        return data

    def _normalized_coupons(self, payload: dict) -> list[dict]:
        defs = {
            item.get("code"): item
            for item in payload.get("available", [])
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        normalized: list[dict] = []
        for raw in payload.get("mine", []):
            if not isinstance(raw, dict):
                continue
            code = raw.get("code")
            definition = defs.get(code) or {}
            normalized.append(
                {
                    "code": code,
                    "status": raw.get("status"),
                    "threshold_cents": int(raw.get("threshold_cents", definition.get("threshold_cents", 0)) or 0),
                    "discount_cents": int(raw.get("discount_cents", definition.get("discount_cents", 0)) or 0),
                    "title": raw.get("title") or definition.get("title") or code,
                    "scope": raw.get("scope") or definition.get("scope") or "all",
                    "expires_at": raw.get("expires_at"),
                    "raw": raw,
                }
            )
        return normalized

    def _available_coupons(self, client: QAClient) -> tuple[dict, list[dict]]:
        payload = self._coupons(client)
        items = [c for c in self._normalized_coupons(payload) if c["status"] == "AVAILABLE" and c["code"]]
        return payload, items

    def _pick_coupon(self, available: list[dict], subtotal_cents: int, *, exclude: Iterable[str] = ()) -> dict:
        excluded = set(exclude)
        # The main journey selects an unrestricted coupon. Category-scoped
        # coupons are deliberately not assumed applicable to whichever product
        # the catalog fixture chose; the backend still rejects/scopes those and
        # API-037 exercises invalid-coupon recovery separately.
        candidates = [
            c for c in available
            if c["code"] not in excluded and c["threshold_cents"] <= subtotal_cents and c["discount_cents"] > 0
            and str(c.get("scope", "all")) == "all"
        ]
        if not candidates:
            codes = [(c["code"], c["status"], c["scope"], c["threshold_cents"], c["discount_cents"]) for c in available]
            raise CheckFailure(
                f"no AVAILABLE unrestricted coupon fits subtotal={subtotal_cents}; available={codes!r} "
                "(journey requires a real usable unrestricted coupon from seed/implementation)"
            )
        return sorted(candidates, key=lambda c: (c["discount_cents"], str(c["code"])), reverse=True)[0]

    def _add_cart(
        self,
        client: QAClient,
        offer: OfferChoice,
        *,
        buy_mode: str = "standard",
        group_code: str | None = None,
        quantity: int = 1,
        extra: dict | None = None,
        csrf: Any = ...,
    ) -> ApiResponse:
        body = {
            "offer_id": offer.offer_id,
            "sku_id": offer.sku_id,
            "quantity": quantity,
            "buy_mode": buy_mode,
            "group_code": group_code,
        }
        if extra:
            body.update(extra)
        kwargs = {}
        if csrf is not ...:
            kwargs["csrf"] = csrf
        return client.post("/api/cart/items", body, **kwargs)

    def _cart_has_offer(self, cart: dict, offer_id: int) -> list[dict]:
        return [i for i in cart.get("items", []) if isinstance(i, dict) and i.get("offer_id") == offer_id]

    def _quote(self, client: QAClient, *, coupon_code: str | None = None, shipping: str = "standard") -> dict:
        body: dict[str, Any] = {"shipping_method": shipping}
        if coupon_code:
            body["coupon_code"] = coupon_code
        data = parse_success(client.post("/api/checkout/quote", body), where=f"POST /api/checkout/quote shipping={shipping}")
        require(isinstance(data, dict), "quote data must be an object")
        return data

    def _create_order(self, client: QAClient, body: dict) -> tuple[str, dict]:
        data = parse_success(client.post("/api/orders", body), where="POST /api/orders")
        require(isinstance(data, dict), "order create data must be an object")
        order = data.get("order") if isinstance(data.get("order"), dict) else data
        order_no = order.get("order_no")
        require(isinstance(order_no, str) and order_no, "order create response contains no order_no")
        detail = parse_success(client.get(f"/api/orders/{urllib.parse.quote(order_no)}"), where=f"GET order {order_no}")
        require(isinstance(detail, dict), "order detail must be an object")
        eq(detail.get("order_no"), order_no, "order detail order_no")
        return order_no, detail

    def _order(self, client: QAClient, order_no: str) -> dict:
        data = parse_success(client.get(f"/api/orders/{urllib.parse.quote(order_no)}"), where=f"GET order {order_no}")
        require(isinstance(data, dict), "order detail must be an object")
        eq(data.get("order_no"), order_no, "order detail order_no")
        return data

    def _order_items(self, order: dict) -> list[dict]:
        items: list[dict] = []
        for pkg in order.get("packages", []) or []:
            if not isinstance(pkg, dict):
                continue
            for item in pkg.get("items", []) or []:
                if isinstance(item, dict):
                    entry = dict(item)
                    entry["_package_id"] = pkg.get("id")
                    items.append(entry)
        return items

    def _group_view_anonymous(self, code: str) -> dict:
        guest = self._client("group-public")
        data = parse_success(guest.get(f"/api/groups/{urllib.parse.quote(code)}"), where=f"GET group {code}")
        require(isinstance(data, dict), "GroupView must be an object")
        return data

    def _clear_cart(self, client: QAClient) -> None:
        cart = self._cart(client, "pre-clear cart")
        for item in list(cart.get("items", [])):
            item_id = item.get("id")
            require(isinstance(item_id, int), "cart item missing integer id")
            parse_success(client.delete(f"/api/cart/items/{item_id}"), where=f"DELETE /api/cart/items/{item_id}")
        after = self._cart(client, "post-clear cart")
        eq(after.get("items"), [], "cart must be empty after QA cleanup")

    def _make_address(self, client: QAClient, *, label: str, phone: str) -> dict:
        body = {
            "label": label,
            "receiver": "验收收货人",
            "phone": phone,
            "province": "上海市",
            "city": "上海市",
            "district": "徐汇区",
            "detail": "虹桥路 1000 号 QA 验收楼 8 层",
            "is_default": True,
        }
        data = parse_success(client.post("/api/addresses", body), where="POST /api/addresses")
        require(isinstance(data, dict) and isinstance(data.get("id"), int), "address create missing integer id")
        eq(data.get("phone"), phone, "created address phone")
        eq(data.get("receiver"), body["receiver"], "created address receiver")
        return data

    def _ensure_demo_seller(self) -> tuple[QAClient, dict]:
        if self.seller is not None and self.seller_user is not None:
            return self.seller, self.seller_user
        client, _guest = self._new_guest("demo-seller")
        data = parse_success(client.post("/api/auth/demo", {"role": "seller"}), where="POST /api/auth/demo seller")
        user = data.get("user") if isinstance(data, dict) else None
        require(isinstance(user, dict), "seller demo login missing user")
        eq(user.get("role"), "seller", "seller demo role")
        require(isinstance(user.get("shop"), dict), "seller demo user missing shop")
        require_no_secret_fields(data, "seller demo session")
        self.seller, self.seller_user = client, user
        return client, user

    # -------------------------------------------------------------- security
    def check_health(self) -> None:
        client = self._client("health")
        resp = client.get("/api/health")
        require(resp.status == 200, f"GET /api/health -> HTTP {resp.status}, expected 200")
        require(isinstance(resp.json_body, dict), "GET /api/health must return JSON")
        require(resp.json_body.get("ok") is True, f"GET /api/health ok!=true: {resp.json_body!r}")
        eq(resp.json_body.get("app"), "openshop", "health app")
        require(resp.json_body.get("demo") is True, "health demo must be true")
        require(isinstance(resp.json_body.get("version"), str), "health version missing")
        raw = client.get("/healthz")
        require(raw.status == 200, f"GET /healthz -> HTTP {raw.status}")
        eq(raw.text.strip(), "ok", "GET /healthz body")

    def check_guest_session_and_headers(self) -> None:
        client = self._client("session-security")
        resp = client.get("/api/session")
        data = parse_success(resp, where="GET /api/session")
        require_no_secret_fields(data, "guest session")
        require(resp.header_values("Set-Cookie"), "GET /api/session did not Set-Cookie")
        cookie_blob = " | ".join(resp.header_values("Set-Cookie")).lower()
        require("httponly" in cookie_blob, "session cookie must be HttpOnly")
        require("samesite=lax" in cookie_blob, "session cookie must be SameSite=Lax")
        require(client.cookie_value("openshop_session"), "session cookie not stored by client jar")
        require(isinstance(data, dict) and data.get("csrf"), "session csrf missing")
        require(len(data["csrf"]) >= 32, "session csrf must be >=32 chars")
        require(data.get("user") is None and data.get("is_guest") is True, "fresh session must be guest")
        h = resp.headers
        require("content-security-policy" in {str(k).lower() for k in h.keys()}, "CSP header missing")
        csp = str(h.get("Content-Security-Policy", ""))
        require("unsafe-eval" not in csp, "CSP must not allow unsafe-eval")
        require("script-src 'self'" in csp, "CSP script-src 'self' missing")
        eq(str(h.get("X-Content-Type-Options", "")).lower(), "nosniff", "X-Content-Type-Options")
        require(bool(h.get("Referrer-Policy")), "Referrer-Policy header missing")
        require(bool(h.get("Permissions-Policy")), "Permissions-Policy header missing")
        require("no-store" in str(h.get("Cache-Control", "")).lower(), "API response must be Cache-Control no-store")

    def check_bad_csrf_red(self) -> None:
        client, _ = self._new_guest("bad-csrf")
        valid_ish_body = {"offer_id": 1, "sku_id": 1, "quantity": 1, "buy_mode": "standard"}
        resp = client.post("/api/cart/items", valid_ish_body, csrf="qa-invalid-csrf-" + secrets.token_hex(8))
        evaluate_csrf_rejection(resp.status, resp.error_code(), context="live wrong-CSRF write")
        parse_error(resp, {403}, allowed_codes={"CSRF_FAILED"}, where="wrong CSRF")

    def check_missing_csrf_red(self) -> None:
        no_session = self._client("no-session-csrf")
        resp = no_session.post("/api/cart/items", {"offer_id": 1, "sku_id": 1, "quantity": 1}, csrf=None)
        evaluate_csrf_rejection(resp.status, resp.error_code(), context="no-session write without CSRF")
        parse_error(resp, {403}, allowed_codes={"CSRF_FAILED"}, where="missing CSRF/no session")

        client, _ = self._new_guest("missing-csrf")
        resp2 = client.post("/api/auth/logout", {}, csrf=None)
        evaluate_csrf_rejection(resp2.status, resp2.error_code(), context="guest write without CSRF header")
        parse_error(resp2, {403}, allowed_codes={"CSRF_FAILED"}, where="missing CSRF with cookie")

    def check_unauth_protected_endpoints(self) -> None:
        client, _ = self._new_guest("guest-protected")
        failures: list[str] = []
        for path in ("/api/favorites", "/api/addresses", "/api/orders", "/api/coupons"):
            resp = client.get(path)
            try:
                parse_error(resp, {401}, allowed_codes={"AUTH_REQUIRED"}, where=f"guest GET {path}")
            except Exception as exc:  # noqa: BLE001
                failures.append(str(exc))
        resp = client.post("/api/checkout/quote", {"shipping_method": "standard"})
        try:
            parse_error(resp, {401}, allowed_codes={"AUTH_REQUIRED"}, where="guest POST /api/checkout/quote")
        except Exception as exc:  # noqa: BLE001
            failures.append(str(exc))
        resp = client.get("/api/seller/overview")
        try:
            parse_error(resp, {401}, allowed_codes={"AUTH_REQUIRED"}, where="guest GET /api/seller/overview")
        except Exception as exc:  # noqa: BLE001
            failures.append(str(exc))
        if failures:
            raise CheckFailure("; ".join(failures))

    def check_csrf_rotation(self) -> None:
        client, _ = self._new_guest("csrf-rotation")
        old_csrf = client.csrf
        data = parse_success(client.post("/api/auth/demo", {"role": "buyer"}), where="POST /api/auth/demo buyer")
        eq((data.get("user") or {}).get("role"), "buyer", "demo buyer role")
        new_csrf = client.csrf
        require(new_csrf and new_csrf != old_csrf, "CSRF token must rotate after identity change")
        stale = client.post("/api/auth/logout", {}, csrf=old_csrf)
        evaluate_csrf_rejection(stale.status, stale.error_code(), context="stale CSRF after login")
        parse_error(stale, {403}, allowed_codes={"CSRF_FAILED"}, where="stale CSRF")
        fresh = parse_success(client.post("/api/auth/logout", {}), where="logout with fresh CSRF")
        require(fresh.get("is_guest") is True, "logout must return a fresh guest session")
        require(fresh.get("csrf"), "logout guest csrf missing")
        require_no_secret_fields(fresh, "logout guest session")

    # ---------------------------------------------------------- auth fixtures
    def check_register_accounts(self) -> None:
        self.b, self.b_user = self._register_buyer("buyer-b", "验收买家乙")
        self.seller2, self.seller2_user = self._register_buyer("seller2", "验收商家丙")
        self.seller2_user, self.seller2_shop = self._promote_to_seller2(self.seller2_user)
        session = parse_success(self.seller2.begin_session(), where="seller2 refresh session")
        user = session.get("user") or {}
        eq(user.get("role"), "seller", "seller2 promoted role")
        require(isinstance(user.get("shop"), dict), "seller2 session missing shop")
        eq(user["shop"].get("id"), self.seller2_shop["id"], "seller2 shop id")
        self.seller2_user = user
        require_no_secret_fields(session, "seller2 session")

    def check_role_isolation(self) -> None:
        b = self._need("b", "API-010")
        s2 = self._need("seller2", "API-010")
        seller2_shop = self._need("seller2_shop", "API-010")
        buyer_resp = b.get("/api/seller/overview")
        parse_error(buyer_resp, {403}, allowed_codes={"FORBIDDEN"}, where="buyer GET /api/seller/overview")
        own = parse_success(s2.get("/api/seller/overview"), where="seller2 GET /api/seller/overview")
        require(isinstance(own, dict) and isinstance(own.get("shop"), dict), "seller overview missing shop")
        eq(own["shop"].get("id"), seller2_shop["id"], "seller2 overview must be scoped to own shop")

    # ---------------------------------------------------------------- catalog
    def check_categories(self) -> None:
        self.catalog_client, _ = self._new_guest("catalog")
        data = parse_success(self.catalog_client.get("/api/categories"), where="GET /api/categories")
        require(isinstance(data, list) and data, "GET /api/categories must return non-empty Category[]")
        for index, item in enumerate(data):
            require(isinstance(item, dict), f"category[{index}] must be object")
            require(isinstance(item.get("slug"), str) and item["slug"], f"category[{index}] slug missing")
            require(isinstance(item.get("name"), str) and item["name"], f"category[{index}] name missing")
            require(isinstance(item.get("subtitle"), str), f"category[{index}] subtitle missing")
            require(isinstance(item.get("icon"), str) and item["icon"], f"category[{index}] icon missing")
            require(isinstance(item.get("count"), int) and item["count"] >= 0, f"category[{index}] count invalid")
        self.categories = data

    def check_home(self) -> None:
        c = self._need("catalog_client", "API-020")
        data = parse_success(c.get("/api/home"), where="GET /api/home")
        require(isinstance(data, dict), "home data must be object")
        for key in (
            "categories", "stats", "hero", "hero_side", "shelves", "deals", "group_picks",
            "new_arrivals", "recently_viewed", "recommended", "trust",
        ):
            require(key in data, f"home missing key {key}")
        require(isinstance(data["categories"], list) and data["categories"], "home categories must be non-empty list")
        stats = data["stats"]
        require(isinstance(stats, dict), "home stats must be object")
        for key in ("products", "shops", "reviews"):
            require(isinstance(stats.get(key), int), f"home stats.{key} must be int")
            require(stats[key] >= 0, f"home stats.{key} must be >=0")
        hero = data["hero"]
        require(isinstance(hero, dict), "home hero must be object")
        require(isinstance(hero.get("product_slug"), str) and hero.get("product_slug"), "home hero.product_slug missing")
        require(isinstance(hero.get("image"), str) and hero["image"].startswith("/web/assets/"), "home hero image must be local asset")
        require(isinstance(hero.get("price_cents"), int), "home hero.price_cents missing")
        shelves = data["shelves"]
        require(isinstance(shelves, list), "home shelves must be list")
        require(2 <= len(shelves) <= 3, f"home must expose 2-3 shelves, got {len(shelves)}")
        for index, shelf in enumerate(shelves):
            require(isinstance(shelf, dict), f"shelf[{index}] must be object")
            for key in ("key", "title", "href", "items"):
                require(key in shelf, f"shelf[{index}] missing {key}")
            require(isinstance(shelf["items"], list), f"shelf[{index}].items must be list")
        for key in ("hero_side", "deals", "group_picks", "new_arrivals", "recently_viewed", "recommended", "trust"):
            require(isinstance(data[key], list), f"home {key} must be list")
        require(data["trust"], "home trust promises must not be empty")
        self.home = data

    def check_products_search(self) -> None:
        c = self._need("catalog_client", "API-020")
        data = parse_success(c.get("/api/products?page=1&page_size=12"), where="GET /api/products")
        require(isinstance(data, dict), "products data must be object")
        items = data.get("items")
        require(isinstance(items, list) and items, "products.items must be non-empty")
        require(isinstance(data.get("total"), int) and data["total"] >= len(items), "products.total invalid")
        require(data.get("page") == 1, "products.page must echo 1")
        require(data.get("page_size") == 12, "products.page_size must echo 12")
        require(isinstance(data.get("pages"), int) and data["pages"] >= 1, "products.pages invalid")
        facets = data.get("facets")
        require(isinstance(facets, dict), "products.facets missing")
        for key in ("categories", "kinds", "price_min_cents", "price_max_cents"):
            require(key in facets, f"products.facets missing {key}")
        for index, card in enumerate(items):
            self._assert_card(card, f"products.items[{index}]")
        self.product_cards = items
        first_title = str(items[0].get("title") or "")
        require(first_title, "first product card has no title to search")
        search = parse_success(
            c.get("/api/products?" + urllib.parse.urlencode({"q": first_title, "page_size": 24})),
            where="GET /api/products?q=<title>",
        )
        require(isinstance(search, dict) and isinstance(search.get("items"), list), "search data shape invalid")
        slugs = [card.get("slug") for card in search["items"] if isinstance(card, dict)]
        require(items[0].get("slug") in slugs, f"search by exact title {first_title!r} did not return {items[0].get('slug')!r}")
        kind_data = parse_success(c.get("/api/products?kind=self&page_size=24"), where="GET /api/products?kind=self")
        require(isinstance(kind_data, dict) and isinstance(kind_data.get("items"), list), "kind filter response invalid")
        for index, card in enumerate(kind_data["items"]):
            require("self" in (card.get("offer_kinds") or []), f"kind=self result[{index}] lacks self offer")
        injection = parse_success(
            c.get("/api/products?" + urllib.parse.urlencode({"q": "' OR 1=1 --"})),
            where="GET /api/products injection probe",
        )
        require(isinstance(injection, dict) and isinstance(injection.get("items"), list), "injection probe response invalid")

    def _assert_card(self, card: Any, context: str) -> None:
        require(isinstance(card, dict), f"{context} must be object")
        for key in ("id", "slug", "title", "brand", "category", "cover", "price_cents", "market_price_cents", "offer_kinds"):
            require(key in card, f"{context} missing {key}")
        require(isinstance(card["id"], int), f"{context}.id must be int")
        require(isinstance(card["slug"], str) and card["slug"], f"{context}.slug missing")
        require(isinstance(card["title"], str) and card["title"], f"{context}.title missing")
        require(isinstance(card["category"], dict) and card["category"].get("slug"), f"{context}.category invalid")
        require(isinstance(card["cover"], str) and card["cover"].startswith("/web/assets/"), f"{context}.cover must be local")
        require(isinstance(card["price_cents"], int) and card["price_cents"] >= 0, f"{context}.price_cents invalid")
        require(isinstance(card["market_price_cents"], int) and card["market_price_cents"] >= card["price_cents"], f"{context} market price invalid")
        eq(card.get("saved_cents"), max(0, card["market_price_cents"] - card["price_cents"]), f"{context}.saved_cents")
        require(isinstance(card.get("offer_kinds"), list) and card["offer_kinds"], f"{context}.offer_kinds invalid")
        require(isinstance(card.get("rating"), (int, float)), f"{context}.rating invalid")
        require(isinstance(card.get("review_count"), int), f"{context}.review_count invalid")
        require(isinstance(card.get("free_shipping"), bool), f"{context}.free_shipping invalid")

    def check_product_details_and_offers(self) -> None:
        c = self._need("catalog_client", "API-020")
        cards = self._need("product_cards", "API-022")
        main_offer: OfferChoice | None = None
        group_offer: OfferChoice | None = None
        self.product_details = {}
        for card in cards:
            slug = card.get("slug")
            if not isinstance(slug, str):
                continue
            detail = parse_success(c.get(f"/api/products/{urllib.parse.quote(slug)}"), where=f"GET /api/products/{slug}")
            require(isinstance(detail, dict), f"product {slug}: detail must be object")
            self.product_details[slug] = detail
            for key in (
                "description", "story", "images", "tags", "highlights", "specs", "detail_sections",
                "faq", "offers", "skus", "reviews", "related", "created_at",
            ):
                require(key in detail, f"product {slug}: detail missing {key}")
            require(isinstance(detail["images"], list) and detail["images"], f"product {slug}: images must be non-empty")
            for image in detail["images"]:
                require(isinstance(image, dict) and isinstance(image.get("src"), str) and image["src"].startswith("/web/assets/"), f"product {slug}: image must be local asset")
            require(isinstance(detail["offers"], list) and detail["offers"], f"product {slug}: offers must be non-empty")
            require(isinstance(detail["skus"], list) and detail["skus"], f"product {slug}: skus must be non-empty")
            skus = detail["skus"]
            for index, sku in enumerate(skus):
                require(isinstance(sku, dict) and isinstance(sku.get("id"), int), f"product {slug} sku[{index}] invalid")
                require(isinstance(sku.get("price_delta_cents"), int) and sku["price_delta_cents"] >= 0, f"product {slug} sku[{index}] delta invalid")
            sku = skus[0]
            for index, offer in enumerate(detail["offers"]):
                require(isinstance(offer, dict), f"product {slug} offer[{index}] must be object")
                for key in ("id", "kind", "name", "shop", "price_cents", "market_price_cents", "stock", "services"):
                    require(key in offer, f"product {slug} offer[{index}] missing {key}")
                require(isinstance(offer["shop"], dict) and isinstance(offer["shop"].get("slug"), str), f"product {slug} offer[{index}].shop invalid")
                if main_offer is None and offer["shop"].get("slug") == "openshop-self" and int(offer.get("stock", 0)) > 0:
                    unit = int(offer["price_cents"]) + int(sku["price_delta_cents"])
                    main_offer = OfferChoice(
                        product_slug=slug,
                        product_title=str(detail.get("title") or card.get("title") or slug),
                        offer_id=int(offer["id"]),
                        sku_id=int(sku["id"]),
                        sku_name=str(sku.get("name") or ""),
                        unit_price_cents=unit,
                        shop_id=int(offer["shop"].get("id")),
                        shop_slug=str(offer["shop"]["slug"]),
                        shop_kind=str(offer["shop"].get("kind") or offer.get("kind")),
                        stock=int(offer["stock"]),
                        kind=str(offer.get("kind")),
                    )
                    self.main_detail = detail
                group = offer.get("group")
                if (
                    group_offer is None
                    and isinstance(group, dict)
                    and int(group.get("size", 0)) in (2, 3)
                    and int(group.get("stock", 0)) > 0
                    and int(group.get("price_cents", 0)) > 0
                ):
                    group_unit = int(group["price_cents"]) + int(sku["price_delta_cents"])
                    group_offer = OfferChoice(
                        product_slug=slug,
                        product_title=str(detail.get("title") or card.get("title") or slug),
                        offer_id=int(offer["id"]),
                        sku_id=int(sku["id"]),
                        sku_name=str(sku.get("name") or ""),
                        unit_price_cents=group_unit,
                        shop_id=int(offer["shop"].get("id")),
                        shop_slug=str(offer["shop"]["slug"]),
                        shop_kind=str(offer["shop"].get("kind") or offer.get("kind")),
                        stock=int(group.get("stock", offer.get("stock", 0))),
                        kind=str(offer.get("kind")),
                        group_size=int(group["size"]),
                        group_price_cents=int(group["price_cents"]),
                    )
            reviews = detail.get("reviews")
            require(isinstance(reviews, dict), f"product {slug}: reviews object missing")
            require(isinstance(reviews.get("summary"), dict), f"product {slug}: reviews.summary missing")
            require(isinstance(reviews.get("items"), list), f"product {slug}: reviews.items missing")
            related = detail.get("related")
            require(isinstance(related, list), f"product {slug}: related must be list")
            if main_offer is not None and group_offer is not None:
                break
        require(main_offer is not None, "no in-stock openshop-self offer with valid SKU found in catalog fixture")
        require(group_offer is not None, "no in-stock 2/3-person group offer found in catalog fixture")
        self.main_offer = main_offer
        self.group_offer = group_offer
        self.group_required_size = int(group_offer.group_size or 0)
        self.runner.add_extra("main_offer", {"slug": main_offer.product_slug, "offer_id": main_offer.offer_id, "sku_id": main_offer.sku_id, "unit_price_cents": main_offer.unit_price_cents})
        self.runner.add_extra("group_offer", {"slug": group_offer.product_slug, "offer_id": group_offer.offer_id, "group_size": group_offer.group_size, "group_price_cents": group_offer.group_price_cents})

    def check_reviews_list(self) -> None:
        c = self._need("catalog_client", "API-020")
        offer = self._need("main_offer", "API-023")
        path = f"/api/products/{urllib.parse.quote(offer.product_slug)}/reviews?page=1&page_size=5&sort=recent"
        data = parse_success(c.get(path), where="GET product reviews")
        require(isinstance(data, dict), "reviews response must be object")
        for key in ("summary", "items", "total", "page", "page_size", "pages", "facets"):
            require(key in data, f"reviews missing {key}")
        require(isinstance(data["items"], list), "reviews.items must be list")
        require(isinstance(data["summary"].get("count"), int), "reviews.summary.count must be int")
        require(isinstance(data["summary"].get("distribution"), list), "reviews.summary.distribution must be list")
        require(isinstance(data["facets"].get("tags"), list), "reviews.facets.tags must be list")
        for index, review in enumerate(data["items"]):
            require(isinstance(review, dict), f"review[{index}] must be object")
            require(1 <= int(review.get("rating", 0)) <= 5, f"review[{index}] rating invalid")
            require(isinstance(review.get("body"), str) and review["body"], f"review[{index}] body missing")
            require(isinstance(review.get("created_at"), str), f"review[{index}] created_at missing")
        filtered = parse_success(
            c.get(f"/api/products/{urllib.parse.quote(offer.product_slug)}/reviews?rating=5&page_size=24"),
            where="GET product reviews rating=5",
        )
        for index, review in enumerate(filtered.get("items", [])):
            eq(review.get("rating"), 5, f"rating=5 filter review[{index}]")

    def check_suggest_and_not_found(self) -> None:
        c = self._need("catalog_client", "API-020")
        offer = self._need("main_offer", "API-023")
        suggest = parse_success(
            c.get("/api/search/suggest?q=" + urllib.parse.quote(offer.product_slug[:2])),
            where="GET /api/search/suggest",
        )
        require(isinstance(suggest, dict), "suggest response must be object")
        for key in ("suggestions", "products", "hot"):
            require(isinstance(suggest.get(key), list), f"suggest.{key} must be list")
        missing = c.get("/api/products/qa-slug-does-not-exist-" + self.run_id)
        parse_error(missing, {404}, allowed_codes={"NOT_FOUND"}, where="unknown product slug")

    def check_browse_event(self) -> None:
        c = self._need("catalog_client", "API-020")
        offer = self._need("main_offer", "API-023")
        data = parse_success(
            c.post("/api/events/view", {"product_slug": offer.product_slug}),
            where="POST /api/events/view",
        )
        require(isinstance(data, dict), "events/view data must be an object")
        home = parse_success(c.get("/api/home"), where="GET /api/home after browse event")
        require(isinstance(home.get("recently_viewed"), list), "home recently_viewed must be list after browse")

    def check_favorites_owner(self) -> None:
        b = self._need("b", "API-010")
        offer = self._need("main_offer", "API-023")
        slug = urllib.parse.quote(offer.product_slug)
        data = parse_success(b.post(f"/api/favorites/{slug}/toggle", {}), where="favorites toggle on")
        eq(data.get("favorited"), True, "favorites first toggle")
        require(isinstance(data.get("total"), int) and data["total"] >= 1, "favorites total after toggle")
        favorites = parse_success(b.get("/api/favorites"), where="GET /api/favorites")
        require(isinstance(favorites, dict) and isinstance(favorites.get("items"), list), "favorites items shape")
        require(any(card.get("slug") == offer.product_slug for card in favorites["items"]), "favorites list missing toggled product")
        data2 = parse_success(b.post(f"/api/favorites/{slug}/toggle", {}), where="favorites toggle off")
        eq(data2.get("favorited"), False, "favorites second toggle")
        favorites2 = parse_success(b.get("/api/favorites"), where="GET /api/favorites after off")
        require(all(card.get("slug") != offer.product_slug for card in favorites2.get("items", [])), "favorites list still contains removed product")

    # ----------------------------------------------------- commerce: guest cart
    def check_guest_cart_and_isolation(self) -> None:
        offer = self._need("main_offer", "API-023")
        self.a, _ = self._new_guest("buyer-a")
        a = self.a
        resp = self._add_cart(a, offer)
        data = parse_success(resp, where="guest POST /api/cart/items")
        require(isinstance(data, dict) and isinstance(data.get("items"), list), "cart add missing CartResponse.items")
        matches = [i for i in data["items"] if i.get("offer_id") == offer.offer_id]
        require(len(matches) == 1, f"cart must contain exactly one matching item, got {len(matches)}")
        item = matches[0]
        self.a_cart_item_id = int(item["id"])
        eq(item.get("line_price_cents"), offer.unit_price_cents, "cart line price must be server offer price")
        eq(item.get("quantity"), 1, "cart quantity")
        summary = data.get("summary") or {}
        eq(summary.get("item_count"), 1, "cart summary item_count")
        eq(summary.get("subtotal_cents"), offer.unit_price_cents, "cart summary subtotal")
        notices = data.get("notices")
        require(isinstance(notices, list), "cart notices must be list")

        g2, _ = self._new_guest("guest-cart-other")
        other_cart = self._cart(g2, "other guest cart")
        eq(other_cart.get("items"), [], "a different guest session must not see first guest cart")
        patch = g2.patch(f"/api/cart/items/{self.a_cart_item_id}", {"quantity": 2})
        parse_error(patch, {404}, allowed_codes={"NOT_FOUND"}, where="IDOR cart PATCH")
        delete = g2.delete(f"/api/cart/items/{self.a_cart_item_id}")
        parse_error(delete, {404}, allowed_codes={"NOT_FOUND"}, where="IDOR cart DELETE")
        a_cart = self._cart(a, "owner cart after IDOR attempts")
        matches = self._cart_has_offer(a_cart, offer.offer_id)
        require(len(matches) == 1 and matches[0].get("quantity") == 1, "owner cart changed after cross-session IDOR attempts")

    def check_cart_negative_controls(self) -> None:
        offer = self._need("main_offer", "API-023")
        g3, _ = self._new_guest("cart-negative")
        for quantity in (0, -1, offer.stock + 1):
            resp = self._add_cart(g3, offer, quantity=quantity)
            parse_error(resp, {400, 409}, where=f"cart add quantity={quantity}")
        bad_sku = self._add_cart(g3, offer, extra={"sku_id": 2_000_000_000})
        parse_error(bad_sku, {400, 404}, where="cart add invalid sku")
        cart = self._cart(g3, "cart after rejected adds")
        eq(cart.get("items"), [], "rejected cart adds must not create items")

    def check_cart_mixed_mode(self) -> None:
        offer = self._need("main_offer", "API-023")
        group = self._need("group_offer", "API-023")
        g3, _ = self._new_guest("cart-mixed")
        parse_success(self._add_cart(g3, offer), where="standard cart add for mixed mode")
        mixed = self._add_cart(g3, group, buy_mode="group")
        parse_error(mixed, {422}, allowed_codes={"MIXED_BUY_MODE"}, where="mixed standard/group cart")

    def check_cart_price_tamper(self) -> None:
        offer = self._need("main_offer", "API-023")
        tamper, _ = self._new_guest("cart-price-tamper")
        resp = self._add_cart(
            tamper,
            offer,
            extra={"unit_price_cents": 1, "line_price_cents": 1, "total_cents": 1, "price_cents": 1},
        )
        if resp.status in (400, 422):
            parse_error(resp, {400, 422}, where="cart add rejects forged price fields")
            return
        data = parse_success(resp, where="cart add with forged price fields")
        matches = [i for i in data.get("items", []) if i.get("offer_id") == offer.offer_id]
        require(len(matches) == 1, "price-tamper cart add missing item")
        eq(matches[0].get("line_price_cents"), offer.unit_price_cents, "forged price fields must not change cart line price")
        subtotal = (data.get("summary") or {}).get("subtotal_cents")
        eq(subtotal, offer.unit_price_cents, "forged price fields must not change cart subtotal")

    def check_buyer_login_cart_merge(self) -> None:
        a = self._need("a", "API-030")
        offer = self._need("main_offer", "API-023")
        old_csrf = a.csrf
        data = parse_success(a.post("/api/auth/demo", {"role": "buyer"}), where="POST /api/auth/demo buyer A")
        require(isinstance(data, dict) and isinstance(data.get("user"), dict), "demo buyer response missing user")
        user = data["user"]
        eq(user.get("handle"), "demo_buyer", "demo buyer handle")
        eq(user.get("role"), "buyer", "demo buyer role")
        require(a.csrf and a.csrf != old_csrf, "CSRF must rotate on demo buyer login")
        require_no_secret_fields(data, "demo buyer login")
        self.a_user = user
        cart = self._cart(a, "merged cart after demo buyer login")
        matches = self._cart_has_offer(cart, offer.offer_id)
        require(len(matches) == 1, "guest cart item missing after buyer login merge")
        eq(matches[0].get("quantity"), 1, "merged guest cart quantity")
        require((data.get("cart_count") or 0) >= 1, "session cart_count did not include merged item")

    # --------------------------------------------------------- addresses/coupons
    def check_address_create_validate_and_idor(self) -> None:
        a = self._need("a", "API-030")
        b = self._need("b", "API-010")
        addr_data = parse_success(a.get("/api/addresses"), where="GET /api/addresses A")
        require(isinstance(addr_data, dict) and isinstance(addr_data.get("items"), list), "addresses shape invalid")
        self.a_address = self._make_address(a, label="验收家", phone="13800000123")
        listed = parse_success(a.get("/api/addresses"), where="GET /api/addresses after create")
        ids = [item.get("id") for item in listed.get("items", [])]
        require(self.a_address["id"] in ids, "created address not returned by list")
        eq(listed.get("default_id"), self.a_address["id"], "new default address default_id")
        bad_body = {
            "label": "坏地址",
            "receiver": "验收收货人",
            "phone": "123",
            "province": "上海市",
            "city": "上海市",
            "district": "徐汇区",
            "detail": "虹桥路 1000 号 QA 验收楼",
            "is_default": False,
        }
        bad = a.post("/api/addresses", bad_body)
        err = parse_error(bad, {400}, allowed_codes={"VALIDATION_ERROR"}, where="invalid address phone")
        fields = err.get("fields") or {}
        require("phone" in fields, f"invalid address error.fields.phone missing: fields={fields!r}")
        self.b_address = self._make_address(b, label="乙的家", phone="13800000456")
        idor_patch = b.patch(f"/api/addresses/{self.a_address['id']}", {"receiver": "越权修改"})
        parse_error(idor_patch, {404}, allowed_codes={"NOT_FOUND"}, where="IDOR address PATCH")
        idor_delete = b.delete(f"/api/addresses/{self.a_address['id']}")
        parse_error(idor_delete, {404}, allowed_codes={"NOT_FOUND"}, where="IDOR address DELETE")
        after = parse_success(a.get("/api/addresses"), where="GET /api/addresses after IDOR")
        target = [item for item in after.get("items", []) if item.get("id") == self.a_address["id"]]
        require(len(target) == 1, "A's address disappeared after B IDOR attempts")
        eq(target[0].get("receiver"), self.a_address.get("receiver"), "A's address was modified by another user")

    def check_coupon_claim_and_expiry(self) -> None:
        a = self._need("a", "API-030")
        payload = self._coupons(a, "GET /api/coupons A")
        available_defs = payload["available"]
        for index, item in enumerate(available_defs):
            require(isinstance(item, dict), f"coupon available[{index}] must be object")
            for key in ("code", "title", "description", "threshold_cents", "discount_cents", "scope", "claimed", "claimable"):
                require(key in item, f"coupon available[{index}] missing {key}")
            if item.get("claimable") and not item.get("claimed"):
                require(item.get("expires_at") is None, f"unclaimed CouponDef {item.get('code')} expires_at must be null")
        for index, item in enumerate(payload["mine"]):
            require(isinstance(item, dict), f"coupon mine[{index}] must be object")
            require(isinstance(item.get("user_coupon_id"), int), f"coupon mine[{index}] missing user_coupon_id")
            require(isinstance(item.get("status"), str), f"coupon mine[{index}] missing status")
            require(isinstance(item.get("expires_at"), str) and item["expires_at"], f"claimed coupon mine[{index}] expires_at must be set")
        claimable = [
            item for item in available_defs
            if isinstance(item, dict) and item.get("claimable") is True and item.get("claimed") is not True
        ]
        require(claimable, "no claimable coupon in seed/implementation; coupon journey cannot run")
        claimed_codes: list[str] = []
        for definition in claimable[:2]:
            code = str(definition["code"])
            require(definition.get("expires_at") is None, f"unclaimed coupon {code} expires_at must be null before claim")
            claim = parse_success(
                a.post(f"/api/coupons/{urllib.parse.quote(code)}/claim", {}),
                where=f"POST coupon claim {code}",
            )
            coupon = claim.get("coupon") if isinstance(claim, dict) else None
            require(isinstance(coupon, dict), f"claim {code} response missing coupon")
            eq(coupon.get("code"), code, f"claim {code} code")
            require(isinstance(coupon.get("expires_at"), str) and coupon["expires_at"], f"claim {code} expires_at missing")
            claimed_codes.append(code)
        duplicate = a.post(f"/api/coupons/{urllib.parse.quote(claimed_codes[0])}/claim", {})
        parse_error(duplicate, {400, 409, 422}, where="duplicate coupon claim")
        unknown = a.post("/api/coupons/QA_UNKNOWN_COUPON/claim", {})
        parse_error(unknown, {404}, allowed_codes={"NOT_FOUND"}, where="unknown coupon claim")
        refreshed = self._coupons(a, "GET /api/coupons after claims")
        normalized = self._normalized_coupons(refreshed)
        for code in claimed_codes:
            entries = [c for c in normalized if c["code"] == code]
            require(entries, f"claimed coupon {code} missing from mine after claim")
            require(entries[0]["expires_at"], f"claimed coupon {code} missing expires_at in mine")
            eq(entries[0]["status"], "AVAILABLE", f"claimed coupon {code} status")
        avail = [c for c in normalized if c["status"] == "AVAILABLE"]
        require(len(avail) >= 2, f"need at least 2 available coupons for cancel/refund journey checks, got {len(avail)}")

    def check_quote_server_pricing(self) -> None:
        a = self._need("a", "API-030")
        offer = self._need("main_offer", "API-023")
        quote = self._quote(a)
        for key in ("subtotal_cents", "market_subtotal_cents", "savings_cents", "shipping_cents", "discount_cents", "total_cents", "selected_count", "shipping_options", "shipping_method", "address_required"):
            require(key in quote, f"quote missing {key}")
        eq(quote.get("subtotal_cents"), offer.unit_price_cents, "quote subtotal must be server price")
        eq(quote.get("discount_cents"), 0, "quote without coupon discount")
        eq(quote.get("shipping_cents"), 0, "standard shipping fee")
        eq(quote.get("total_cents"), quote.get("subtotal_cents"), "standard quote total")
        require(quote.get("address_required") is True, "quote address_required must be true")
        options = quote.get("shipping_options")
        require(isinstance(options, list) and len(options) >= 2, "quote shipping_options must include standard and express")
        express = [o for o in options if o.get("code") == "express"]
        require(express and int(express[0].get("fee_cents", -1)) == 1200, "quote express fee must be 1200")
        express_quote = self._quote(a, shipping="express")
        eq(express_quote.get("shipping_cents"), 1200, "express quote shipping")
        eq(express_quote.get("total_cents"), express_quote.get("subtotal_cents") + 1200, "express quote total")
        invalid = a.post("/api/checkout/quote", {"shipping_method": "standard", "coupon_code": "QA_UNKNOWN_COUPON"})
        invalid_data = parse_success(invalid, where="quote with invalid coupon")
        require(invalid_data.get("coupon") is None, "invalid coupon must not be silently applied")
        require(invalid_data.get("coupon_error"), "invalid coupon must produce coupon_error")
        payload, available = self._available_coupons(a)
        chosen = self._pick_coupon(available, int(quote["subtotal_cents"]))
        applied = self._quote(a, coupon_code=chosen["code"])
        coupon = applied.get("coupon")
        require(isinstance(coupon, dict), "valid coupon was not applied in quote")
        eq(coupon.get("code"), chosen["code"], "quote applied coupon code")
        eq(coupon.get("discount_cents"), chosen["discount_cents"], "quote coupon discount")
        expected_total = int(applied["subtotal_cents"]) + int(applied["shipping_cents"]) - int(applied["discount_cents"])
        eq(applied.get("total_cents"), expected_total, "quote total with coupon")
        require(int(applied.get("discount_cents", 0)) <= int(applied["subtotal_cents"]), "quote discount exceeds subtotal")
        self.main_quote = applied
        self.main_coupon = chosen


    def _extract_order_no(self, data: dict) -> str:
        require(isinstance(data, dict), "order create response data must be object")
        nested = data.get("order") if isinstance(data.get("order"), dict) else data
        order_no = nested.get("order_no")
        require(isinstance(order_no, str) and order_no, "order create response missing order_no")
        return order_no

    def _offer_stock(self, slug: str, offer_id: int) -> int:
        detail = self._detail(slug)
        for offer in detail.get("offers", []):
            if isinstance(offer, dict) and offer.get("id") == offer_id:
                return int(offer.get("stock", -1))
        raise CheckFailure(f"offer {offer_id} not found in detail for {slug}")

    def _checkout_body(
        self,
        *,
        address_id: int,
        key: str,
        coupon_code: str | None = None,
        note: str = "",
        extra: dict | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "address_id": int(address_id),
            "shipping_method": "standard",
            "note": note,
            "payment_method": "demo",
            "idempotency_key": key,
        }
        if coupon_code:
            body["coupon_code"] = coupon_code
        if extra:
            body.update(extra)
        return body

    def _pay(self, client: QAClient, order_no: str, *, method: str = "demo") -> dict:
        path = f"/api/orders/{urllib.parse.quote(order_no)}/pay"
        parse_success(client.post(path, {"payment_method": method}), where=f"POST pay {order_no}")
        return self._order(client, order_no)

    def _coupon_entry(self, client: QAClient, code: str) -> dict | None:
        payload = self._coupons(client)
        for entry in self._normalized_coupons(payload):
            if entry["code"] == code:
                return entry
        return None

    # ------------------------------------------------------------ order create
    def check_order_create_idempotency(self) -> None:
        a = self._need("a", "API-030")
        offer = self._need("main_offer", "API-023")
        address = self._need("a_address", "API-035")
        coupon = self._need("main_coupon", "API-036/037")
        quote = self._need("main_quote", "API-037")
        stock_before = self._offer_stock(offer.product_slug, offer.offer_id)
        body = self._checkout_body(
            address_id=address["id"],
            key=self._key("main"),
            coupon_code=coupon["code"],
            note="QA 主流程验收",
        )
        order_no, detail = self._create_order(a, body)
        self.a_order_no = order_no
        self.a_order = detail
        eq(detail.get("status"), "PENDING_PAYMENT", "new order status")
        require(isinstance(detail.get("status_label"), str) and detail["status_label"], "new order status_label missing")
        eq(detail.get("total_cents"), quote["total_cents"], "order total must equal server quote")
        eq(detail.get("subtotal_cents"), quote["subtotal_cents"], "order subtotal must equal quote")
        eq(detail.get("discount_cents"), quote["discount_cents"], "order discount must equal quote")
        eq(detail.get("shipping_cents"), quote["shipping_cents"], "order shipping must equal quote")
        address_snapshot = detail.get("address")
        require(isinstance(address_snapshot, dict), "order detail missing address snapshot")
        eq(address_snapshot.get("phone"), address.get("phone"), "order address snapshot phone")
        packages = detail.get("packages")
        require(
            isinstance(packages, list) and len(packages) == 1,
            f"single-shop order must have 1 package, got {len(packages) if isinstance(packages, list) else packages!r}",
        )
        items = self._order_items(detail)
        require(len(items) == 1, f"single-item order must expose 1 item, got {len(items)}")
        item = items[0]
        eq(item.get("unit_price_cents"), offer.unit_price_cents, "order item unit price must be server price")
        eq(item.get("reviewable"), False, "PENDING order item must not be reviewable")
        eq(item.get("reviewed"), False, "new order item must not be reviewed")
        actions = detail.get("actions") or {}
        eq(actions.get("pay"), True, "PENDING_PAYMENT actions.pay")
        eq(actions.get("cancel"), True, "PENDING_PAYMENT actions.cancel")
        eq(actions.get("confirm"), False, "PENDING_PAYMENT actions.confirm")
        eq(actions.get("review"), False, "PENDING_PAYMENT actions.review")
        eq(actions.get("refund"), False, "PENDING_PAYMENT actions.refund")
        stock_after = self._offer_stock(offer.product_slug, offer.offer_id)
        eq(stock_after, stock_before - 1, "stock must decrement exactly once at order creation")
        cart_after = self._cart(a, "cart after order create")
        eq(cart_after.get("items"), [], "ordered cart items must be removed")
        coupon_after = self._coupon_entry(a, coupon["code"])
        require(coupon_after is not None, f"used coupon {coupon['code']} disappeared from mine")
        eq(coupon_after["status"], "USED", "coupon must be USED by order creation")

        replay = parse_success(a.post("/api/orders", body), where="POST /api/orders idempotent replay")
        replay_no = self._extract_order_no(replay)
        eq(replay_no, order_no, "idempotent replay must return same order_no")
        eq(self._offer_stock(offer.product_slug, offer.offer_id), stock_after, "idempotent replay must not decrement stock")
        coupon_replay = self._coupon_entry(a, coupon["code"])
        require(coupon_replay is not None and coupon_replay["status"] == "USED", "idempotent replay must not consume coupon twice")

        conflict_body = dict(body)
        conflict_body["note"] = "QA changed body with same idempotency key"
        conflict = a.post("/api/orders", conflict_body)
        parse_error(conflict, {409}, allowed_codes={"IDEMPOTENCY_CONFLICT"}, where="idempotency conflict different body")

    def check_order_idor_and_pay(self) -> None:
        a = self._need("a", "API-030")
        b = self._need("b", "API-010")
        order_no = self._need("a_order_no", "API-038")
        unauthorized_get = b.get(f"/api/orders/{urllib.parse.quote(order_no)}")
        parse_error(unauthorized_get, {404}, allowed_codes={"NOT_FOUND"}, where="IDOR order GET")
        unauthorized_pay = b.post(f"/api/orders/{urllib.parse.quote(order_no)}/pay", {"payment_method": "demo"})
        parse_error(unauthorized_pay, {404}, allowed_codes={"NOT_FOUND"}, where="IDOR order pay")
        pending = self._order(a, order_no)
        eq(pending.get("status"), "PENDING_PAYMENT", "unpaid order status before invalid payment probes")
        bad_method = a.post(f"/api/orders/{urllib.parse.quote(order_no)}/pay", {"payment_method": "cash"})
        parse_error(bad_method, {400, 422}, where="non-demo payment method")
        still_pending = self._order(a, order_no)
        eq(still_pending.get("status"), "PENDING_PAYMENT", "order must stay pending after rejected payment method")
        require(not still_pending.get("paid_at"), "paid_at must remain null after rejected payment")
        early_confirm = a.post(f"/api/orders/{urllib.parse.quote(order_no)}/confirm", {})
        parse_error(early_confirm, {409}, allowed_codes={"ORDER_STATE"}, where="confirm before shipment")
        paid = self._pay(a, order_no)
        eq(paid.get("status"), "PAID", "demo paid order status")
        eq(paid.get("payment_method"), "demo", "payment_method must be demo")
        require(isinstance(paid.get("paid_at"), str) and paid["paid_at"], "paid_at missing after demo payment")
        actions = paid.get("actions") or {}
        eq(actions.get("confirm"), False, "PAID actions.confirm before shipment")
        eq(actions.get("refund"), True, "PAID actions.refund")
        orders = parse_success(a.get("/api/orders?tab=all&page=1"), where="GET /api/orders")
        require(isinstance(orders, dict) and isinstance(orders.get("items"), list), "GET /api/orders shape invalid")
        require(
            any(o.get("order_no") == order_no for o in orders["items"] if isinstance(o, dict)),
            "buyer order list lacks new order",
        )
        self.a_order = paid
        self.buyer_package_id = int(paid["packages"][0]["id"])

    def check_seller_ship_and_buyer_visibility(self) -> None:
        a = self._need("a", "API-030")
        b = self._need("b", "API-010")
        seller2 = self._need("seller2", "API-010")
        order_no = self._need("a_order_no", "API-038")
        package_id = self._need("buyer_package_id", "API-039")
        order = self._order(a, order_no)
        eq(order.get("status"), "PAID", "order must be PAID before shipping")
        require(all(p.get("status") == "PENDING" for p in order.get("packages", [])), "packages must be PENDING after payment")
        tracking = "QA" + self.run_id.upper() + "SHIP1"
        require(6 <= len(tracking) <= 24, "QA tracking number length bug")
        body = {"carrier": "青鸟速运", "tracking_no": tracking}
        cross_shop = seller2.post(f"/api/seller/orders/{package_id}/ship", body)
        parse_error(cross_shop, {404}, allowed_codes={"NOT_FOUND"}, where="cross-shop seller ship")
        buyer_ship = b.post(f"/api/seller/orders/{package_id}/ship", body)
        parse_error(buyer_ship, {403}, allowed_codes={"FORBIDDEN"}, where="buyer seller ship")
        seller, seller_user = self._ensure_demo_seller()
        require(isinstance(seller_user.get("shop"), dict), "demo seller session missing shop")
        eq(seller_user["shop"].get("slug"), "openshop-self", "demo seller must own openshop-self")
        parse_success(seller.post(f"/api/seller/orders/{package_id}/ship", body), where="seller POST ship")
        self.tracking_no = tracking
        shipped = self._order(a, order_no)
        eq(shipped.get("status"), "SHIPPED", "order status after seller ship")
        require(isinstance(shipped.get("packages"), list) and len(shipped["packages"]) == 1, "shipped order package shape")
        package = shipped["packages"][0]
        eq(package.get("carrier"), "青鸟速运", "buyer-visible package carrier")
        eq(package.get("tracking_no"), tracking, "buyer-visible package tracking_no")
        require(isinstance(package.get("shipped_at"), str) and package["shipped_at"], "buyer-visible shipped_at missing")
        eq((shipped.get("actions") or {}).get("confirm"), True, "SHIPPED actions.confirm")
        items = self._order_items(shipped)
        require(items and all(item.get("reviewable") is False for item in items), "SHIPPED order items must not be reviewable yet")
        completed = parse_success(a.post(f"/api/orders/{urllib.parse.quote(order_no)}/confirm", {}), where="buyer confirm receipt")
        require(isinstance(completed, dict), "confirm receipt response data must be object")
        final = self._order(a, order_no)
        eq(final.get("status"), "COMPLETED", "order status after confirm")
        require(isinstance(final.get("completed_at"), str) and final["completed_at"], "completed_at missing")
        final_items = self._order_items(final)
        require(final_items and all(item.get("reviewed") is False for item in final_items), "new completed order items must start unreviewed")
        require(any(item.get("reviewable") is True for item in final_items), "COMPLETED order item should be reviewable")
        eq((final.get("actions") or {}).get("review"), True, "COMPLETED actions.review")
        self.a_order = final

    def check_review_and_aggregate(self) -> None:
        a = self._need("a", "API-030")
        b = self._need("b", "API-010")
        order_no = self._need("a_order_no", "API-038")
        offer = self._need("main_offer", "API-023")
        order = self._order(a, order_no)
        eq(order.get("status"), "COMPLETED", "review requires COMPLETED order")
        items = self._order_items(order)
        require(items, "completed order has no items to review")
        reviewable = [item for item in items if item.get("reviewable") is True and item.get("reviewed") is False]
        require(reviewable, "completed order exposes no reviewable item")
        target = reviewable[0]
        title = f"QA验收-{self.run_id[:6]}"
        body_text = "这是一次真实验收后的评价，商品质感与页面描述一致，包装完整。"
        review_payload = {
            "reviews": [
                {
                    "item_id": int(target["id"]),
                    "rating": 5,
                    "title": title,
                    "body": body_text,
                    "tags": ["质感好"],
                }
            ]
        }
        unauthorized = b.post(f"/api/orders/{urllib.parse.quote(order_no)}/reviews", review_payload)
        parse_error(unauthorized, {404}, allowed_codes={"NOT_FOUND"}, where="IDOR review submit")
        before = self._detail(offer.product_slug, a)
        before_count = int(before.get("review_count", -1))
        before_summary_count = int(((before.get("reviews") or {}).get("summary") or {}).get("count", -1))
        response = parse_success(
            a.post(f"/api/orders/{urllib.parse.quote(order_no)}/reviews", review_payload),
            where="POST order reviews",
        )
        reviews = response.get("reviews") if isinstance(response, dict) else None
        require(isinstance(reviews, list) and reviews, "review response must contain non-empty reviews[]")
        created = reviews[0]
        eq(created.get("title"), title, "review title response")
        eq(created.get("body"), body_text, "review body response")
        eq(created.get("mine"), True, "new review must be marked mine for owner")
        require(isinstance(created.get("author"), str) and created["author"], "review author missing")
        after_order = self._order(a, order_no)
        target_after = [i for i in self._order_items(after_order) if i.get("id") == target["id"]]
        require(len(target_after) == 1, "reviewed order item missing after review")
        eq(target_after[0].get("reviewed"), True, "order item reviewed flag after review")
        eq(target_after[0].get("reviewable"), False, "order item reviewable flag after review")
        if all(i.get("reviewable") is False for i in self._order_items(after_order)):
            eq((after_order.get("actions") or {}).get("review"), False, "actions.review after all items reviewed")
        duplicate = a.post(f"/api/orders/{urllib.parse.quote(order_no)}/reviews", review_payload)
        parse_error(duplicate, {409}, allowed_codes={"DUPLICATE_REVIEW"}, where="duplicate review")
        product_after = self._detail(offer.product_slug, a)
        eq(int(product_after.get("review_count", -1)), before_count + 1, "product review_count aggregate must increment")
        eq(
            int(((product_after.get("reviews") or {}).get("summary") or {}).get("count", -1)),
            before_summary_count + 1,
            "product reviews.summary.count aggregate must increment",
        )
        reviews_list = parse_success(
            a.get(f"/api/products/{urllib.parse.quote(offer.product_slug)}/reviews?sort=recent&page_size=24"),
            where="GET reviews after submit",
        )
        found = [rv for rv in reviews_list.get("items", []) if isinstance(rv, dict) and rv.get("title") == title]
        require(found, "new review not returned by public product reviews endpoint")
        eq(found[0].get("mine"), True, "own review must be marked mine in public review list")
        self.a_review_title = title

    # ---------------------------------------------------------------- cancel
    def check_cancel_restores_stock_coupon(self) -> None:
        a = self._need("a", "API-030")
        offer = self._need("main_offer", "API-023")
        address = self._need("a_address", "API-035")
        self._clear_cart(a)
        parse_success(self._add_cart(a, offer), where="add item for cancel flow")
        quote0 = self._quote(a)
        _payload, available = self._available_coupons(a)
        coupon = self._pick_coupon(available, int(quote0["subtotal_cents"]))
        quote = self._quote(a, coupon_code=coupon["code"])
        stock_before = self._offer_stock(offer.product_slug, offer.offer_id)
        body = self._checkout_body(address_id=address["id"], key=self._key("cancel"), coupon_code=coupon["code"])
        order_no, order = self._create_order(a, body)
        eq(order.get("total_cents"), quote["total_cents"], "cancel-flow order total")
        eq(self._offer_stock(offer.product_slug, offer.offer_id), stock_before - 1, "cancel-flow stock decrement")
        used = self._coupon_entry(a, coupon["code"])
        require(used is not None and used["status"] == "USED", "cancel-flow coupon must be USED after order create")
        cancelled = parse_success(
            a.post(f"/api/orders/{urllib.parse.quote(order_no)}/cancel", {"reason": "QA 取消验收"}),
            where="POST cancel order",
        )
        require(isinstance(cancelled, dict), "cancel order response data must be object")
        final = self._order(a, order_no)
        eq(final.get("status"), "CANCELLED", "order status after cancel")
        require(isinstance(final.get("cancelled_at"), str) and final["cancelled_at"], "cancelled_at missing")
        eq(self._offer_stock(offer.product_slug, offer.offer_id), stock_before, "cancel must restore offer stock")
        restored = self._coupon_entry(a, coupon["code"])
        require(restored is not None, f"cancelled coupon {coupon['code']} no longer visible in mine")
        eq(restored["status"], "AVAILABLE", "cancel must restore coupon to AVAILABLE")
        cart = self._cart(a, "cart after cancel")
        eq(cart.get("items"), [], "cart must not be restored by cancellation")

    # ------------------------------------------------------------------ group
    def check_group_full(self) -> None:
        a = self._need("a", "API-030")
        b = self._need("b", "API-010")
        group = self._need("group_offer", "API-023")
        address_a = self._need("a_address", "API-035")
        address_b = self._need("b_address", "API-035")
        required_size = int(self._need("group_required_size", "API-023"))
        require(required_size in (2, 3), f"QA group fixture supports 2-3 members, got {required_size}")
        self._clear_cart(a)
        self._clear_cart(b)
        add_a = parse_success(self._add_cart(a, group, buy_mode="group"), where="group initiator add to cart")
        require((add_a.get("summary") or {}).get("has_group") is True, "group cart summary.has_group must be true")
        quote_a = self._quote(a)
        require(quote_a.get("has_group") is True, "group quote.has_group must be true")
        eq(quote_a.get("shipping_cents"), 0, "group quote shipping must be standard/free")
        eq(quote_a.get("subtotal_cents"), group.unit_price_cents, "group quote must use group price")
        body_a = self._checkout_body(address_id=address_a["id"], key=self._key("group-a"))
        order_a_no, pending_a = self._create_order(a, body_a)
        group_info = pending_a.get("group")
        require(isinstance(group_info, dict) and isinstance(group_info.get("code"), str), "pending group order missing group.code")
        code = group_info["code"]
        self.group_code = code
        expected_size = int(group_info.get("required_size") or group.group_size or 0)
        eq(expected_size, required_size, "group required_size")
        public_before = self._group_view_anonymous(code)
        eq(public_before.get("joined_count"), 0, "public group must not count an unpaid initiator")
        eq(public_before.get("group_price_cents"), group.group_price_cents, "public group price")
        a_after_pay = self._pay(a, order_a_no)
        eq(a_after_pay.get("status"), "GROUP_FORMING", "group initiator order status after pay")
        public_1 = self._group_view_anonymous(code)
        eq(public_1.get("joined_count"), 1, "public group count after initiator pays")
        eq(public_1.get("status"), "FORMING", "group status after first paid member")
        members = public_1.get("members")
        require(isinstance(members, list) and len(members) == 1, "public group members must match paid count")

        joiners: list[tuple[QAClient, dict, str]] = [(b, address_b, "b")]
        if required_size == 3:
            extra_client, _extra_user = self._register_buyer("group-d", "验收买家丁")
            extra_address = self._make_address(extra_client, label="丁的家", phone="13800000789")
            joiners.append((extra_client, extra_address, "d"))
        require(len(joiners) == required_size - 1, "internal group joiner count bug")
        joined_orders: list[tuple[QAClient, str]] = [(a, order_a_no)]
        for idx, (client, address, label) in enumerate(joiners, start=2):
            self._clear_cart(client)
            added = parse_success(
                self._add_cart(client, group, buy_mode="group", group_code=code),
                where=f"group joiner {idx} add",
            )
            require((added.get("summary") or {}).get("has_group") is True, f"joiner {idx} group cart flag")
            joiner_quote = self._quote(client)
            require(joiner_quote.get("has_group") is True, f"joiner {idx} quote group flag")
            body = self._checkout_body(address_id=address["id"], key=self._key(f"group-{label}"))
            joiner_no, joiner_pending = self._create_order(client, body)
            eq(joiner_pending.get("status"), "PENDING_PAYMENT", "group joiner pending status")
            before_pay = self._group_view_anonymous(code)
            eq(before_pay.get("joined_count"), idx - 1, "unpaid group joiner must not increase public joined_count")
            joining = self._pay(client, joiner_no)
            views_after_pay = self._group_view_anonymous(code)
            eq(views_after_pay.get("joined_count"), idx, "paid group joiner must increase joined_count")
            require(
                isinstance(views_after_pay.get("members"), list) and len(views_after_pay["members"]) == idx,
                "group members length must track paid count",
            )
            joined_orders.append((client, joiner_no))
            if idx < required_size:
                eq(joining.get("status"), "GROUP_FORMING", "group order stays FORMING until full")
            else:
                require(joining.get("status") in {"PAID", "GROUP_FORMING"}, "paid joiner status invalid")
        final_view = self._group_view_anonymous(code)
        eq(final_view.get("status"), "FULL", "group status after all members paid")
        eq(final_view.get("joined_count"), required_size, "group joined_count at full")
        eq(final_view.get("required_size"), required_size, "group required_size in public view")
        require((final_view.get("actions") or {}).get("can_join") is False, "FULL group actions.can_join must be false")
        for client, joiner_no in joined_orders:
            final_order = self._order(client, joiner_no)
            eq(final_order.get("status"), "PAID", "all paid members must be PAID after group fills")
        eq(self._order(a, order_a_no).get("status"), "PAID", "initiator order must become PAID after full group")


    def _find_second_shop_offers(self) -> list[OfferChoice]:
        c = self._need("catalog_client", "API-020")
        cards = self._need("product_cards", "API-022")
        all_cards = list(cards)
        page = parse_success(c.get("/api/products?page=1&page_size=24"), where="GET products for multi-shop fixture")
        if isinstance(page, dict) and isinstance(page.get("items"), list):
            known_slugs = {card.get("slug") for card in all_cards if isinstance(card, dict)}
            for card in page["items"]:
                if isinstance(card, dict) and card.get("slug") not in known_slugs:
                    all_cards.append(card)
                    known_slugs.add(card.get("slug"))
        choices: list[OfferChoice] = []
        seen_shop_ids: set[int] = set()
        for card in all_cards:
            slug = card.get("slug") if isinstance(card, dict) else None
            if not isinstance(slug, str):
                continue
            detail = self.product_details.get(slug)
            if detail is None:
                detail = parse_success(
                    c.get(f"/api/products/{urllib.parse.quote(slug)}"),
                    where=f"GET product {slug} for multi-shop",
                )
                self.product_details[slug] = detail
            skus = detail.get("skus") or []
            if not skus:
                continue
            sku = skus[0]
            for offer in detail.get("offers", []):
                if not isinstance(offer, dict) or int(offer.get("stock", 0)) <= 0:
                    continue
                shop = offer.get("shop") or {}
                shop_id = shop.get("id")
                if not isinstance(shop_id, int) or shop_id in seen_shop_ids:
                    continue
                unit = int(offer.get("price_cents", 0)) + int(sku.get("price_delta_cents", 0))
                choices.append(
                    OfferChoice(
                        product_slug=slug,
                        product_title=str(detail.get("title") or card.get("title") or slug),
                        offer_id=int(offer["id"]),
                        sku_id=int(sku["id"]),
                        sku_name=str(sku.get("name") or ""),
                        unit_price_cents=unit,
                        shop_id=shop_id,
                        shop_slug=str(shop.get("slug") or ""),
                        shop_kind=str(shop.get("kind") or offer.get("kind") or ""),
                        stock=int(offer["stock"]),
                        kind=str(offer.get("kind") or ""),
                    )
                )
                seen_shop_ids.add(shop_id)
                if len(choices) >= 2:
                    return choices[:2]
        raise CheckFailure(f"multi-shop fixture needs 2 distinct shop offers, found {len(choices)}")

    # ---------------------------------------------------------------- refunds
    def check_refund_approve_and_isolation(self) -> None:
        a = self._need("a", "API-030")
        seller2 = self._need("seller2", "API-010")
        offer = self._need("main_offer", "API-023")
        address = self._need("a_address", "API-035")
        seller, _seller_user = self._ensure_demo_seller()
        self._clear_cart(a)
        parse_success(self._add_cart(a, offer), where="refund approve add item")
        quote0 = self._quote(a)
        _payload, available = self._available_coupons(a)
        require(available, "refund approve requires at least one AVAILABLE coupon")
        coupon = self._pick_coupon(available, int(quote0["subtotal_cents"]))
        quote = self._quote(a, coupon_code=coupon["code"])
        stock_before = self._offer_stock(offer.product_slug, offer.offer_id)
        body = self._checkout_body(address_id=address["id"], key=self._key("refund-approve"), coupon_code=coupon["code"])
        order_no, _ = self._create_order(a, body)
        eq(self._order(a, order_no).get("total_cents"), quote["total_cents"], "refund-flow order total must match quote")
        paid = self._pay(a, order_no)
        eq(paid.get("status"), "PAID", "refund approve order must be PAID before request")
        stock_paid = self._offer_stock(offer.product_slug, offer.offer_id)
        require(stock_paid < stock_before, "refund approve stock must be decremented before refund")
        refund_resp = parse_success(
            a.post(
                f"/api/orders/{urllib.parse.quote(order_no)}/refund",
                {"reason": "QA 验收整单退款", "kind": "refund_only", "detail": "自动化验收申请的整单单店铺退款"},
            ),
            where="POST buyer refund",
        )
        require(isinstance(refund_resp, dict), "refund response data must be object")
        refunding = self._order(a, order_no)
        eq(refunding.get("status"), "REFUNDING", "order status after refund request")
        refunds = refunding.get("refunds")
        require(isinstance(refunds, list) and refunds, "order detail missing refunds after request")
        refund = refunds[0]
        require(isinstance(refund, dict) and isinstance(refund.get("id"), int), "refund missing integer id")
        eq(refund.get("status"), "REQUESTED", "new refund status")
        eq(refund.get("amount_cents"), refunding.get("total_cents"), "refund amount must equal order total")
        duplicate = a.post(
            f"/api/orders/{urllib.parse.quote(order_no)}/refund",
            {"reason": "QA 重复售后", "kind": "refund_only"},
        )
        parse_error(duplicate, {409, 422}, where="duplicate active refund request")
        cross_shop = seller2.post(
            f"/api/seller/refunds/{refund['id']}/decision",
            {"action": "approve", "note": "QA 跨店越权尝试"},
        )
        parse_error(cross_shop, {404}, allowed_codes={"NOT_FOUND"}, where="cross-shop refund decision")
        parse_success(
            seller.post(
                f"/api/seller/refunds/{refund['id']}/decision",
                {"action": "approve", "note": "QA 验收同意退款"},
            ),
            where="seller refund approve",
        )
        final = self._order(a, order_no)
        eq(final.get("status"), "REFUNDED", "order status after seller approve")
        final_refunds = final.get("refunds") or []
        require(final_refunds and final_refunds[0].get("status") == "APPROVED", "refund status after approve")
        eq(self._offer_stock(offer.product_slug, offer.offer_id), stock_before, "approved refund must restore stock")
        restored = self._coupon_entry(a, coupon["code"])
        require(restored is not None, f"refunded coupon {coupon['code']} missing from mine")
        eq(restored["status"], "AVAILABLE", "approved refund must restore AVAILABLE coupon")

    def check_refund_reject_restores_state(self) -> None:
        a = self._need("a", "API-030")
        address = self._need("a_address", "API-035")
        offer = self._need("main_offer", "API-023")
        seller, _seller_user = self._ensure_demo_seller()
        self._clear_cart(a)
        parse_success(self._add_cart(a, offer), where="refund reject add item")
        stock_before = self._offer_stock(offer.product_slug, offer.offer_id)
        body = self._checkout_body(address_id=address["id"], key=self._key("refund-reject"))
        order_no, _ = self._create_order(a, body)
        self._pay(a, order_no)
        stock_paid = self._offer_stock(offer.product_slug, offer.offer_id)
        eq(stock_paid, stock_before - 1, "refund reject stock decrement")
        parse_success(
            a.post(
                f"/api/orders/{urllib.parse.quote(order_no)}/refund",
                {"reason": "QA 验收拒绝分支", "kind": "refund_only", "detail": "用于验证拒绝后恢复原状态"},
            ),
            where="POST refund reject branch",
        )
        refunding = self._order(a, order_no)
        eq(refunding.get("status"), "REFUNDING", "refund reject order must enter REFUNDING")
        refunds = refunding.get("refunds") or []
        require(refunds and isinstance(refunds[0].get("id"), int), "refund reject branch missing refund id")
        parse_success(
            seller.post(
                f"/api/seller/refunds/{refunds[0]['id']}/decision",
                {"action": "reject", "note": "QA 验收拒绝退款"},
            ),
            where="seller refund reject",
        )
        final = self._order(a, order_no)
        eq(final.get("status"), "PAID", "rejected refund must restore last state PAID")
        final_refunds = final.get("refunds") or []
        require(final_refunds and final_refunds[0].get("status") == "REJECTED", "refund status after reject")
        eq(self._offer_stock(offer.product_slug, offer.offer_id), stock_paid, "rejected refund must not restore stock")

    def check_multi_shop_refund_unsupported(self) -> None:
        a = self._need("a", "API-030")
        address = self._need("a_address", "API-035")
        offers = self._find_second_shop_offers()
        require(len({o.shop_id for o in offers}) == 2, "multi-shop fixture needs two distinct shop offers")
        self._clear_cart(a)
        for offer in offers:
            parse_success(self._add_cart(a, offer), where="multi-shop add item")
        quote = self._quote(a)
        body = self._checkout_body(address_id=address["id"], key=self._key("multi-shop"))
        order_no, _ = self._create_order(a, body)
        paid = self._pay(a, order_no)
        eq(paid.get("status"), "PAID", "multi-shop order status after pay")
        eq(paid.get("total_cents"), quote["total_cents"], "multi-shop order total must match quote")
        packages = paid.get("packages") or []
        eq(len(packages), 2, "multi-shop order must split into 2 packages")
        refund = a.post(
            f"/api/orders/{urllib.parse.quote(order_no)}/refund",
            {"reason": "QA 多店售后限制", "kind": "refund_only", "detail": "多店铺订单必须拒绝整单售后"},
        )
        parse_error(refund, {422}, allowed_codes={"AFTER_SALES_UNSUPPORTED"}, where="multi-shop refund")
        after = self._order(a, order_no)
        eq(after.get("status"), "PAID", "rejected multi-shop refund must not change order state")
        require(not after.get("refunds"), "rejected multi-shop refund must not create refund record")

    # ------------------------------------------------------------ price tamper
    def check_order_price_tamper(self) -> None:
        b = self._need("b", "API-010")
        offer = self._need("main_offer", "API-023")
        address = self._need("b_address", "API-035")
        self._clear_cart(b)
        parse_success(self._add_cart(b, offer), where="price tamper add item")
        quote = self._quote(b)
        key = self._key("tamper")
        body = self._checkout_body(
            address_id=address["id"],
            key=key,
            extra={
                "total_cents": 1,
                "subtotal_cents": 1,
                "discount_cents": 9_999_999,
                "items": [{"offer_id": offer.offer_id, "unit_price_cents": 1, "quantity": 1}],
            },
        )
        resp = b.post("/api/orders", body)
        if resp.status in (400, 422):
            parse_error(resp, {400, 422}, where="forged checkout fields rejected")
            cart = self._cart(b, "cart after rejected forged checkout")
            require(self._cart_has_offer(cart, offer.offer_id), "rejected forged checkout must leave cart intact")
            return
        data = parse_success(resp, where="order create with forged price fields")
        order_no = self._extract_order_no(data)
        created = self._order(b, order_no)
        eq(created.get("total_cents"), quote["total_cents"], "forged total_cents must be ignored; server quote wins")
        eq(created.get("subtotal_cents"), quote["subtotal_cents"], "forged subtotal must be ignored")
        parse_success(
            b.post(f"/api/orders/{urllib.parse.quote(order_no)}/cancel", {"reason": "QA 清理价格篡改验收订单"}),
            where="cancel forged-price order",
        )
        final = self._order(b, order_no)
        eq(final.get("status"), "CANCELLED", "forged-price order cleanup status")

    # ---------------------------------------------------------------- oversell
    def check_concurrent_oversell(self) -> None:
        a = self._need("a", "API-030")
        b = self._need("b", "API-010")
        offer = self._need("main_offer", "API-023")
        address_a = self._need("a_address", "API-035")
        address_b = self._need("b_address", "API-035")
        self._clear_cart(a)
        self._clear_cart(b)
        self._db_set_stock(offer.offer_id, 1)
        eq(self._offer_stock(offer.product_slug, offer.offer_id), 1, "QA stock fixture must be visible through catalog API")
        parse_success(self._add_cart(a, offer), where="oversell add buyer A")
        parse_success(self._add_cart(b, offer), where="oversell add buyer B")

        barrier = threading.Barrier(2)

        def fire(client: QAClient, address_id: int, label: str) -> tuple[str, int, str | None, str | None, Exception | None]:
            body = self._checkout_body(address_id=address_id, key=self._key("oversell-" + label))
            try:
                barrier.wait(timeout=10)
                resp = client.post("/api/orders", body)
                order_no = None
                if resp.status == 200 and isinstance(resp.json_body, dict):
                    try:
                        order_no = self._extract_order_no(resp.data() or {})
                    except Exception:
                        order_no = None
                return (label, resp.status, resp.error_code(), order_no, None)
            except Exception as exc:  # noqa: BLE001
                return (label, -1, None, None, exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(fire, a, address_a["id"], "a"),
                pool.submit(fire, b, address_b["id"], "b"),
            ]
            results = [f.result(timeout=30) for f in futures]
        request_errors = [(r[0], type(r[4]).__name__, str(r[4])) for r in results if r[4] is not None]
        require(not request_errors, f"oversell concurrent requests raised transport errors: {request_errors!r}")
        statuses = sorted(r[1] for r in results)
        require(
            statuses == [200, 409],
            f"oversell race expected exactly one 200 and one 409, got results={[(r[0], r[1], r[2]) for r in results]!r}",
        )
        losers = [r for r in results if r[1] == 409]
        winners = [r for r in results if r[1] == 200]
        eq(losers[0][2], "STOCK_CHANGED", "oversell losing request error.code")
        winner = winners[0]
        require(winner[3], f"oversell winning response missing order_no: {winner!r}")
        eq(self._offer_stock(offer.product_slug, offer.offer_id), 0, "stock must be exactly 0 after stock=1 oversell race")
        winner_client = a if winner[0] == "a" else b
        winner_order = self._order(winner_client, winner[3])
        eq(winner_order.get("status"), "PENDING_PAYMENT", "oversell winner order status")
        parse_success(
            winner_client.post(
                f"/api/orders/{urllib.parse.quote(winner[3])}/cancel",
                {"reason": "QA 清理超卖验收订单"},
            ),
            where="cancel oversell winner order",
        )
        eq(self._offer_stock(offer.product_slug, offer.offer_id), 1, "cancel of oversell winner must restore stock to 1")

    # ------------------------------------------------------------------- run
    def run_all(self) -> None:
        checks: list[tuple[str, str, Any]] = [
            ("API-001", "health and healthz", self.check_health),
            ("API-002", "guest session cookie/CSRF/security headers", self.check_guest_session_and_headers),
            ("API-003", "wrong CSRF rejected and detector live", self.check_bad_csrf_red),
            ("API-004", "missing CSRF rejected", self.check_missing_csrf_red),
            ("API-005", "unauthenticated protected endpoints -> 401", self.check_unauth_protected_endpoints),
            ("API-006", "CSRF rotates and stale token rejected", self.check_csrf_rotation),
            ("API-010", "register buyer B + cross-shop seller fixture", self.check_register_accounts),
            ("API-011", "buyer/guest seller API role isolation", self.check_role_isolation),
            ("API-020", "categories", self.check_categories),
            ("API-021", "home aggregate/personalization fields", self.check_home),
            ("API-022", "products pagination, facets, search, kind filter", self.check_products_search),
            ("API-023", "product detail and offer/sku fixture validation", self.check_product_details_and_offers),
            ("API-024", "reviews list/filter shapes", self.check_reviews_list),
            ("API-025", "search suggest + unknown slug 404", self.check_suggest_and_not_found),
            ("API-026", "browse event", self.check_browse_event),
            ("API-027", "favorites toggle/list ownership", self.check_favorites_owner),
            ("API-030", "guest cart add + cross-session cart IDOR", self.check_guest_cart_and_isolation),
            ("API-031", "cart negative quantities/invalid sku", self.check_cart_negative_controls),
            ("API-032", "mixed standard/group cart -> 422", self.check_cart_mixed_mode),
            ("API-033", "forged price fields cannot alter cart totals", self.check_cart_price_tamper),
            ("API-034", "one-click buyer login merges guest cart", self.check_buyer_login_cart_merge),
            ("API-035", "address CRUD validation + cross-user IDOR", self.check_address_create_validate_and_idor),
            ("API-036", "coupon list/claim/duplicate/expiry boundary", self.check_coupon_claim_and_expiry),
            ("API-037", "server-side quote, shipping and coupon math", self.check_quote_server_pricing),
            ("API-038", "idempotent order create, stock/coupon authority", self.check_order_create_idempotency),
            ("API-039", "order IDOR, state machine, demo-only payment", self.check_order_idor_and_pay),
            ("API-040", "seller isolation, ship, buyer tracking visibility", self.check_seller_ship_and_buyer_visibility),
            ("API-041", "buyer confirm + review body + aggregate + duplicate", self.check_review_and_aggregate),
            ("API-050", "cancel restores stock and coupon", self.check_cancel_restores_stock_coupon),
            ("API-051", "true group fill, unpaid not counted, full -> paid", self.check_group_full),
            ("API-052", "refund approve + cross-shop isolation + stock/coupon restore", self.check_refund_approve_and_isolation),
            ("API-053", "refund reject restores previous state without stock restore", self.check_refund_reject_restores_state),
            ("API-054", "multi-shop refund -> 422 AFTER_SALES_UNSUPPORTED", self.check_multi_shop_refund_unsupported),
            ("API-055", "order body price tampering ignored/rejected securely", self.check_order_price_tamper),
            ("API-060", "concurrent oversell race with stock=1", self.check_concurrent_oversell),
        ]
        for check_id, title, fn in checks:
            self.runner.check(check_id, title, fn)
        self.runner.add_extra("declared_checks", len(checks))
        self.runner.add_extra("declared_check_ids", [check_id for check_id, _title, _fn in checks])
        self.runner.add_extra(
            "fixture_notes",
            {
                "seller2": "registered through real /api/auth/register, then promoted to seller of an existing non-self shop via isolated QA SQLite DB",
                "oversell_stock": "offer.stock set to 1 directly in the isolated QA SQLite DB before two concurrent real HTTP order creates",
                "group_extra_buyer": "registered through real HTTP only when required_size == 3",
                "direct_db": "only non-empty data_dir below /hf3fs-jd/data/tmp/openshop was used; production was never accessed",
            },
        )


