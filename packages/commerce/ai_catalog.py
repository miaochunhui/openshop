"""Build the grounded public-supply snapshot used by the AI mediator."""
from __future__ import annotations

import json
import re
from typing import Any

from packages.commerce import db as dbutil

_UNSAFE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

CATALOG_SQL = """
SELECT p.id AS product_id, p.slug AS product_slug, p.title AS product_title,
       p.subtitle, p.brand, p.description, p.tags_json, p.cover,
       p.sold_count, p.rating,
       c.name AS category_name,
       o.id AS offer_id, o.name AS offer_name, o.kind AS offer_kind,
       o.price_cents, o.market_price_cents, o.shipping_fee_cents, o.stock,
       o.delivery_eta_days, o.delivery_note, o.services_json,
       o.group_size, o.group_price_cents, o.is_primary,
       s.id AS shop_id, s.slug AS shop_slug, s.name AS shop_name,
       s.kind AS shop_kind, s.verified AS shop_verified,
       k.id AS sku_id, k.name AS sku_name, k.price_delta_cents
FROM products p
JOIN categories c ON c.slug = p.category_slug
JOIN offers o ON o.product_id = p.id
JOIN shops s ON s.id = o.shop_id
JOIN skus k ON k.product_id = p.id
WHERE p.status = 'active' AND o.status = 'active' AND o.stock > 0
ORDER BY p.featured DESC, p.id, o.is_primary DESC, o.price_cents, o.id,
         k.sort_order, k.id
"""


def clean_text(value: Any, limit: int, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    text = _UNSAFE_RE.sub(" ", value).replace("\r\n", "\n").strip()
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text or fallback


def parse_string_list(raw: Any, limit: int, item_limit: int) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            text = str(item).strip()
            if text:
                result.append(text[:item_limit])
    return result


def build_catalog(conn: Any) -> dict[str, Any]:
    rows = dbutil.all_rows(conn, CATALOG_SQL)
    products: dict[int, dict[str, Any]] = {}
    pair_map: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        product_id = int(row["product_id"])
        product = products.get(product_id)
        if product is None:
            product = {
                "id": product_id,
                "slug": str(row["product_slug"]),
                "title": clean_text(row["product_title"], 100),
                "subtitle": clean_text(row["subtitle"], 120),
                "brand": clean_text(row["brand"], 60),
                "category": clean_text(row["category_name"], 60),
                "description": clean_text(row["description"], 180),
                "tags": parse_string_list(row["tags_json"], 8, 24),
                "cover": str(row["cover"]),
                "sold_count": int(row["sold_count"] or 0),
                "rating": round(float(row["rating"] or 0), 2),
                "offers": {},
            }
            products[product_id] = product
        offers: dict[int, dict[str, Any]] = product["offers"]
        offer_id = int(row["offer_id"])
        offer = offers.get(offer_id)
        if offer is None:
            group_size = row["group_size"]
            group_base = row["group_price_cents"]
            group = None
            if group_size is not None and group_base is not None:
                group = {
                    "size": int(group_size),
                    "price_cents": int(group_base),
                    "stock": int(row["stock"] or 0),
                }
            offer = {
                "offer_id": offer_id,
                "name": clean_text(row["offer_name"], 80),
                "kind": str(row["offer_kind"]),
                "shop": {
                    "id": int(row["shop_id"]),
                    "slug": str(row["shop_slug"]),
                    "name": clean_text(row["shop_name"], 80),
                    "kind": str(row["shop_kind"]),
                    "verified": bool(row["shop_verified"]),
                },
                "price_cents": int(row["price_cents"] or 0),
                "market_price_cents": int(row["market_price_cents"] or 0),
                "shipping_fee_cents": int(row["shipping_fee_cents"] or 0),
                "stock": int(row["stock"] or 0),
                "delivery_eta_days": int(row["delivery_eta_days"] or 1),
                "delivery_note": clean_text(row["delivery_note"], 80),
                "services": parse_string_list(row["services_json"], 4, 30),
                "group": group,
                "skus": {},
            }
            offers[offer_id] = offer
        sku_id = int(row["sku_id"])
        delta = int(row["price_delta_cents"] or 0)
        sku = {
            "sku_id": sku_id,
            "name": clean_text(row["sku_name"], 60),
            "standard_cents": int(offer["price_cents"]) + delta,
            "group_cents": (int(row["group_price_cents"]) + delta) if row["group_price_cents"] is not None else None,
            "market_cents": int(offer["market_price_cents"]) + delta,
        }
        offer["skus"][sku_id] = sku
        pair_map[(offer_id, sku_id)] = {
            "product": product,
            "offer": offer,
            "sku": sku,
        }
    prompt_catalog: list[dict[str, Any]] = []
    for product in products.values():
        prompt_product = {
            "slug": product["slug"],
            "title": product["title"],
            "brand": product["brand"],
            "category": product["category"],
            "tags": product["tags"],
            "note": product["description"] or product["subtitle"],
            "rating": product["rating"],
            "sold": product["sold_count"],
            "offers": [],
        }
        for offer in product["offers"].values():
            skus = list(offer["skus"].values())
            if not skus:
                continue
            prompt_offer = {
                "offer_id": offer["offer_id"],
                "supply": offer["kind"],
                "shop": offer["shop"]["name"],
                "verified": offer["shop"]["verified"],
                "listing": offer["name"],
                "stock": offer["stock"],
                "eta_days": offer["delivery_eta_days"],
                "delivery_note": offer["delivery_note"],
                "shipping_cents": offer["shipping_fee_cents"],
                "group": (
                    {"size": offer["group"]["size"], "price_cents": offer["group"]["price_cents"]}
                    if offer["group"] is not None
                    else None
                ),
                "skus": [
                    {
                        "sku_id": sku["sku_id"],
                        "name": sku["name"],
                        "standard_cents": sku["standard_cents"],
                        "group_cents": sku["group_cents"],
                    }
                    for sku in skus
                ],
            }
            prompt_product["offers"].append(prompt_offer)
        if prompt_product["offers"]:
            prompt_catalog.append(prompt_product)
    offer_count = sum(len(product["offers"]) for product in products.values())
    return {
        "pair_map": pair_map,
        "catalog": prompt_catalog,
        "product_count": len(prompt_catalog),
        "offer_count": offer_count,
        "sku_count": len(pair_map),
    }


def public_selection(pair: dict[str, Any], mode: str) -> dict[str, Any]:
    product = pair["product"]
    offer = pair["offer"]
    sku = pair["sku"]
    if mode == "standard":
        unit_price = int(sku["standard_cents"])
    elif mode == "group" and offer.get("group") is not None and sku.get("group_cents") is not None:
        unit_price = int(sku["group_cents"])
    else:
        raise ValueError("unsupported buy mode")
    return {
        "offer_id": int(offer["offer_id"]),
        "sku_id": int(sku["sku_id"]),
        "quantity": 1,
        "buy_mode": mode,
        "product": {
            "slug": product["slug"],
            "title": product["title"],
            "cover": product["cover"],
            "brand": product["brand"],
            "category": product["category"],
            "tags": product["tags"],
        },
        "shop": {
            "id": offer["shop"]["id"],
            "slug": offer["shop"]["slug"],
            "name": offer["shop"]["name"],
            "kind": offer["shop"]["kind"],
            "verified": offer["shop"]["verified"],
        },
        "offer": {
            "id": offer["offer_id"],
            "name": offer["name"],
            "kind": offer["kind"],
            "stock": offer["stock"],
            "delivery_eta_days": offer["delivery_eta_days"],
            "shipping_fee_cents": offer["shipping_fee_cents"],
            "group": (
                {
                    "size": offer["group"]["size"],
                    "price_cents": offer["group"]["price_cents"],
                    "stock": offer["group"]["stock"],
                }
                if offer["group"] is not None
                else None
            ),
        },
        "sku": {"id": sku["sku_id"], "name": sku["name"]},
        "unit_price_cents": unit_price,
        "market_price_cents": int(sku["market_cents"]),
        "line_total_cents": unit_price,
    }
