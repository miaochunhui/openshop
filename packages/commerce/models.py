"""Shared domain serialization and small owner-state helpers."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any, Iterable

from packages.commerce import db as dbutil

KIND_LABELS = {"self": "官方自营", "brand": "品牌旗舰", "factory": "源头工厂"}
KIND_ORDER = {"self": 0, "brand": 1, "factory": 2}
STATUS_LABELS = {
    "PENDING_PAYMENT": "待付款",
    "GROUP_FORMING": "拼团中",
    "PAID": "待发货",
    "SHIPPED": "待收货",
    "COMPLETED": "已完成",
    "CANCELLED": "已取消",
    "REFUNDED": "已退款",
}
PACKAGE_STATUS_LABELS = {"PENDING": "待发货", "SHIPPED": "运输中", "DELIVERED": "已送达", "CANCELLED": "已取消"}
CARRIERS = ["青鸟速运", "江海快递", "晨风同城"]


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def mask_name(name: str) -> str:
    if not name:
        return "匿名"
    if len(name) <= 1:
        return name + "**"
    return name[0] + "**"


def shop_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "slug": row["slug"],
        "name": row["name"],
        "kind": row["kind"],
        "tagline": row["tagline"],
        "story": row["story"],
        "rating": round(float(row["rating"]), 1),
        "followers_count": int(row["followers_count"]),
        "since": row["since"],
        "verified": bool(row["verified"]),
        "response_rate": row["response_rate"],
        "ship_from": row["ship_from"],
    }


def get_user_row(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return dbutil.one(conn, "SELECT * FROM users WHERE id=?", (int(user_id),))


def user_payload(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    shop = None
    if row["shop_id"] is not None:
        shop = shop_payload(dbutil.one(conn, "SELECT * FROM shops WHERE id=?", (row["shop_id"],)))
    return {
        "id": int(row["id"]),
        "handle": row["handle"],
        "display_name": row["display_name"],
        "email": row["email"],
        "role": row["role"],
        "avatar": row["avatar"],
        "member_since": row["member_since"],
        "shop_id": int(row["shop_id"]) if row["shop_id"] is not None else None,
        "shop": (
            {"id": shop["id"], "slug": shop["slug"], "name": shop["name"], "kind": shop["kind"], "verified": shop["verified"]}
            if shop
            else None
        ),
    }


def category_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "name": row["name"],
        "subtitle": row["subtitle"] if "subtitle" in row.keys() else "",
        "icon": row["icon"] if "icon" in row.keys() else "store",
        "sort_order": int(row["sort_order"]) if "sort_order" in row.keys() else 0,
        "count": int(row["count"]) if "count" in row.keys() else 0,
    }


def categories_with_counts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = dbutil.all_rows(
        conn,
        """SELECT c.*, COUNT(p.id) AS count FROM categories c
           LEFT JOIN products p ON p.category_slug=c.slug AND p.status='active'
           GROUP BY c.slug ORDER BY c.sort_order, c.name""",
    )
    return [category_payload(row) for row in rows]


def _sku_rows(conn: sqlite3.Connection, product_id: int) -> list[sqlite3.Row]:
    return dbutil.all_rows(conn, "SELECT * FROM skus WHERE product_id=? ORDER BY sort_order,id", (int(product_id),))


def _offer_rows(conn: sqlite3.Connection, product_id: int) -> list[sqlite3.Row]:
    return dbutil.all_rows(
        conn,
        """SELECT o.*, s.slug AS shop_slug, s.name AS shop_name, s.kind AS shop_kind,
                  s.tagline AS shop_tagline, s.rating AS shop_rating, s.followers_count AS shop_followers,
                  s.since AS shop_since, s.verified AS shop_verified, s.response_rate AS shop_response_rate,
                  s.ship_from AS shop_ship_from
           FROM offers o JOIN shops s ON s.id=o.shop_id
           WHERE o.product_id=? AND o.status='active' AND s.verified=1
           ORDER BY o.is_primary DESC, o.price_cents ASC, o.id ASC""",
        (int(product_id),),
    )


def _unit_delta_min(skus: list[sqlite3.Row]) -> int:
    return min((int(s["price_delta_cents"]) for s in skus), default=0)


def _review_count(conn: sqlite3.Connection, product_id: int) -> int:
    return int(dbutil.scalar(conn, "SELECT COUNT(*) FROM reviews WHERE product_id=?", (int(product_id),)) or 0)


def get_product_row(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return dbutil.one(conn, "SELECT * FROM products WHERE slug=? AND status='active'", (slug,))


def product_card(conn: sqlite3.Connection, product: sqlite3.Row, *, category_name: str | None = None) -> dict[str, Any]:
    pid = int(product["id"])
    offers = _offer_rows(conn, pid)
    skus = _sku_rows(conn, pid)
    min_delta = _unit_delta_min(skus)
    category = dbutil.one(conn, "SELECT * FROM categories WHERE slug=?", (product["category_slug"],))
    if category is None:
        raise ValueError(f"product {product['slug']} has missing category")
    if category_name is None:
        category_name = category["name"]

    priced_offers = [
        (int(o["price_cents"]) + min_delta, int(o["market_price_cents"]) + min_delta, o, min_delta)
        for o in offers
    ]
    if priced_offers:
        price, market_price, best_offer, _delta = min(priced_offers, key=lambda pair: pair[0])
    else:
        price, market_price, best_offer = 0, 0, None
    group_candidates: list[tuple[int, sqlite3.Row]] = []
    for offer in offers:
        if offer["group_size"] is not None and offer["group_price_cents"] is not None:
            group_candidates.append((int(offer["group_price_cents"]) + min_delta, offer))
    group = None
    if group_candidates:
        group_price, group_offer = min(group_candidates, key=lambda pair: pair[0])
        group = {
            "offer_id": int(group_offer["id"]),
            "size": int(group_offer["group_size"]),
            "price_cents": int(group_price),
            "stock": int(group_offer["stock"]),
        }
    services: list[str] = []
    for offer in offers:
        for service in dbutil.json_loads(offer["services_json"], []):
            if isinstance(service, str) and service not in services:
                services.append(service)
    kinds = sorted({str(o["kind"]) for o in offers}, key=lambda k: KIND_ORDER.get(k, 9))
    delivery_label = ""
    eta_min: int | None = None
    ship_from = ""
    if best_offer is not None:
        eta_min = int(best_offer["delivery_eta_days"])
        ship_from = best_offer["shop_ship_from"] or ""
        delivery_label = (ship_from + " · " if ship_from else "") + f"预计{eta_min}天送达"
    discount = 0
    if market_price > 0 and price < market_price:
        discount = int(((market_price - price) * 100) // market_price)
    return {
        "id": pid,
        "slug": product["slug"],
        "title": product["title"],
        "subtitle": product["subtitle"],
        "brand": product["brand"],
        "category": {"slug": category["slug"], "name": category_name},
        "cover": product["cover"],
        "cover_alt": product["cover_alt"],
        "price_cents": int(price),
        "market_price_cents": int(market_price),
        "saved_cents": max(0, int(market_price) - int(price)),
        "discount_percent": discount,
        "offer_count": len(offers),
        "offer_kinds": kinds,
        "rating": round(float(product["rating"]), 1),
        "review_count": int(product["review_count"]),
        "sold_count": int(product["sold_count"]),
        "badges": dbutil.json_loads(product["badges_json"], []),
        "tags": dbutil.json_loads(product["tags_json"], []),
        "services": services[:4],
        "delivery_label": delivery_label,
        "eta_days": eta_min,
        "free_shipping": bool(best_offer and int(best_offer["shipping_fee_cents"]) == 0),
        "group": group,
        "featured": bool(product["featured"]),
        "is_new": bool(product["is_new"]),
        "in_stock": any(int(o["stock"]) > 0 for o in offers),
        "created_at": product["created_at"],
    }


def cards_for_rows(conn: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [product_card(conn, row) for row in rows]


def review_payload(row: sqlite3.Row, *, viewer_id: int | None = None) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "author": row["author"],
        "rating": int(row["rating"]),
        "title": row["title"],
        "body": row["body"],
        "tags": dbutil.json_loads(row["tags_json"], []),
        "verified": bool(row["verified"]),
        "helpful_count": int(row["helpful_count"]),
        "sku_label": row["sku_label"],
        "created_at": row["created_at"],
        "mine": viewer_id is not None and row["user_id"] == viewer_id,
    }


def review_summary(conn: sqlite3.Connection, product_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reviews = dbutil.all_rows(conn, "SELECT * FROM reviews WHERE product_id=? ORDER BY created_at DESC, id DESC", (int(product_id),))
    total = len(reviews)
    dist: list[dict[str, Any]] = []
    for stars in (5, 4, 3, 2, 1):
        count = sum(1 for r in reviews if int(r["rating"]) == stars)
        percent = int(round(count * 100 / total)) if total else 0
        dist.append({"stars": stars, "count": count, "percent": percent})
    if total:
        diff = 100 - sum(item["percent"] for item in dist)
        dist[0]["percent"] = max(0, dist[0]["percent"] + diff)
    tags_map: dict[str, int] = {}
    for review in reviews:
        for tag in dbutil.json_loads(review["tags_json"], []):
            if isinstance(tag, str) and tag:
                tags_map[tag] = tags_map.get(tag, 0) + 1
    tags = [{"label": key, "count": val} for key, val in sorted(tags_map.items(), key=lambda kv: (-kv[1], kv[0]))]
    avg = round(sum(int(r["rating"]) for r in reviews) / total, 1) if total else 0.0
    summary = {"rating": avg, "count": total, "distribution": dist, "tags": tags}
    items = [review_payload(r) for r in reviews[:3]]
    return summary, items


def product_detail(conn: sqlite3.Connection, product: sqlite3.Row) -> dict[str, Any]:
    card = product_card(conn, product)
    offers = _offer_rows(conn, int(product["id"]))
    offer_payloads: list[dict[str, Any]] = []
    for offer in offers:
        group = None
        if offer["group_size"] is not None:
            group = {"size": int(offer["group_size"]), "price_cents": int(offer["group_price_cents"]), "stock": int(offer["stock"])}
        offer_payloads.append(
            {
                "id": int(offer["id"]),
                "kind": offer["kind"],
                "kind_label": KIND_LABELS.get(offer["kind"], offer["kind"]),
                "name": offer["name"],
                "shop": shop_payload(
                    {
                        "id": offer["shop_id"], "slug": offer["shop_slug"], "name": offer["shop_name"],
                        "kind": offer["shop_kind"], "tagline": offer["shop_tagline"], "story": "",
                        "rating": offer["shop_rating"], "followers_count": offer["shop_followers"],
                        "since": offer["shop_since"], "verified": offer["shop_verified"],
                        "response_rate": offer["shop_response_rate"], "ship_from": offer["shop_ship_from"],
                    }
                ),
                "price_cents": int(offer["price_cents"]),
                "market_price_cents": int(offer["market_price_cents"]),
                "shipping_fee_cents": int(offer["shipping_fee_cents"]),
                "delivery": {
                    "eta_days": int(offer["delivery_eta_days"]),
                    "label": f"预计{int(offer['delivery_eta_days'])}天送达",
                    "note": offer["delivery_note"],
                },
                "services": dbutil.json_loads(offer["services_json"], []),
                "stock": int(offer["stock"]),
                "group": group,
                "is_primary": bool(offer["is_primary"]),
            }
        )
    summary, items = review_summary(conn, int(product["id"]))
    images = dbutil.json_loads(product["images_json"], [])
    if not images:
        images = [{"src": product["cover"], "alt": product["cover_alt"]}]
    return {
        **card,
        "description": product["description"],
        "story": product["story"],
        "images": images,
        "highlights": dbutil.json_loads(product["highlights_json"], []),
        "specs": dbutil.json_loads(product["specs_json"], []),
        "detail_sections": dbutil.json_loads(product["detail_json"], []),
        "faq": dbutil.json_loads(product["faq_json"], []),
        "skus": [
            {
                "id": int(sku["id"]),
                "name": sku["name"],
                "options": dbutil.json_loads(sku["options_json"], {}),
                "price_delta_cents": int(sku["price_delta_cents"]),
            }
            for sku in _sku_rows(conn, int(product["id"]))
        ],
        "offers": offer_payloads,
        "reviews": {"summary": summary, "items": items},
    }


def related_cards(conn: sqlite3.Connection, product: sqlite3.Row, limit: int = 4) -> list[dict[str, Any]]:
    rows = dbutil.all_rows(
        conn,
        """SELECT * FROM products WHERE status='active' AND id!=? AND category_slug=?
           ORDER BY featured DESC, rating DESC, id DESC LIMIT ?""",
        (int(product["id"]), product["category_slug"], int(limit)),
    )
    if len(rows) < limit:
        seen = {int(r["id"]) for r in rows} | {int(product["id"])}
        fillers = dbutil.all_rows(
            conn,
            "SELECT * FROM products WHERE status='active' ORDER BY featured DESC, rating DESC, id DESC LIMIT 16",
        )
        for row in fillers:
            if int(row["id"]) in seen:
                continue
            seen.add(int(row["id"]))
            rows.append(row)
            if len(rows) >= limit:
                break
    return cards_for_rows(conn, rows)


def address_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "label": row["label"],
        "receiver": row["receiver"],
        "phone": row["phone"],
        "province": row["province"],
        "city": row["city"],
        "district": row["district"],
        "detail": row["detail"],
        "is_default": bool(row["is_default"]),
    }


def default_address(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return dbutil.one(conn, "SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC, updated_at DESC, id DESC LIMIT 1", (int(user_id),))


def cart_count(conn: sqlite3.Connection, owner_key: str) -> int:
    row = dbutil.one(
        conn,
        """SELECT COALESCE(SUM(ci.quantity),0) AS n FROM carts c
           LEFT JOIN cart_items ci ON ci.cart_id=c.id WHERE c.owner_key=?""",
        (owner_key,),
    )
    return int(row["n"]) if row else 0


def favorite_count(conn: sqlite3.Connection, owner_key: str) -> int:
    return int(dbutil.scalar(conn, "SELECT COUNT(*) FROM favorites WHERE owner_key=?", (owner_key,)) or 0)


def merge_owner_state(conn: sqlite3.Connection, guest_key: str, user_key: str) -> None:
    """Merge guest cart/favorites/browse history into the user's state."""
    if guest_key == user_key:
        return
    source = dbutil.one(conn, "SELECT * FROM carts WHERE owner_key=?", (guest_key,))
    target = dbutil.one(conn, "SELECT * FROM carts WHERE owner_key=?", (user_key,))
    if source is not None:
        if target is None:
            conn.execute("UPDATE carts SET owner_key=? WHERE id=?", (user_key, source["id"]))
        else:
            items = dbutil.all_rows(conn, "SELECT * FROM cart_items WHERE cart_id=? ORDER BY id", (source["id"],))
            for item in items:
                stock = dbutil.scalar(conn, "SELECT stock FROM offers WHERE id=?", (item["offer_id"],)) or 0
                if int(stock) <= 0:
                    continue
                existing = dbutil.one(
                    conn,
                    """SELECT * FROM cart_items WHERE cart_id=? AND offer_id=? AND sku_id=? AND buy_mode=?
                       AND IFNULL(group_code,'')=IFNULL(?,'') LIMIT 1""",
                    (target["id"], item["offer_id"], item["sku_id"], item["buy_mode"], item["group_code"]),
                )
                if existing:
                    quantity = 1 if item["buy_mode"] == "group" else max(1, int(existing["quantity"]) + int(item["quantity"]))
                    conn.execute(
                        "UPDATE cart_items SET quantity=?, selected=?, updated_at=? WHERE id=?",
                        (min(quantity, int(stock)), 1 if item["selected"] or existing["selected"] else 0, dbutil.iso(), existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO cart_items(cart_id,offer_id,sku_id,quantity,selected,buy_mode,group_code,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (
                            target["id"], item["offer_id"], item["sku_id"],
                            1 if item["buy_mode"] == "group" else min(int(item["quantity"]), int(stock)),
                            item["selected"], item["buy_mode"], item["group_code"],
                            item["created_at"], dbutil.iso(),
                        ),
                    )
            conn.execute("DELETE FROM cart_items WHERE cart_id=?", (source["id"],))
            conn.execute("DELETE FROM carts WHERE id=?", (source["id"],))
            conn.execute("UPDATE carts SET updated_at=? WHERE id=?", (dbutil.iso(), target["id"]))
    for fav in dbutil.all_rows(conn, "SELECT product_id,created_at FROM favorites WHERE owner_key=?", (guest_key,)):
        conn.execute(
            "INSERT OR IGNORE INTO favorites(owner_key,product_id,created_at) VALUES(?,?,?)",
            (user_key, fav["product_id"], fav["created_at"]),
        )
    conn.execute("DELETE FROM favorites WHERE owner_key=?", (guest_key,))
    conn.execute("UPDATE browse_events SET owner_key=? WHERE owner_key=?", (user_key, guest_key))


def grant_auto_coupons(conn: sqlite3.Connection, user_id: int) -> int:
    now = dbutil.utc_now()
    rows = dbutil.all_rows(conn, "SELECT * FROM coupons WHERE auto_claim_new_user=1")
    granted = 0
    for coupon in rows:
        existing = dbutil.one(conn, "SELECT 1 FROM user_coupons WHERE user_id=? AND coupon_code=?", (int(user_id), coupon["code"]))
        if existing:
            continue
        if int(coupon["quantity"]) > 0 and int(coupon["claimed_count"]) >= int(coupon["quantity"]):
            continue
        expires = dbutil.iso(now + __import__("datetime").timedelta(days=int(coupon["valid_days"])))
        conn.execute(
            "INSERT INTO user_coupons(user_id,coupon_code,status,claimed_at,expires_at) VALUES(?,?,'AVAILABLE',?,?)",
            (int(user_id), coupon["code"], dbutil.iso(now), expires),
        )
        conn.execute("UPDATE coupons SET claimed_count=claimed_count+1 WHERE code=?", (coupon["code"],))
        granted += 1
    return granted


def username_from_email(email: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "", email.split("@", 1)[0].lower())[:20]
    if len(base) < 3:
        base = "user"
    return base[:30]


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
