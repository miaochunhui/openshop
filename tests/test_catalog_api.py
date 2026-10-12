#!/usr/bin/env python3
"""Real-HTTP acceptance tests for the OpenShop catalog/address seat.

The suite owns its server, binds 127.0.0.1:8861 by default and writes an
isolated SQLite database and server log under $TMPDIR/openshop-tests (override
with OPENSHOP_TMP_BASE or OPENSHOP_CATALOG_TEST_PORT). It never touches the
repository data directory.

    python3 -B tests/test_catalog_api.py

A fresh timestamped data dir is used on every run, so the suite is rerunnable.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("OPENSHOP_CATALOG_TEST_PORT", "8861"))
BASE_URL = f"http://127.0.0.1:{PORT}"
TEMP_BASE = Path(os.environ.get("OPENSHOP_TMP_BASE", str(Path(tempfile.gettempdir()) / "openshop-tests")))
WRITE_METHODS = {"POST", "PATCH", "DELETE", "PUT"}
_MISSING = object()

CARD_FIELDS = {
    "id",
    "slug",
    "title",
    "subtitle",
    "brand",
    "category",
    "cover",
    "cover_alt",
    "price_cents",
    "market_price_cents",
    "discount_percent",
    "offer_count",
    "offer_kinds",
    "rating",
    "review_count",
    "sold_count",
    "badges",
    "services",
    "delivery_label",
    "free_shipping",
    "saved_cents",
    "group",
}
REVIEW_FIELDS = {
    "id",
    "author",
    "rating",
    "title",
    "body",
    "tags",
    "verified",
    "helpful_count",
    "sku_label",
    "created_at",
    "mine",
}
ADDRESS_FIELDS = {
    "id",
    "label",
    "receiver",
    "phone",
    "province",
    "city",
    "district",
    "detail",
    "is_default",
}


def _url(path: str, **params: Any) -> str:
    cleaned = {key: value for key, value in params.items() if value is not None}
    if not cleaned:
        return path
    return path + "?" + urllib.parse.urlencode(cleaned)


def _error_of(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload["error"]
    return {}


def _short(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:700]
    except (TypeError, ValueError):
        return repr(value)[:700]


class ApiClient:
    """Cookie-jar HTTP client with explicit CSRF control."""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf = ""

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        csrf: Any = _MISSING,
        content_type: str = "application/json",
        timeout: float = 30,
    ) -> tuple[int, Any]:
        method = method.upper()
        headers = {"Accept": "application/json", "Connection": "close"}
        data: bytes | None = None
        if method in WRITE_METHODS:
            headers["Content-Type"] = content_type
            token = self.csrf if csrf is _MISSING else csrf
            if token:
                headers["X-OpenShop-CSRF"] = str(token)
            data = b"" if body is None else json.dumps(
                body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            BASE_URL + path, data=data, method=method, headers=headers
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                status = response.getcode()
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
        payload: Any = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"_raw": raw[:300].decode("utf-8", "replace")}
        return status, payload

    def session(self) -> dict[str, Any]:
        status, payload = self.request("GET", "/api/session")
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"session bootstrap failed status={status}")
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("csrf"):
            raise RuntimeError("session bootstrap returned no csrf")
        self.csrf = str(data["csrf"])
        return data

    def login(self, role: str) -> dict[str, Any]:
        self.session()
        status, payload = self.request("POST", "/api/auth/demo", {"role": role})
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(
                f"demo login failed role={role} status={status} "
                f"code={_error_of(payload).get('code')}"
            )
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("csrf"):
            raise RuntimeError("demo login returned no csrf")
        self.csrf = str(data["csrf"])
        return data


class CatalogAPITests(unittest.TestCase):
    proc: subprocess.Popen[bytes] | None = None
    log_handle: Any = None
    data_dir: Path | None = None
    log_path: Path | None = None
    check_count = 0

    @classmethod
    def setUpClass(cls) -> None:
        cls.check_count = 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + f"-{os.getpid()}"
        cls.data_dir = TEMP_BASE / "catalog-api" / stamp
        cls.log_path = TEMP_BASE / "logs" / f"catalog-api-{stamp}.log"
        cls.proc = None
        cls.log_handle = None
        try:
            cls.log_path.parent.mkdir(parents=True, exist_ok=True)
            cls.data_dir.mkdir(parents=True, exist_ok=False)
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", PORT))
            finally:
                probe.close()

            cls.log_handle = open(cls.log_path, "w", encoding="utf-8")
            cls.proc = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-B",
                    str(ROOT / "apps/storefront/server.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(PORT),
                    "--data-dir",
                    str(cls.data_dir),
                ],
                cwd=str(ROOT),
                stdout=cls.log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            time.sleep(0.35)
            if cls.proc.poll() is not None:
                raise RuntimeError(f"server_exited_{cls.proc.returncode}")

            deadline = time.monotonic() + 35
            healthy = False
            while time.monotonic() < deadline:
                if cls.proc.poll() is not None:
                    break
                try:
                    request = urllib.request.Request(
                        f"{BASE_URL}/api/health", headers={"Connection": "close"}
                    )
                    with urllib.request.urlopen(request, timeout=2) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    if health.get("ok") is True:
                        healthy = True
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            if not healthy or cls.proc.poll() is not None:
                raise RuntimeError("server_not_ready")
            print(
                f"PASS server_ready url={BASE_URL} data_dir={cls.data_dir} "
                f"log={cls.log_path}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"FAIL server_startup reason={type(exc).__name__} detail={exc} "
                f"log={cls.log_path}",
                flush=True,
            )
            if cls.log_handle is not None:
                try:
                    cls.log_handle.flush()
                except OSError:
                    pass
            if cls.log_path is not None and cls.log_path.is_file():
                try:
                    tail = cls.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
                except OSError:
                    tail = []
                if tail:
                    print("SERVER_LOG_TAIL_BEGIN", flush=True)
                    for line in tail:
                        print(line, flush=True)
                    print("SERVER_LOG_TAIL_END", flush=True)
            cls._stop_server()
            raise

    @classmethod
    def _stop_server(cls) -> None:
        proc = cls.proc
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
        if cls.log_handle is not None:
            try:
                cls.log_handle.close()
            except OSError:
                pass
            cls.log_handle = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stop_server()
        print(
            f"TEARDOWN server_stopped data_dir={cls.data_dir} log={cls.log_path}",
            flush=True,
        )

    def _check(self, condition: bool, label: str, detail: str = "") -> None:
        if not condition:
            print(f"FAIL {label}" + (f" detail={detail}" if detail else ""), flush=True)
            self.fail(label + (f": {detail}" if detail else ""))
        type(self).check_count += 1

    def _data(self, result: tuple[int, Any], label: str) -> Any:
        status, payload = result
        error = _error_of(payload)
        self._check(
            status == 200,
            f"{label}_status",
            f"status={status} code={error.get('code')}",
        )
        self._check(
            isinstance(payload, dict) and "data" in payload,
            f"{label}_envelope",
            _short(payload),
        )
        assert isinstance(payload, dict)
        meta = payload.get("meta") or {}
        self._check(
            meta.get("currency") == "CNY" and meta.get("demo") is True,
            f"{label}_meta",
            _short(meta),
        )
        return payload.get("data")

    def _negative_line(
        self,
        result: tuple[int, Any],
        expected_status: int,
        expected_code: str,
        label: str,
    ) -> dict[str, Any]:
        status, payload = result
        error = _error_of(payload)
        actual_code = error.get("code")
        ok = status == expected_status and actual_code == expected_code
        if not ok:
            print(
                f"FAIL {label} expected_status={expected_status} "
                f"expected_code={expected_code} actual_status={status} "
                f"actual_code={actual_code} message={error.get('message')}",
                flush=True,
            )
            self.fail(f"{label}: expected {expected_status}/{expected_code}")
        type(self).check_count += 1
        print(f"PASS {label} status={status} code={expected_code}", flush=True)
        return error

    def _all_products(self, client: ApiClient, **filters: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        first: dict[str, Any] | None = None
        collected: list[dict[str, Any]] = []
        page = 1
        while True:
            query = dict(filters)
            query["page"] = page
            query["page_size"] = 24
            status, payload = client.request("GET", _url("/api/products", **query))
            self._check(
                status == 200,
                f"products_page_status_{page}",
                f"status={status} code={_error_of(payload).get('code')}",
            )
            data = self._data((status, payload), f"products_page_{page}")
            self._check(isinstance(data, dict), f"products_page_{page}_shape", _short(data))
            if not isinstance(data, dict):
                self.fail("products page data must be an object")
            if first is None:
                first = data
            collected.extend(data.get("items") or [])
            pages = int(data.get("pages") or 0)
            if not data.get("items") or page >= pages:
                break
            page += 1
            self._check(page <= 100, "products_pagination_guard", f"page={page}")
        assert first is not None
        return first, collected

    @staticmethod
    def _address_body(label: str, receiver: str, *, is_default: bool = False) -> dict[str, Any]:
        return {
            "label": label,
            "receiver": receiver,
            "phone": "13900001234",
            "province": "上海市",
            "city": "上海市",
            "district": "黄浦区",
            "detail": "验收路 128 号 2 栋 301 室",
            "is_default": is_default,
        }

    # ------------------------------------------------------------------
    # Reads: categories, home and product cards
    # ------------------------------------------------------------------

    def test_01_home_categories_and_cards(self) -> None:
        client = ApiClient()
        session = client.session()
        self._check(
            session.get("is_guest") is True and session.get("user") is None,
            "guest_session",
            _short(session),
        )

        categories = self._data(
            client.request("GET", "/api/categories"), "categories"
        )
        self._check(
            isinstance(categories, list) and bool(categories),
            "categories_shape",
            _short(categories),
        )
        if not isinstance(categories, list):
            self.fail("categories data must be a list")
        categories_by_slug: dict[str, dict[str, Any]] = {}
        for category in categories:
            self._check(
                isinstance(category, dict)
                and {"slug", "name", "subtitle", "icon", "count"}.issubset(category),
                "category_fields",
                _short(category),
            )
            if not isinstance(category, dict):
                continue
            self._check(
                isinstance(category.get("count"), int) and category["count"] >= 0,
                "category_real_count",
                _short(category),
            )
            categories_by_slug[str(category.get("slug"))] = category

        products_page, cards = self._all_products(client)
        total = int(products_page.get("total") or 0)
        self._check(
            total > 0 and total == len(cards),
            "products_total_matches_rows",
            f"total={total} collected={len(cards)}",
        )
        self._check(
            products_page.get("sort") == "featured",
            "products_default_sort",
            str(products_page.get("sort")),
        )
        for card in cards:
            self._check(
                CARD_FIELDS.issubset(card) and isinstance(card.get("category"), dict),
                "card_contract_fields",
                _short(card),
            )
        for category in categories:
            derived = sum(
                1
                for card in cards
                if (card.get("category") or {}).get("slug") == category.get("slug")
            )
            self._check(
                int(category.get("count") or 0) == derived,
                "category_count_from_db",
                f"slug={category.get('slug')} api={category.get('count')} derived={derived}",
            )
        self._check(
            sum(int(category.get("count") or 0) for category in categories) == total,
            "category_counts_sum_to_products",
            f"total={total}",
        )

        home = self._data(client.request("GET", "/api/home"), "home")
        self._check(isinstance(home, dict), "home_shape", _short(home))
        if not isinstance(home, dict):
            self.fail("home data must be an object")
        required_home = {
            "categories",
            "stats",
            "hero",
            "hero_side",
            "shelves",
            "deals",
            "group_picks",
            "new_arrivals",
            "recently_viewed",
            "recommended",
            "trust",
        }
        self._check(
            required_home.issubset(home),
            "home_required_fields",
            ",".join(sorted(required_home - set(home))),
        )
        stats = home.get("stats") or {}
        self._check(
            int(stats.get("products") or -1) == total,
            "home_stats_products_db",
            f"home={stats.get('products')} products={total}",
        )
        self._check(
            int(stats.get("reviews") or -1)
            == sum(int(card.get("review_count") or 0) for card in cards),
            "home_stats_reviews_db",
            f"home={stats.get('reviews')}",
        )
        self._check(int(stats.get("shops") or 0) > 0, "home_stats_shops_db", _short(stats))
        home_categories = home.get("categories") or []
        self._check(
            isinstance(home_categories, list)
            and {
                str(category.get("slug")): int(category.get("count") or 0)
                for category in home_categories
                if isinstance(category, dict)
            }
            == {
                str(category.get("slug")): int(category.get("count") or 0)
                for category in categories
            },
            "home_categories_match_api",
        )

        hero = home.get("hero")
        self._check(
            isinstance(hero, dict)
            and {
                "product_slug",
                "image",
                "eyebrow",
                "title",
                "body",
                "price_cents",
                "badge",
            }.issubset(hero),
            "home_hero_fields",
            _short(hero),
        )
        hero_detail = self._data(
            client.request(
                "GET",
                "/api/products/" + urllib.parse.quote(str(hero.get("product_slug")), safe=""),
            ),
            "hero_detail",
        )
        self._check(
            isinstance(hero_detail, dict)
            and int(hero_detail.get("price_cents") or -1) == int(hero.get("price_cents") or -2),
            "home_hero_price_db",
            f"hero={hero.get('price_cents')} detail={hero_detail.get('price_cents') if isinstance(hero_detail, dict) else None}",
        )

        active_slugs = {str(card.get("slug")) for card in cards}
        for section in (
            "hero_side",
            "deals",
            "group_picks",
            "new_arrivals",
            "recently_viewed",
            "recommended",
        ):
            section_items = home.get(section)
            self._check(
                isinstance(section_items, list),
                f"home_{section}_list",
                _short(section_items),
            )
            if isinstance(section_items, list):
                for item in section_items:
                    self._check(
                        isinstance(item, dict)
                        and CARD_FIELDS.issubset(item)
                        and str(item.get("slug")) in active_slugs,
                        f"home_{section}_card_from_db",
                        _short(item),
                    )

        shelves = home.get("shelves")
        self._check(
            isinstance(shelves, list) and 2 <= len(shelves) <= 3,
            "home_shelves_count",
            _short(shelves),
        )
        if isinstance(shelves, list):
            for shelf in shelves:
                self._check(
                    isinstance(shelf, dict)
                    and {"key", "title", "subtitle", "href", "items"}.issubset(shelf)
                    and isinstance(shelf.get("items"), list)
                    and bool(shelf.get("items")),
                    "home_shelf_shape",
                    _short(shelf),
                )
        self._check(
            isinstance(home.get("trust"), list) and bool(home.get("trust")),
            "home_trust_shape",
            _short(home.get("trust")),
        )
        print(
            f"PASS home_categories_products stats_products={stats.get('products')} "
            f"categories={len(categories)} reviews={stats.get('reviews')} "
            f"sections=6 shelves={len(shelves) if isinstance(shelves, list) else 0}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Product query surface and empty/negative-safe behaviour
    # ------------------------------------------------------------------

    def test_02_products_filters_sort_pagination_and_empty(self) -> None:
        client = ApiClient()
        client.session()
        base_page, cards = self._all_products(client)
        total = int(base_page.get("total") or 0)
        self._check(total > 0 and len(cards) == total, "products_base_rows", f"total={total}")
        target = cards[0]
        target_slug = str(target["slug"])

        search_terms: list[tuple[str, str]] = [
            ("title", str(target["title"])),
            ("subtitle", str(target["subtitle"])),
            ("brand", str(target["brand"])),
            ("category", str(target["category"]["name"])),
        ]
        if target.get("tags"):
            search_terms.append(("tag", str(target["tags"][0])))
        for field_name, term in search_terms:
            if not term:
                continue
            result_page, result_items = self._all_products(client, q=term)
            self._check(
                result_page.get("total", 0) > 0
                and any(str(item.get("slug")) == target_slug for item in result_items),
                f"search_by_{field_name}",
                f"q={term} total={result_page.get('total')}",
            )
        spaced = f"  {target['title']}  "
        spaced_page, spaced_items = self._all_products(client, q=spaced)
        self._check(
            spaced_page.get("query") == " ".join(str(target["title"]).split())
            and any(str(item.get("slug")) == target_slug for item in spaced_items),
            "search_whitespace_safe",
            f"query={spaced_page.get('query')}",
        )

        category_slug = str(target["category"]["slug"])
        category_page, category_items = self._all_products(client, category=category_slug)
        self._check(
            int(category_page.get("total") or 0) > 0
            and all(str(item["category"]["slug"]) == category_slug for item in category_items),
            "category_filter",
            f"category={category_slug} total={category_page.get('total')}",
        )
        category_facet = next(
            (
                int(entry.get("count") or 0)
                for entry in (base_page.get("facets") or {}).get("categories") or []
                if isinstance(entry, dict) and entry.get("slug") == category_slug
            ),
            -1,
        )
        self._check(
            category_facet == int(category_page.get("total") or 0),
            "facet_categories_db",
            f"facet={category_facet} category_total={category_page.get('total')}",
        )

        kind = str((target.get("offer_kinds") or ["self"])[0])
        kind_page, kind_items = self._all_products(client, kind=kind)
        self._check(
            int(kind_page.get("total") or 0) > 0
            and all(kind in (item.get("offer_kinds") or []) for item in kind_items),
            "kind_filter",
            f"kind={kind} total={kind_page.get('total')}",
        )
        kind_facet = next(
            (
                int(entry.get("count") or 0)
                for entry in (base_page.get("facets") or {}).get("kinds") or []
                if isinstance(entry, dict) and entry.get("kind") == kind
            ),
            -1,
        )
        self._check(
            kind_facet == int(kind_page.get("total") or 0),
            "facet_kinds_db",
            f"facet={kind_facet} kind_total={kind_page.get('total')}",
        )

        prices = [int(card["price_cents"]) for card in cards]
        facets = base_page.get("facets") or {}
        self._check(
            int(facets.get("price_min_cents") or -1) == min(prices)
            and int(facets.get("price_max_cents") or -1) == max(prices),
            "facet_price_bounds_db",
            f"facets={facets.get('price_min_cents')}..{facets.get('price_max_cents')} actual={min(prices)}..{max(prices)}",
        )
        low_price = min(prices)
        price_page, price_items = self._all_products(
            client, min_price_cents=low_price, max_price_cents=low_price
        )
        self._check(
            int(price_page.get("total") or 0) > 0
            and all(int(item["price_cents"]) == low_price for item in price_items)
            and int(price_page.get("total") or 0)
            == sum(1 for card in cards if int(card["price_cents"]) == low_price),
            "price_range_filter",
            f"price={low_price} total={price_page.get('total')}",
        )

        top_rating = max(float(card["rating"]) for card in cards)
        rating_page, rating_items = self._all_products(
            client, min_rating=f"{top_rating:.1f}"
        )
        self._check(
            int(rating_page.get("total") or 0) > 0
            and all(float(item["rating"]) + 0.001 >= top_rating for item in rating_items),
            "min_rating_filter",
            f"min_rating={top_rating} total={rating_page.get('total')}",
        )

        for sort_name in ("featured", "newest", "price_asc", "price_desc", "rating", "sales"):
            sort_page, sort_items = self._all_products(client, sort=sort_name)
            self._check(
                len(sort_items) == total and sort_page.get("sort") == sort_name,
                f"sort_{sort_name}_shape",
                f"total={sort_page.get('total')} returned={len(sort_items)}",
            )
            if sort_name == "featured":
                flags = [bool(card.get("featured")) for card in sort_items]
                ordered = flags == sorted(flags, reverse=True)
                values = flags
            elif sort_name == "newest":
                stamps = [(str(card.get("created_at")), int(card.get("id") or 0)) for card in sort_items]
                ordered = stamps == sorted(stamps, reverse=True)
                values = stamps
            elif sort_name == "price_asc":
                values = [int(card["price_cents"]) for card in sort_items]
                ordered = values == sorted(values)
            elif sort_name == "price_desc":
                values = [int(card["price_cents"]) for card in sort_items]
                ordered = values == sorted(values, reverse=True)
            elif sort_name == "rating":
                values = [float(card["rating"]) for card in sort_items]
                ordered = values == sorted(values, reverse=True)
            else:
                values = [int(card["sold_count"]) for card in sort_items]
                ordered = values == sorted(values, reverse=True)
            self._check(ordered, f"sort_{sort_name}_order", _short(values[:12]))

        if total > 1:
            first_status, first_payload = client.request(
                "GET", _url("/api/products", page=1, page_size=1)
            )
            p1 = self._data((first_status, first_payload), "page1")
            p1_items = p1.get("items") if isinstance(p1, dict) else []
            second_status, second_payload = client.request(
                "GET", _url("/api/products", page=2, page_size=1)
            )
            p2 = self._data((second_status, second_payload), "page2")
            p2_items = p2.get("items") if isinstance(p2, dict) else []
            self._check(
                isinstance(p1, dict)
                and isinstance(p2, dict)
                and p1.get("page_size") == 1
                and p1.get("pages") == total
                and isinstance(p1_items, list)
                and len(p1_items) == 1
                and isinstance(p2_items, list)
                and len(p2_items) == 1
                and p1_items[0].get("id") != p2_items[0].get("id"),
                "pagination_pages",
                f"p1={_short(p1)} p2={_short(p2)}",
            )
            beyond_status, beyond_payload = client.request(
                "GET", _url("/api/products", page=total + 1, page_size=1)
            )
            beyond = self._data((beyond_status, beyond_payload), "page_beyond")
            self._check(
                isinstance(beyond, dict)
                and beyond.get("items") == []
                and beyond.get("total") == total,
                "pagination_beyond_end",
                _short(beyond),
            )

        empty_status, empty_payload = client.request(
            "GET", _url("/api/products", q="no_such_product_7f91c3", page_size=5)
        )
        empty = self._data((empty_status, empty_payload), "empty_products")
        self._check(
            isinstance(empty, dict)
            and empty.get("total") == 0
            and empty.get("items") == []
            and empty.get("pages") == 0
            and empty.get("facets")
            == {"categories": [], "kinds": [], "price_min_cents": 0, "price_max_cents": 0},
            "empty_search_shape",
            _short(empty),
        )

        for weird in ("%", "_", "' OR 1=1 --"):
            status, payload = client.request(
                "GET", _url("/api/products", q=weird, page_size=24)
            )
            data = self._data((status, payload), "like_escape")
            self._check(
                isinstance(data, dict) and int(data.get("total") or 0) < total,
                "like_special_chars_not_wildcards",
                f"q={weird!r} total={data.get('total') if isinstance(data, dict) else None}",
            )

        print(
            f"PASS products_filters_sort_pagination_empty total={total} "
            f"search_fields={len(search_terms)} sorts=6 queries_checked=3",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Detail, reviews, suggestions and session view events
    # ------------------------------------------------------------------

    def test_03_detail_reviews_suggest_and_view(self) -> None:
        client = ApiClient()
        client.session()
        _, cards = self._all_products(client)
        self._check(bool(cards), "detail_has_product", "catalog returned no cards")
        target = cards[0]
        slug = str(target["slug"])
        encoded = urllib.parse.quote(slug, safe="")
        detail = self._data(
            client.request("GET", f"/api/products/{encoded}"), "product_detail"
        )
        self._check(isinstance(detail, dict), "product_detail_shape", _short(detail))
        if not isinstance(detail, dict):
            self.fail("product detail data must be an object")
        detail_required = CARD_FIELDS | {
            "description",
            "story",
            "images",
            "tags",
            "highlights",
            "specs",
            "detail_sections",
            "faq",
            "offers",
            "skus",
            "reviews",
            "related",
            "created_at",
        }
        self._check(
            detail_required.issubset(detail),
            "product_detail_fields",
            ",".join(sorted(detail_required - set(detail))),
        )
        self._check(
            int(detail.get("price_cents") or -1) == int(target.get("price_cents") or -2),
            "detail_price_matches_list",
            f"detail={detail.get('price_cents')} list={target.get('price_cents')}",
        )
        images = detail.get("images")
        offers = detail.get("offers")
        skus = detail.get("skus")
        self._check(isinstance(images, list) and bool(images), "detail_images", _short(images))
        self._check(
            isinstance(offers, list) and bool(offers), "detail_offers", _short(offers)
        )
        if isinstance(offers, list):
            for offer in offers:
                self._check(
                    isinstance(offer, dict)
                    and {"id", "kind", "kind_label", "name", "shop", "price_cents", "stock", "group", "is_primary"}.issubset(offer),
                    "detail_offer_fields",
                    _short(offer),
                )
        self._check(
            isinstance(skus, list) and bool(skus), "detail_skus", _short(skus)
        )
        if isinstance(skus, list):
            for sku in skus:
                self._check(
                    isinstance(sku, dict)
                    and {"id", "name", "options", "price_delta_cents"}.issubset(sku),
                    "detail_sku_fields",
                    _short(sku),
                )
        detail_reviews = detail.get("reviews")
        self._check(
            isinstance(detail_reviews, dict)
            and isinstance(detail_reviews.get("summary"), dict)
            and isinstance(detail_reviews.get("items"), list),
            "detail_review_summary",
            _short(detail_reviews),
        )
        if isinstance(detail_reviews, dict):
            for review in detail_reviews.get("items") or []:
                self._check(
                    isinstance(review, dict) and REVIEW_FIELDS.issubset(review),
                    "detail_review_fields",
                    _short(review),
                )
        self._check(
            isinstance(detail.get("related"), list),
            "detail_related_list",
            _short(detail.get("related")),
        )

        review_path = f"/api/products/{encoded}/reviews"
        review_page = self._data(
            client.request("GET", _url(review_path, page_size=2, page=1)),
            "reviews_page1",
        )
        self._check(isinstance(review_page, dict), "reviews_shape", _short(review_page))
        if not isinstance(review_page, dict):
            self.fail("reviews data must be an object")
        summary = review_page.get("summary") or {}
        self._check(
            isinstance(summary, dict)
            and {"rating", "count", "distribution", "tags"}.issubset(summary),
            "reviews_summary_fields",
            _short(summary),
        )
        self._check(
            isinstance(summary.get("distribution"), list)
            and len(summary["distribution"]) == 5,
            "reviews_distribution_five_stars",
            _short(summary.get("distribution")),
        )
        review_total = int(review_page.get("total") or 0)
        self._check(review_total > 0, "reviews_total_positive", f"total={review_total}")
        self._check(
            int(summary.get("count") or -1) == review_total,
            "reviews_summary_count_matches_total",
            f"summary={summary.get('count')} total={review_total}",
        )
        self._check(
            review_page.get("page_size") == 2
            and review_page.get("page") == 1
            and review_page.get("pages") == (review_total + 1) // 2,
            "reviews_pagination_meta",
            _short({k: review_page.get(k) for k in ("total", "page", "page_size", "pages")}),
        )
        review_items = review_page.get("items")
        self._check(
            isinstance(review_items, list) and len(review_items) <= 2,
            "reviews_page_items",
            _short(review_items),
        )
        for review in review_items or []:
            self._check(
                isinstance(review, dict)
                and REVIEW_FIELDS.issubset(review)
                and review.get("mine") is False,
                "reviews_item_contract",
                _short(review),
            )
        facets = review_page.get("facets") or {}
        self._check(
            isinstance(facets.get("tags"), list)
            and all(
                isinstance(tag, dict) and {"label", "count"}.issubset(tag)
                for tag in facets.get("tags") or []
            ),
            "reviews_tag_facets",
            _short(facets),
        )
        if int(review_page.get("pages") or 0) > 1:
            second_page = self._data(
                client.request("GET", _url(review_path, page_size=2, page=2)),
                "reviews_page2",
            )
            second_ids = {
                item.get("id") for item in (second_page.get("items") or [])
            } if isinstance(second_page, dict) else set()
            first_ids = {
                item.get("id") for item in (review_items or [])
            }
            self._check(
                isinstance(second_page, dict)
                and second_page.get("page") == 2
                and bool(second_ids)
                and not (first_ids & second_ids),
                "reviews_page2_items",
                _short(second_page),
            )

        all_reviews = self._data(
            client.request("GET", _url(review_path, page_size=24, page=1)),
            "reviews_all",
        )
        all_items = all_reviews.get("items") if isinstance(all_reviews, dict) else []
        self._check(bool(all_items), "reviews_available_for_filter", _short(all_reviews))
        if all_items:
            rating_value = int(all_items[0]["rating"])
            filtered_reviews = self._data(
                client.request(
                    "GET",
                    _url(review_path, rating=rating_value, page_size=24, page=1),
                ),
                "reviews_rating_filter",
            )
            filtered_items = (
                filtered_reviews.get("items") if isinstance(filtered_reviews, dict) else []
            )
            self._check(
                isinstance(filtered_reviews, dict)
                and int(filtered_reviews.get("total") or 0) > 0
                and all(int(item["rating"]) == rating_value for item in filtered_items or []),
                "reviews_rating_filter",
                f"rating={rating_value} total={filtered_reviews.get('total') if isinstance(filtered_reviews, dict) else None}",
            )
        for sort_name, field_name in (("helpful", "helpful_count"), ("rating_desc", "rating")):
            sorted_reviews = self._data(
                client.request(
                    "GET",
                    _url(review_path, sort=sort_name, page_size=24, page=1),
                ),
                f"reviews_sort_{sort_name}",
            )
            values = [
                int(item[field_name])
                for item in (sorted_reviews.get("items") or [])
            ] if isinstance(sorted_reviews, dict) else []
            self._check(
                values == sorted(values, reverse=True),
                f"reviews_sort_{sort_name}",
                _short(values),
            )

        suggest = self._data(
            client.request(
                "GET", _url("/api/search/suggest", q=str(target["title"]))
            ),
            "suggest",
        )
        self._check(
            isinstance(suggest, dict)
            and {"suggestions", "products", "hot"}.issubset(suggest),
            "suggest_fields",
            _short(suggest),
        )
        if not isinstance(suggest, dict):
            self.fail("suggest data must be an object")
        self._check(
            isinstance(suggest.get("suggestions"), list)
            and bool(suggest.get("suggestions")),
            "suggest_terms",
            _short(suggest.get("suggestions")),
        )
        for suggestion in suggest.get("suggestions") or []:
            self._check(
                isinstance(suggestion, dict)
                and {"text", "kind"}.issubset(suggestion)
                and isinstance(suggestion.get("text"), str)
                and bool(suggestion.get("text")),
                "suggest_term_shape",
                _short(suggestion),
            )
        suggest_products = suggest.get("products")
        self._check(
            isinstance(suggest_products, list) and bool(suggest_products),
            "suggest_products",
            _short(suggest_products),
        )
        for product in suggest_products or []:
            self._check(
                isinstance(product, dict)
                and {"slug", "title", "cover"}.issubset(product),
                "suggest_product_shape",
                _short(product),
            )
        self._check(
            isinstance(suggest.get("hot"), list)
            and bool(suggest.get("hot"))
            and all(isinstance(term, str) and term.strip() for term in suggest.get("hot") or []),
            "suggest_hot_db_terms",
            _short(suggest.get("hot")),
        )

        before_home = self._data(client.request("GET", "/api/home"), "home_before_view")
        self._check(
            isinstance(before_home, dict) and before_home.get("recently_viewed") == [],
            "recently_viewed_initial_empty",
            _short(before_home.get("recently_viewed") if isinstance(before_home, dict) else None),
        )
        view_data = self._data(
            client.request(
                "POST", "/api/events/view", {"product_slug": slug}
            ),
            "view_event",
        )
        self._check(
            isinstance(view_data, dict) and view_data.get("recorded") is True,
            "view_event_recorded",
            _short(view_data),
        )
        after_home = self._data(client.request("GET", "/api/home"), "home_after_view")
        recently = after_home.get("recently_viewed") if isinstance(after_home, dict) else None
        self._check(
            isinstance(recently, list)
            and any(
                isinstance(item, dict) and item.get("slug") == slug
                for item in recently
            ),
            "recently_viewed_session_scoped",
            _short(recently),
        )
        print(
            f"PASS detail_reviews_suggest_view review_total={review_total} "
            f"offers={len(offers) if isinstance(offers, list) else 0} "
            f"suggest_products={len(suggest_products) if isinstance(suggest_products, list) else 0} "
            f"recent_views={len(recently) if isinstance(recently, list) else 0}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------

    def test_04_favorites_require_login_and_toggle(self) -> None:
        guest = ApiClient()
        guest.session()
        self._negative_line(
            guest.request("GET", "/api/favorites"),
            401,
            "AUTH_REQUIRED",
            "negative_favorites_guest_get",
        )
        _, guest_products = self._all_products(guest)
        guest_target = str(guest_products[0]["slug"])
        self._negative_line(
            guest.request(
                "POST",
                "/api/favorites/"
                + urllib.parse.quote(guest_target, safe="")
                + "/toggle",
            ),
            401,
            "AUTH_REQUIRED",
            "negative_favorites_guest_toggle",
        )

        buyer = ApiClient()
        buyer.login("buyer")
        favorites_before = self._data(
            buyer.request("GET", "/api/favorites"), "favorites_before"
        )
        self._check(
            isinstance(favorites_before, dict)
            and isinstance(favorites_before.get("items"), list)
            and isinstance(favorites_before.get("total"), int)
            and int(favorites_before.get("total") or 0) == len(favorites_before.get("items") or []),
            "favorites_shape",
            _short(favorites_before),
        )
        before_items = favorites_before.get("items") or []
        for item in before_items:
            self._check(
                isinstance(item, dict) and CARD_FIELDS.issubset(item),
                "favorites_card_shape",
                _short(item),
            )
        before_total = int(favorites_before.get("total") or 0)
        liked_slugs = {str(item.get("slug")) for item in before_items}
        _, products = self._all_products(buyer)
        self._check(bool(products), "favorites_catalog_nonempty", "no products to favorite")
        unliked = [card for card in products if str(card["slug"]) not in liked_slugs]
        if unliked:
            target = unliked[0]
            expected_after_toggle = True
        else:
            target = before_items[0]
            expected_after_toggle = False
        target_slug = str(target["slug"])
        toggle_path = "/api/favorites/" + urllib.parse.quote(target_slug, safe="") + "/toggle"

        self._negative_line(
            buyer.request("POST", toggle_path, csrf=None),
            403,
            "CSRF_FAILED",
            "negative_favorites_missing_csrf",
        )
        toggled = self._data(buyer.request("POST", toggle_path), "favorite_toggle_on")
        self._check(
            isinstance(toggled, dict)
            and toggled.get("favorited") is expected_after_toggle
            and int(toggled.get("total") or -1) == before_total + (1 if expected_after_toggle else -1),
            "favorite_toggle_first",
            _short(toggled),
        )
        favorites_after = self._data(
            buyer.request("GET", "/api/favorites"), "favorites_after"
        )
        after_items = favorites_after.get("items") or []
        after_slugs = {str(item.get("slug")) for item in after_items}
        self._check(
            isinstance(favorites_after, dict)
            and (target_slug in after_slugs) is expected_after_toggle
            and int(favorites_after.get("total") or -1)
            == before_total + (1 if expected_after_toggle else -1),
            "favorite_list_after_toggle",
            _short(favorites_after),
        )

        toggled_back = self._data(buyer.request("POST", toggle_path), "favorite_toggle_off")
        self._check(
            isinstance(toggled_back, dict)
            and toggled_back.get("favorited") is (not expected_after_toggle)
            and int(toggled_back.get("total") or -1) == before_total,
            "favorite_toggle_back",
            _short(toggled_back),
        )
        favorites_final = self._data(
            buyer.request("GET", "/api/favorites"), "favorites_final"
        )
        final_slugs = {
            str(item.get("slug")) for item in (favorites_final.get("items") or [])
        } if isinstance(favorites_final, dict) else set()
        self._check(
            int(favorites_final.get("total") or -1) == before_total
            and ((target_slug in final_slugs) is (target_slug in liked_slugs)),
            "favorite_toggle_restores_state",
            _short(favorites_final),
        )
        print(
            f"PASS favorites_login_toggle baseline={before_total} target={target_slug}",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Address CRUD, ownership and atomic default
    # ------------------------------------------------------------------

    def test_05_addresses_crud_owner_and_default(self) -> None:
        buyer = ApiClient()
        buyer.login("buyer")
        before = self._data(buyer.request("GET", "/api/addresses"), "addresses_before")
        self._check(
            isinstance(before, dict)
            and isinstance(before.get("items"), list)
            and "default_id" in before,
            "addresses_shape",
            _short(before),
        )
        if not isinstance(before, dict):
            self.fail("addresses data must be an object")
        before_items = before.get("items") or []
        baseline_count = len(before_items)
        baseline_ids = {int(item["id"]) for item in before_items}
        baseline_default = before.get("default_id")
        for item in before_items:
            self._check(
                isinstance(item, dict) and ADDRESS_FIELDS.issubset(item),
                "seeded_address_fields",
                _short(item),
            )

        first_body = self._address_body("验收一", "验收买家甲", is_default=False)
        first_body.pop("is_default", None)
        first_result = self._data(
            buyer.request("POST", "/api/addresses", first_body),
            "address_create_one",
        )
        self._check(
            isinstance(first_result, dict)
            and ADDRESS_FIELDS.issubset(first_result)
            and first_result.get("is_default") is False,
            "address_create_one",
            _short(first_result),
        )
        if not isinstance(first_result, dict):
            self.fail("created address must be an object")
        first_id = int(first_result["id"])
        self._check(first_id not in baseline_ids, "address_create_gets_new_id", str(first_id))

        after_one = self._data(buyer.request("GET", "/api/addresses"), "addresses_after_one")
        if not isinstance(after_one, dict):
            self.fail("address list must be an object")
        self._check(
            len(after_one.get("items") or []) == baseline_count + 1
            and any(int(item.get("id") or 0) == first_id for item in after_one.get("items") or []),
            "address_create_visible_in_list",
            _short(after_one),
        )

        second_result = self._data(
            buyer.request(
                "POST",
                "/api/addresses",
                self._address_body("验收二", "验收买家乙", is_default=True),
            ),
            "address_create_two_default",
        )
        self._check(
            isinstance(second_result, dict)
            and second_result.get("is_default") is True,
            "address_create_two_default",
            _short(second_result),
        )
        if not isinstance(second_result, dict):
            self.fail("second address must be an object")
        second_id = int(second_result["id"])
        self._check(
            int(second_id) != int(first_id),
            "address_ids_distinct",
            f"first={first_id} second={second_id}",
        )
        after_two = self._data(buyer.request("GET", "/api/addresses"), "addresses_after_two")
        if not isinstance(after_two, dict):
            self.fail("address list must be an object")
        after_two_items = after_two.get("items") or []
        self._check(
            len(after_two_items) == baseline_count + 2
            and [int(item["id"]) for item in after_two_items if item.get("is_default")]
            == [second_id]
            and after_two.get("default_id") == second_id,
            "address_default_singleton_after_create",
            _short(after_two),
        )

        patched = self._data(
            buyer.request(
                "PATCH",
                f"/api/addresses/{first_id}",
                {"label": "公司", "receiver": "验收买家甲", "is_default": True},
            ),
            "address_patch_default",
        )
        self._check(
            isinstance(patched, dict)
            and patched.get("label") == "公司"
            and patched.get("receiver") == "验收买家甲"
            and patched.get("is_default") is True
            and int(patched.get("id") or 0) == first_id,
            "address_patch_fields",
            _short(patched),
        )
        after_patch = self._data(
            buyer.request("GET", "/api/addresses"), "addresses_after_patch"
        )
        if not isinstance(after_patch, dict):
            self.fail("address list must be an object")
        after_patch_items = after_patch.get("items") or []
        self._check(
            [int(item["id"]) for item in after_patch_items if item.get("is_default")]
            == [first_id]
            and after_patch.get("default_id") == first_id,
            "address_default_exclusive_after_patch",
            _short(after_patch),
        )

        cleared = self._data(
            buyer.request("PATCH", f"/api/addresses/{first_id}", {"is_default": False}),
            "address_unset_default",
        )
        self._check(
            isinstance(cleared, dict) and cleared.get("is_default") is False,
            "address_unset_default",
            _short(cleared),
        )
        after_unset = self._data(
            buyer.request("GET", "/api/addresses"), "addresses_after_unset"
        )
        if not isinstance(after_unset, dict):
            self.fail("address list must be an object")
        self._check(
            after_unset.get("default_id") is None
            and all(item.get("is_default") is False for item in after_unset.get("items") or []),
            "address_default_null_when_none",
            _short(after_unset),
        )

        seller = ApiClient()
        seller.login("seller")
        seller_addresses = self._data(
            seller.request("GET", "/api/addresses"), "seller_addresses"
        )
        seller_ids = {
            int(item["id"])
            for item in (seller_addresses.get("items") or [])
        } if isinstance(seller_addresses, dict) else set()
        self._check(
            isinstance(seller_addresses, dict) and not (seller_ids & (baseline_ids | {first_id, second_id})),
            "address_list_owner_scoped",
            _short(seller_addresses),
        )
        self._negative_line(
            seller.request("PATCH", f"/api/addresses/{first_id}", {"label": "越权"}),
            404,
            "NOT_FOUND",
            "negative_address_owner_patch",
        )
        self._negative_line(
            seller.request("DELETE", f"/api/addresses/{first_id}"),
            404,
            "NOT_FOUND",
            "negative_address_owner_delete",
        )
        buyer_after_attack = self._data(
            buyer.request("GET", "/api/addresses"), "addresses_after_owner_attack"
        )
        self._check(
            isinstance(buyer_after_attack, dict)
            and any(
                int(item.get("id") or 0) == first_id and item.get("label") == "公司"
                for item in buyer_after_attack.get("items") or []
            ),
            "address_owner_attack_no_effect",
            _short(buyer_after_attack),
        )

        deleted_second = self._data(
            buyer.request("DELETE", f"/api/addresses/{second_id}"), "address_delete_two"
        )
        self._check(
            isinstance(deleted_second, dict) and deleted_second.get("deleted") is True,
            "address_delete_second",
            _short(deleted_second),
        )
        after_delete_two = self._data(
            buyer.request("GET", "/api/addresses"), "addresses_after_delete_two"
        )
        self._check(
            isinstance(after_delete_two, dict)
            and not any(
                int(item.get("id") or 0) == second_id
                for item in after_delete_two.get("items") or []
            )
            and len(after_delete_two.get("items") or []) == baseline_count + 1,
            "address_delete_removed_from_list",
            _short(after_delete_two),
        )

        deleted_first = self._data(
            buyer.request("DELETE", f"/api/addresses/{first_id}"), "address_delete_one"
        )
        self._check(
            isinstance(deleted_first, dict) and deleted_first.get("deleted") is True,
            "address_delete_first",
            _short(deleted_first),
        )
        final_list = self._data(
            buyer.request("GET", "/api/addresses"), "addresses_final"
        )
        if not isinstance(final_list, dict):
            self.fail("address list must be an object")
        self._check(
            len(final_list.get("items") or []) == baseline_count
            and {int(item["id"]) for item in final_list.get("items") or []} == baseline_ids,
            "address_state_restored",
            _short(final_list),
        )
        if baseline_default is not None and int(baseline_default) in baseline_ids:
            restored = self._data(
                buyer.request(
                    "PATCH",
                    f"/api/addresses/{int(baseline_default)}",
                    {"is_default": True},
                ),
                "address_restore_seeded_default",
            )
            self._check(
                isinstance(restored, dict) and restored.get("is_default") is True,
                "address_restore_seeded_default",
                _short(restored),
            )
        print(
            f"PASS addresses_crud_owner_default baseline={baseline_count} "
            f"created=2 deleted=2 owner_patch=404 owner_delete=404 default_omitted_ok=true",
            flush=True,
        )

    def test_06_address_default_atomic_under_concurrency(self) -> None:
        first = ApiClient()
        first.login("buyer")
        second = ApiClient()
        second.login("buyer")
        before = self._data(first.request("GET", "/api/addresses"), "atomic_before")
        if not isinstance(before, dict):
            self.fail("address list must be an object")
        baseline_ids = {int(item["id"]) for item in before.get("items") or []}
        baseline_default = before.get("default_id")

        a1 = self._data(
            first.request(
                "POST",
                "/api/addresses",
                self._address_body("并发甲", "并发买家甲", is_default=False),
            ),
            "atomic_create_a",
        )
        a2 = self._data(
            first.request(
                "POST",
                "/api/addresses",
                self._address_body("并发乙", "并发买家乙", is_default=False),
            ),
            "atomic_create_b",
        )
        if not isinstance(a1, dict) or not isinstance(a2, dict):
            self.fail("atomic test requires two created addresses")
        first_id = int(a1["id"])
        second_id = int(a2["id"])

        barrier = threading.Barrier(2)

        def fire(client: ApiClient, address_id: int) -> tuple[int, Any]:
            barrier.wait(timeout=10)
            return client.request(
                "PATCH", f"/api/addresses/{address_id}", {"is_default": True}
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            future_one = pool.submit(fire, first, first_id)
            future_two = pool.submit(fire, second, second_id)
            result_one = future_one.result(timeout=45)
            result_two = future_two.result(timeout=45)
        data_one = self._data(result_one, "atomic_patch_a")
        data_two = self._data(result_two, "atomic_patch_b")
        self._check(
            isinstance(data_one, dict) and data_one.get("is_default") is True,
            "atomic_patch_a",
            _short(data_one),
        )
        self._check(
            isinstance(data_two, dict) and data_two.get("is_default") is True,
            "atomic_patch_b",
            _short(data_two),
        )
        final_list = self._data(
            first.request("GET", "/api/addresses"), "atomic_final"
        )
        if not isinstance(final_list, dict):
            self.fail("address list must be an object")
        defaults = [
            int(item["id"])
            for item in final_list.get("items") or []
            if item.get("is_default")
        ]
        self._check(
            len(defaults) == 1
            and defaults[0] in {first_id, second_id}
            and final_list.get("default_id") == defaults[0],
            "atomic_default_singleton",
            _short(final_list),
        )

        for address_id in (first_id, second_id):
            status, payload = first.request("DELETE", f"/api/addresses/{address_id}")
            self._check(
                status == 200 and isinstance(payload, dict),
                "atomic_cleanup_delete",
                f"id={address_id} status={status} code={_error_of(payload).get('code')}",
            )
        if baseline_default is not None and int(baseline_default) in baseline_ids:
            status, payload = first.request(
                "PATCH",
                f"/api/addresses/{int(baseline_default)}",
                {"is_default": True},
            )
            self._check(
                status == 200 and isinstance(payload, dict),
                "atomic_restore_default",
                f"status={status}",
            )
        print(
            f"PASS address_default_atomic_concurrency "
            f"statuses={result_one[0]},{result_two[0]} winner={defaults[0]} one_default=true",
            flush=True,
        )

    # ------------------------------------------------------------------
    # Negative controls
    # ------------------------------------------------------------------

    def test_07_negative_controls(self) -> None:
        guest = ApiClient()
        guest.session()
        buyer = ApiClient()
        buyer.login("buyer")
        seller = ApiClient()
        seller.login("seller")
        _, products = self._all_products(buyer)
        target_slug = str(products[0]["slug"])
        quoted_target = urllib.parse.quote(target_slug, safe="")
        review_path = f"/api/products/{quoted_target}/reviews"
        toggle_path = f"/api/favorites/{quoted_target}/toggle"
        buyer_addresses = self._data(
            buyer.request("GET", "/api/addresses"), "negative_buyer_addresses"
        )
        if not isinstance(buyer_addresses, dict):
            self.fail("buyer addresses must be an object")
        buyer_address_ids = [
            int(item["id"]) for item in buyer_addresses.get("items") or []
        ]
        self._check(bool(buyer_address_ids), "negative_owner_setup", "buyer has no address")
        buyer_address_id = buyer_address_ids[0]

        negative_labels: list[str] = []

        def negative(result: tuple[int, Any], expected_status: int, expected_code: str, label: str) -> dict[str, Any]:
            error = self._negative_line(result, expected_status, expected_code, label)
            negative_labels.append(label)
            return error

        self._negative_line(
            guest.request("GET", "/api/favorites"),
            401,
            "AUTH_REQUIRED",
            "negative_favorites_guest_get",
        )
        negative_labels.append("negative_favorites_guest_get")
        self._negative_line(
            guest.request("GET", "/api/addresses"),
            401,
            "AUTH_REQUIRED",
            "negative_addresses_guest_get",
        )
        negative_labels.append("negative_addresses_guest_get")
        guest_toggle = guest.request("POST", toggle_path)
        negative(guest_toggle, 401, "AUTH_REQUIRED", "negative_favorites_guest_post")
        guest_create = guest.request(
            "POST",
            "/api/addresses",
            self._address_body("游客", "游客买家", is_default=False),
        )
        negative(guest_create, 401, "AUTH_REQUIRED", "negative_addresses_guest_post")

        missing_csrf = buyer.request("POST", toggle_path, csrf=None)
        negative(missing_csrf, 403, "CSRF_FAILED", "negative_favorites_csrf")

        missing_fields = buyer.request("POST", "/api/addresses", {})
        missing_error = negative(
            missing_fields, 400, "VALIDATION_ERROR", "negative_address_missing_fields"
        )
        self._check(
            isinstance(missing_error.get("fields"), dict)
            and "receiver" in missing_error["fields"],
            "negative_address_missing_fields_detail",
            _short(missing_error),
        )

        bad_phone_body = self._address_body("坏手机号", "验收买家", is_default=False)
        bad_phone_body["phone"] = "12345678901"
        bad_phone = buyer.request("POST", "/api/addresses", bad_phone_body)
        bad_phone_error = negative(
            bad_phone, 400, "VALIDATION_ERROR", "negative_address_bad_phone"
        )
        self._check(
            isinstance(bad_phone_error.get("fields"), dict)
            and "phone" in bad_phone_error["fields"],
            "negative_address_bad_phone_detail",
            _short(bad_phone_error),
        )

        extra_body = self._address_body("未知字段", "验收买家", is_default=False)
        extra_body["role"] = "buyer"
        extra_field = buyer.request("POST", "/api/addresses", extra_body)
        extra_error = negative(
            extra_field, 400, "VALIDATION_ERROR", "negative_address_unknown_field"
        )
        self._check(
            isinstance(extra_error.get("fields"), dict)
            and "role" in extra_error["fields"],
            "negative_address_unknown_field_detail",
            _short(extra_error),
        )

        unknown_address = buyer.request(
            "PATCH", "/api/addresses/99999999", {"label": "不存在"}
        )
        unknown_address_error = negative(
            unknown_address, 404, "NOT_FOUND", "negative_address_not_found"
        )
        owner_patch = seller.request(
            "PATCH", f"/api/addresses/{buyer_address_id}", {"label": "越权"}
        )
        owner_patch_error = negative(
            owner_patch, 404, "NOT_FOUND", "negative_address_foreign_owner"
        )
        self._check(
            unknown_address_error.get("message") == owner_patch_error.get("message"),
            "negative_address_existence_hidden",
            f"missing={unknown_address_error.get('message')} foreign={owner_patch_error.get('message')}",
        )

        invalid_sort = buyer.request("GET", _url("/api/products", sort="latest"))
        negative(invalid_sort, 400, "VALIDATION_ERROR", "negative_products_bad_sort")
        invalid_kind = buyer.request("GET", _url("/api/products", kind="direct"))
        negative(invalid_kind, 400, "VALIDATION_ERROR", "negative_products_bad_kind")
        invalid_rating = buyer.request("GET", _url("/api/products", min_rating="NaN"))
        negative(
            invalid_rating, 400, "VALIDATION_ERROR", "negative_products_bad_min_rating"
        )
        invalid_page = buyer.request("GET", _url("/api/products", page_size=25))
        negative(invalid_page, 400, "VALIDATION_ERROR", "negative_products_bad_page_size")
        invalid_review_rating = buyer.request(
            "GET", _url(review_path, rating=6)
        )
        negative(
            invalid_review_rating,
            400,
            "VALIDATION_ERROR",
            "negative_reviews_bad_rating",
        )
        missing_product = buyer.request(
            "GET", "/api/products/no-such-product-7a1d"
        )
        negative(missing_product, 404, "NOT_FOUND", "negative_product_detail_missing")
        missing_product_reviews = buyer.request(
            "GET", "/api/products/no-such-product-7a1d/reviews"
        )
        negative(
            missing_product_reviews,
            404,
            "NOT_FOUND",
            "negative_product_reviews_missing",
        )
        missing_toggle_product = buyer.request(
            "POST", "/api/favorites/no-such-product-7a1d/toggle"
        )
        negative(
            missing_toggle_product,
            404,
            "NOT_FOUND",
            "negative_favorite_product_missing",
        )
        missing_view_slug = guest.request("POST", "/api/events/view", {})
        negative(
            missing_view_slug, 400, "VALIDATION_ERROR", "negative_view_missing_slug"
        )
        unknown_view = guest.request(
            "POST", "/api/events/view", {"product_slug": "no-such-product-7a1d"}
        )
        negative(unknown_view, 404, "NOT_FOUND", "negative_view_missing_product")
        unknown_csrf = buyer.request("POST", toggle_path, csrf="definitely-not-the-token")
        negative(unknown_csrf, 403, "CSRF_FAILED", "negative_favorites_wrong_csrf")

        base_page, base_cards = self._all_products(buyer)
        base_total = int(base_page.get("total") or 0)
        for weird in ("%", "_", "' OR 1=1 --"):
            status, payload = buyer.request(
                "GET", _url("/api/products", q=weird, page_size=24)
            )
            weird_data = self._data((status, payload), "like_escape_query")
            self._check(
                isinstance(weird_data, dict)
                and int(weird_data.get("total") or 0) < base_total,
                "negative_like_wildcards",
                f"q={weird!r} total={weird_data.get('total') if isinstance(weird_data, dict) else None}",
            )
        print(
            "PASS security_like_escaped terms=3 base_total=" + str(base_total),
            flush=True,
        )
        print(
            f"PASS negative_controls_all count={len(negative_labels)} "
            f"products={len(base_cards)} address_id={buyer_address_id}",
            flush=True,
        )


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CatalogAPITests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    verdict = "PASS" if result.wasSuccessful() else "FAIL"
    print(
        f"CATALOG_API verdict={verdict} tests={result.testsRun} "
        f"checks={CatalogAPITests.check_count} "
        f"failures={len(result.failures)} errors={len(result.errors)}",
        flush=True,
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
