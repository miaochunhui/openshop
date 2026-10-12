"""Deterministic catalog/demo seed loader.

The seed is fictional. It is idempotent for a database seed fingerprint; a
different catalog against a populated database fails closed rather than
silently mixing two demo worlds.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from packages.commerce import db as dbutil

SEED_VERSION = "1"


class SeedError(RuntimeError):
    pass


class SeedMismatchError(SeedError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise SeedError(message)


def _text(value: Any, name: str, *, min_len: int = 1, max_len: int = 500) -> str:
    _need(isinstance(value, str), f"{name} must be a string")
    value = value.strip()
    _need(min_len <= len(value) <= max_len, f"{name} length must be {min_len}..{max_len}")
    return value


def _money(value: Any, name: str, *, allow_zero: bool = True) -> int:
    _need(isinstance(value, int) and not isinstance(value, bool), f"{name} must be integer cents")
    _need(value > 0 or (allow_zero and value == 0), f"{name} invalid cents")
    return value


def _slug(value: Any, name: str, max_len: int = 64) -> str:
    value = _text(value, name, min_len=2, max_len=max_len)
    _need(bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value)), f"{name} invalid slug: {value}")
    return value


def _local_asset(root: Path, value: Any, name: str) -> str:
    path = _text(value, name, min_len=5, max_len=240)
    _need(path.startswith("/web/assets/") and ".." not in path, f"{name} must be a local /web/assets path")
    _need((root / path.lstrip("/")).is_file(), f"{name} asset missing on disk: {path}")
    return path


def _load_catalog(path: Path) -> tuple[dict[str, Any], bytes, str]:
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedError(f"catalog JSON invalid: {exc}") from exc
    _need(isinstance(data, dict), "catalog root must be object")
    digest = hashlib.sha256(dbutil.SCHEMA_VERSION.encode() + b"\0" + raw + b"\0" + SEED_VERSION.encode()).hexdigest()
    return data, raw, digest


def seed_catalog(conn, catalog_path: Path | str) -> None:
    catalog_path = Path(catalog_path)
    data, _raw, digest = _load_catalog(catalog_path)
    existing_products = dbutil.scalar(conn, "SELECT COUNT(*) FROM products") or 0
    existing_digest = dbutil.scalar(conn, "SELECT value FROM meta WHERE key='seed_digest'")
    if existing_products:
        if existing_digest == digest:
            return
        raise SeedMismatchError(
            "database already has a different catalog seed; use a fresh --data-dir "
            "or reset the demo database explicitly (no user data was changed)"
        )
    root = dbutil.ROOT
    categories = data.get("categories")
    shops = data.get("shops")
    users = data.get("users")
    coupons = data.get("coupons") or []
    products = data.get("products")
    _need(isinstance(categories, list) and categories, "categories required")
    _need(isinstance(shops, list) and shops, "shops required")
    _need(isinstance(users, list) and users, "users required")
    _need(isinstance(products, list) and products, "products required")
    now = dbutil.utc_now()
    now_iso = dbutil.iso(now)

    category_slugs: set[str] = set()
    shop_kind_by_slug: dict[str, str] = {}
    shop_id_by_slug: dict[str, int] = {}
    user_id_by_handle: dict[str, int] = {}
    product_slug_to_offer: dict[str, int] = {}

    with dbutil.transaction(conn):
        for index, cat in enumerate(categories):
            slug = _slug(cat.get("slug"), "category.slug", 32)
            _need(slug not in category_slugs, f"duplicate category {slug}")
            category_slugs.add(slug)
            conn.execute(
                "INSERT INTO categories(slug,name,subtitle,icon,sort_order) VALUES(?,?,?,?,?)",
                (
                    slug,
                    _text(cat.get("name"), "category.name", max_len=20),
                    _text(cat.get("subtitle", ""), "category.subtitle", min_len=0, max_len=80),
                    _text(cat.get("icon", "store"), "category.icon", max_len=30),
                    int(cat.get("sort_order", index)),
                ),
            )

        for shop in shops:
            slug = _slug(shop.get("slug"), "shop.slug", 40)
            kind = _text(shop.get("kind"), "shop.kind", max_len=8)
            _need(kind in {"self", "brand", "factory"}, f"invalid shop kind {kind}")
            _need(slug not in shop_kind_by_slug, f"duplicate shop {slug}")
            conn.execute(
                """INSERT INTO shops(slug,name,kind,tagline,story,rating,followers_count,since,verified,response_rate,ship_from,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    slug,
                    _text(shop.get("name"), "shop.name", max_len=40),
                    kind,
                    _text(shop.get("tagline", ""), "shop.tagline", min_len=0, max_len=100),
                    _text(shop.get("story", ""), "shop.story", min_len=0, max_len=600),
                    float(shop.get("rating", 4.8)),
                    int(shop.get("followers_count", 0)),
                    _text(shop.get("since", ""), "shop.since", min_len=0, max_len=10),
                    1 if shop.get("verified", True) else 0,
                    _text(shop.get("response_rate", ""), "shop.response_rate", min_len=0, max_len=20),
                    _text(shop.get("ship_from", ""), "shop.ship_from", min_len=0, max_len=50),
                    now_iso,
                ),
            )
            shop_id_by_slug[slug] = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            shop_kind_by_slug[slug] = kind

        emails: set[str] = set()
        for user in users:
            handle = _slug(user.get("handle"), "user.handle", 30)
            _need(handle not in user_id_by_handle, f"duplicate user handle {handle}")
            email = _text(user.get("email"), "user.email", max_len=120).lower()
            _need("@" in email and email not in emails, f"invalid/duplicate email {email}")
            emails.add(email)
            role = _text(user.get("role"), "user.role", max_len=10)
            _need(role in {"buyer", "seller"}, f"invalid user role {role}")
            shop_slug = user.get("shop")
            if shop_slug:
                _need(shop_slug in shop_id_by_slug, f"user.shop unknown: {shop_slug}")
            _need(not user.get("password") and not user.get("password_hash"), "seed users must not contain passwords")
            _local_asset(root, user.get("avatar", "/web/assets/brand/avatar-lin.svg"), "user.avatar")
            conn.execute(
                """INSERT INTO users(handle,display_name,email,role,avatar,shop_id,member_since,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    handle,
                    _text(user.get("display_name"), "user.display_name", min_len=2, max_len=24),
                    email,
                    role,
                    user.get("avatar", "/web/assets/brand/avatar-lin.svg"),
                    shop_id_by_slug.get(shop_slug),
                    _text(user.get("member_since", ""), "user.member_since", min_len=0, max_len=12),
                    now_iso,
                ),
            )
            uid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            user_id_by_handle[handle] = uid
            for address in user.get("addresses") or []:
                is_default = 1 if address.get("is_default") else 0
                if is_default:
                    conn.execute("UPDATE addresses SET is_default=0 WHERE user_id=?", (uid,))
                conn.execute(
                    """INSERT INTO addresses(user_id,label,receiver,phone,province,city,district,detail,is_default,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uid,
                        _text(address.get("label", ""), "address.label", min_len=0, max_len=10),
                        _text(address.get("receiver"), "address.receiver", min_len=2, max_len=20),
                        _text(address.get("phone"), "address.phone", min_len=6, max_len=20),
                        _text(address.get("province"), "address.province", max_len=30),
                        _text(address.get("city"), "address.city", max_len=30),
                        _text(address.get("district"), "address.district", max_len=30),
                        _text(address.get("detail"), "address.detail", min_len=5, max_len=120),
                        is_default,
                        now_iso,
                        now_iso,
                    ),
                )

        coupon_by_code: dict[str, dict[str, Any]] = {}
        for coupon in coupons:
            code = _text(coupon.get("code"), "coupon.code", max_len=30).upper()
            _need(re.fullmatch(r"[A-Z0-9]{3,30}", code) is not None, f"coupon code invalid: {code}")
            _need(code not in coupon_by_code, f"duplicate coupon {code}")
            threshold = _money(coupon.get("threshold_cents", 0), "coupon.threshold_cents")
            discount = _money(coupon.get("discount_cents"), "coupon.discount_cents")
            _need(discount > 0 and (threshold == 0 or threshold >= discount), f"coupon {code} invalid amounts")
            scope = _text(coupon.get("scope", "all"), "coupon.scope", max_len=40)
            if scope != "all":
                _need(scope.startswith("category:") and scope.split(":", 1)[1] in category_slugs, f"coupon {code} bad scope")
            coupon_by_code[code] = coupon
            conn.execute(
                """INSERT INTO coupons(code,title,description,threshold_cents,discount_cents,scope,valid_days,
                   auto_claim_new_user,quantity,claimed_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    _text(coupon.get("title"), "coupon.title", max_len=30),
                    _text(coupon.get("description"), "coupon.description", max_len=80),
                    threshold,
                    discount,
                    scope,
                    int(coupon.get("valid_days", 30)),
                    1 if coupon.get("auto_claim_new_user") else 0,
                    int(coupon.get("quantity", 0)),
                    0,
                    now_iso,
                ),
            )

        auto_codes = [c for c in coupon_by_code if coupon_by_code[c].get("auto_claim_new_user")]
        for user in users:
            if user.get("role") != "buyer":
                continue
            uid = user_id_by_handle[_slug(user.get("handle"), "user.handle", 30)]
            requested = [str(c).upper() for c in (user.get("coupons") or [])]
            grant_codes = list(dict.fromkeys(requested + auto_codes))
            for code in grant_codes:
                _need(code in coupon_by_code, f"user coupon unknown: {code}")
                coupon = coupon_by_code[code]
                expires_at = dbutil.iso(now + timedelta(days=int(coupon.get("valid_days", 30))))
                conn.execute(
                    "INSERT INTO user_coupons(user_id,coupon_code,status,claimed_at,expires_at) VALUES(?,?, 'AVAILABLE',?,?)",
                    (uid, code, now_iso, expires_at),
                )
                conn.execute("UPDATE coupons SET claimed_count=claimed_count+1 WHERE code=?", (code,))

        seen_product_slugs: set[str] = set()
        hero_count = 0
        for product in products:
            slug = _slug(product.get("slug"), "product.slug", 64)
            _need(slug not in seen_product_slugs, f"duplicate product {slug}")
            seen_product_slugs.add(slug)
            category = product.get("category")
            _need(category in category_slugs, f"product {slug} category unknown")
            flags = product.get("flags") or {}
            if flags.get("hero"):
                hero_count += 1
            art = product.get("art") or {}
            cover = _local_asset(root, art.get("cover"), "product.art.cover")
            cover_alt = _text(art.get("cover_alt", product.get("title", "")), "product.art.cover_alt", min_len=0, max_len=120)
            images = art.get("images") or []
            _need(isinstance(images, list) and images, f"product {slug} images required")
            for img in images:
                _local_asset(root, img.get("src"), "product.art.images.src")
                _text(img.get("alt", ""), "product.art.images.alt", min_len=0, max_len=120)
            reviews = product.get("reviews") or []
            _need(len(reviews) >= 1, f"product {slug} needs reviews")
            ratings = []
            for review in reviews:
                rating = int(review.get("rating", 0))
                _need(1 <= rating <= 5, f"review rating invalid for {slug}")
                ratings.append(rating)
                _need(len(_text(review.get("body"), "review.body", min_len=10, max_len=800)) >= 10, "review body too short")
            rating = round(sum(ratings) / len(ratings), 2)
            offers = product.get("offers") or []
            _need(isinstance(offers, list) and len(offers) >= 1, f"product {slug} offers required")
            primary_count = 0
            for offer in offers:
                sh = offer.get("shop")
                _need(sh in shop_kind_by_slug, f"product {slug} offer shop unknown")
                kind = offer.get("kind", shop_kind_by_slug[sh])
                _need(kind == shop_kind_by_slug[sh], f"product {slug} offer kind does not match shop")
                price = _money(offer.get("price_cents"), "offer.price_cents")
                market = _money(offer.get("market_price_cents"), "offer.market_price_cents")
                _need(market >= price, f"product {slug} market price below price")
                group_size = offer.get("group_size")
                group_price = offer.get("group_price_cents")
                if group_size in (0, None, "") or (isinstance(group_size, int) and group_size < 2):
                    group_size = None
                    group_price = None
                else:
                    _need(isinstance(group_size, int) and 2 <= group_size <= 20, f"group size invalid {slug}")
                    _need(isinstance(group_price, int) and 0 < group_price < price, f"group price invalid {slug}")
                if offer.get("is_primary"):
                    primary_count += 1
            _need(primary_count == 1, f"product {slug} needs exactly one primary offer")
            skus = product.get("skus") or []
            _need(isinstance(skus, list) and skus, f"product {slug} skus required")
            conn.execute(
                """INSERT INTO products(slug,title,subtitle,brand,category_slug,description,story,tags_json,badges_json,
                     images_json,cover,cover_alt,highlights_json,specs_json,detail_json,faq_json,featured,is_new,is_hero,
                     sold_count,rating,review_count,status,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?)""",
                (
                    slug,
                    _text(product.get("title"), "product.title", min_len=2, max_len=28),
                    _text(product.get("subtitle", ""), "product.subtitle", min_len=0, max_len=40),
                    _text(product.get("brand"), "product.brand", max_len=40),
                    category,
                    _text(product.get("description", ""), "product.description", min_len=0, max_len=800),
                    _text(product.get("story", ""), "product.story", min_len=0, max_len=1200),
                    dbutil.json_dumps(product.get("tags") or []),
                    dbutil.json_dumps(product.get("badges") or []),
                    dbutil.json_dumps(images),
                    cover,
                    cover_alt,
                    dbutil.json_dumps(product.get("highlights") or []),
                    dbutil.json_dumps(product.get("specs") or []),
                    dbutil.json_dumps(product.get("detail_sections") or []),
                    dbutil.json_dumps(product.get("faq") or []),
                    1 if flags.get("featured") else 0,
                    1 if flags.get("new") else 0,
                    1 if flags.get("hero") else 0,
                    int(product.get("sold_count", 0) or 0),
                    rating,
                    len(reviews),
                    dbutil.iso(now - timedelta(days=int(product.get("days_old", 0) or 0))),
                ),
            )
            pid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            for sort_index, sku in enumerate(skus):
                conn.execute(
                    "INSERT INTO skus(product_id,name,options_json,price_delta_cents,sort_order) VALUES(?,?,?,?,?)",
                    (
                        pid,
                        _text(sku.get("name"), "sku.name", max_len=50),
                        dbutil.json_dumps(sku.get("options") or {}),
                        _money(sku.get("price_delta_cents", 0), "sku.price_delta_cents"),
                        sort_index,
                    ),
                )
            first_offer_id = None
            for offer in offers:
                group_size = offer.get("group_size")
                if not isinstance(group_size, int) or group_size < 2:
                    group_size = None
                group_price = offer.get("group_price_cents")
                if group_size is None:
                    group_price = None
                cur = conn.execute(
                    """INSERT INTO offers(product_id,shop_id,kind,name,price_cents,market_price_cents,shipping_fee_cents,
                         stock,delivery_eta_days,delivery_note,services_json,group_size,group_price_cents,is_primary,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?, ?)""",
                    (
                        pid,
                        shop_id_by_slug[offer["shop"]],
                        offer["kind"],
                        _text(offer.get("name"), "offer.name", max_len=50),
                        int(offer["price_cents"]),
                        int(offer["market_price_cents"]),
                        int(offer.get("shipping_fee_cents", 0) or 0),
                        int(offer.get("stock", 0) or 0),
                        int(offer.get("delivery_eta_days", 3)),
                        _text(offer.get("delivery_note", ""), "offer.delivery_note", min_len=0, max_len=80),
                        dbutil.json_dumps(offer.get("services") or []),
                        group_size,
                        group_price,
                        1 if offer.get("is_primary") else 0,
                        now_iso,
                        now_iso,
                    ),
                )
                if offer.get("is_primary") and first_offer_id is None:
                    first_offer_id = int(cur.lastrowid)
            product_slug_to_offer[slug] = first_offer_id
            for review in reviews:
                days = int(review.get("days_ago", 1))
                conn.execute(
                    """INSERT INTO reviews(product_id,author,rating,title,body,tags_json,verified,helpful_count,sku_label,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pid,
                        _text(review.get("author", "匿**"), "review.author", max_len=20),
                        int(review["rating"]),
                        _text(review.get("title", ""), "review.title", min_len=0, max_len=40),
                        _text(review.get("body"), "review.body", min_len=10, max_len=800),
                        dbutil.json_dumps(review.get("tags") or []),
                        1 if review.get("verified", True) else 0,
                        int(review.get("helpful_count", 0) or 0),
                        _text(review.get("sku_name", ""), "review.sku_name", min_len=0, max_len=50),
                        dbutil.iso(now - timedelta(days=days)),
                    ),
                )
        _need(hero_count == 1, f"exactly one hero product required, got {hero_count}")

        # A small, internally real demo order history for account + merchant screens.
        buyer = dbutil.one(conn, "SELECT * FROM users WHERE handle='demo_buyer' LIMIT 1")
        if buyer is None:
            buyer = dbutil.one(conn, "SELECT * FROM users WHERE role='buyer' ORDER BY id LIMIT 1")
        _need(buyer is not None, "at least one buyer required")
        address = dbutil.one(conn, "SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC,id LIMIT 1", (buyer["id"],))
        _need(address is not None, "demo buyer address required")
        address_snapshot = {
            "id": address["id"], "label": address["label"], "receiver": address["receiver"],
            "phone": address["phone"], "province": address["province"], "city": address["city"],
            "district": address["district"], "detail": address["detail"],
        }
        self_offer = dbutil.one(conn,
            """SELECT o.*, p.title, p.cover, p.id AS pid, p.slug AS pslug, s.name AS shop_name, s.kind AS shop_kind,
                      k.id AS sku_id, k.name AS sku_name, k.price_delta_cents
               FROM offers o JOIN products p ON p.id=o.product_id JOIN shops s ON s.id=o.shop_id
               JOIN skus k ON k.product_id=p.id
               WHERE s.slug='openshop-self' AND o.is_primary=1
               ORDER BY p.is_hero DESC, p.id, k.sort_order LIMIT 1"""
        )
        brand_offer = dbutil.one(conn,
            """SELECT o.*, p.title, p.cover, p.id AS pid, p.slug AS pslug, s.name AS shop_name, s.kind AS shop_kind,
                      k.id AS sku_id, k.name AS sku_name, k.price_delta_cents
               FROM offers o JOIN products p ON p.id=o.product_id JOIN shops s ON s.id=o.shop_id
               JOIN skus k ON k.product_id=p.id
               WHERE s.slug!='openshop-self' AND o.is_primary=1
               ORDER BY p.featured DESC, p.id, k.sort_order LIMIT 1"""
        )
        _need(self_offer is not None, "seed history requires a self offer")
        demo_orders = []
        if self_offer:
            demo_orders.append((self_offer, "PENDING", "PAID", 2, 1))
        if brand_offer:
            demo_orders.append((brand_offer, "SHIPPED", "SHIPPED", 4, 1))
        if self_offer:
            demo_orders.append((self_offer, "SHIPPED", "COMPLETED", 8, 2))

        for idx, (offer, package_status, order_status, days_ago, quantity) in enumerate(demo_orders, 1):
            created = now - timedelta(days=days_ago)
            unit = int(offer["price_cents"]) + int(offer["price_delta_cents"])
            subtotal = unit * quantity
            order_no = f"OSDEMO{2026000 + idx}"
            paid_at = dbutil.iso(created + timedelta(minutes=18))
            shipped_at = paid_at if package_status in {"SHIPPED"} else None
            completed_at = dbutil.iso(created + timedelta(days=2)) if order_status == "COMPLETED" else None
            conn.execute(
                """INSERT INTO orders(order_no,user_id,status,subtotal_cents,discount_cents,shipping_cents,total_cents,
                     market_subtotal_cents,coupon_code,user_coupon_id,address_json,shipping_method,payment_method,note,
                     created_at,updated_at,paid_at,shipped_at,completed_at,idempotency_key)
                   VALUES(?,?,?,?,0,0,?,?,NULL,NULL,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_no, buyer["id"], order_status, subtotal, subtotal,
                    (int(offer["market_price_cents"]) + int(offer["price_delta_cents"])) * quantity,
                    dbutil.json_dumps(address_snapshot), "standard", "demo", "",
                    dbutil.iso(created), dbutil.iso(created), paid_at, shipped_at, completed_at,
                    f"seed-history-{idx}",
                ),
            )
            order_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            pkg_tracking = "QB202610120001" if package_status == "SHIPPED" else None
            conn.execute(
                """INSERT INTO order_packages(order_id,shop_id,status,carrier,tracking_no,shipping_method,shipped_at,
                     delivered_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_id, offer["shop_id"], package_status,
                    "青鸟速运" if package_status == "SHIPPED" else None,
                    pkg_tracking, "standard", shipped_at,
                    completed_at if order_status == "COMPLETED" else None,
                    dbutil.iso(created), dbutil.iso(created),
                ),
            )
            package_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """INSERT INTO order_items(order_id,package_id,product_id,offer_id,sku_id,shop_id,title,cover,sku_label,
                     unit_price_cents,quantity,buy_mode,offer_kind,services_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'standard',?,?)""",
                (
                    order_id, package_id, offer["pid"], offer["id"], offer["sku_id"], offer["shop_id"],
                    offer["title"], offer["cover"], offer["sku_name"], unit, quantity, offer["kind"],
                    offer["services_json"],
                ),
            )
            events = [("订单已创建", "等待买家付款")]
            if order_status in {"PAID", "SHIPPED", "COMPLETED"}:
                events.append(("支付成功", "演示支付已完成"))
            if package_status == "SHIPPED":
                events.append(("商家已发货", "青鸟速运 " + str(pkg_tracking)))
            if order_status == "COMPLETED":
                events.append(("交易完成", "买家已确认收货"))
            for n, (label, detail) in enumerate(events):
                conn.execute(
                    "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
                    (order_id, package_id, label, detail, dbutil.iso(created + timedelta(minutes=12 * n))),
                )

        fav_product = dbutil.one(conn, "SELECT id FROM products ORDER BY is_hero DESC,id LIMIT 1")
        if fav_product:
            owner_key = f"u:{buyer['id']}"
            conn.execute(
                "INSERT OR IGNORE INTO favorites(owner_key,product_id,created_at) VALUES(?,?,?)",
                (owner_key, fav_product["id"], now_iso),
            )
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('seed_digest',?)", (digest,))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('seed_version',?)", (SEED_VERSION,))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('seeded_at',?)", (now_iso,))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('app_name','openshop')")
