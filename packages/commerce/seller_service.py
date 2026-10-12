"""Merchant workbench domain logic.

All reads and writes in this module are scoped by the authenticated seller's
shop. Buyer identity is minimized to a masked display name; address snapshots
never leave this module.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, time as daytime, timedelta, timezone
from typing import Any

from packages.commerce import db as dbutil
from packages.commerce import models
from packages.commerce.web import ApiError

CST = timezone(timedelta(hours=8))
RANGE_DAYS = 7
LOW_STOCK_THRESHOLD = 5
MAX_STOCK = 1_000_000
MAX_SEARCH_LENGTH = 60
_MAX_DECISION_NOTE = 200
_TRACKING_RE = re.compile(r"^[A-Za-z0-9]{6,24}$")

_PAID_ORDER_STATUSES = ("GROUP_FORMING", "PAID", "SHIPPED", "COMPLETED", "REFUNDING")
_VISIBLE_ORDER_STATUSES = ("PAID", "SHIPPED", "COMPLETED", "REFUNDING", "REFUNDED")
_ORDER_FILTERS = {"ALL", "PENDING", "SHIPPED"}
_OFFER_STATUS_LABELS = {"active": "在售", "paused": "已下架"}
_REFUND_KIND_LABELS = {"refund_only": "仅退款", "return_refund": "退货退款"}
_REFUND_STATUS_LABELS = {
    "REQUESTED": "待处理",
    "APPROVED": "已同意",
    "REJECTED": "已驳回",
    "CANCELLED": "已取消",
}
_REFUND_FILTERS = {"ALL", *_REFUND_STATUS_LABELS}
_PATCH_FIELDS = {"price_cents", "stock", "status"}
_RESTORABLE_ORDER_STATUSES = {"PAID", "SHIPPED", "COMPLETED"}


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def seller_shop(conn: sqlite3.Connection, seller_row: sqlite3.Row) -> sqlite3.Row:
    shop_id = seller_row["shop_id"]
    if shop_id is None:
        raise ApiError(403, "FORBIDDEN", "商家账号尚未绑定店铺")
    shop = dbutil.one(conn, "SELECT * FROM shops WHERE id=?", (int(shop_id),))
    if shop is None:
        raise ApiError(403, "FORBIDDEN", "商家店铺不存在")
    return shop


def _now_utc(server_time: str | None = None) -> datetime:
    if server_time:
        try:
            return dbutil.parse_iso(server_time).astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass
    return dbutil.utc_now()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return dbutil.parse_iso(value).astimezone(CST).date()
    except (ValueError, TypeError):
        return None


def _masked_receiver(address_json: str | None) -> str:
    if not address_json:
        return "匿名买家"
    try:
        snapshot = json.loads(address_json)
    except (TypeError, ValueError):
        return "匿名买家"
    if not isinstance(snapshot, dict):
        return "匿名买家"
    receiver = snapshot.get("receiver")
    return models.mask_name(receiver.strip()) if isinstance(receiver, str) and receiver.strip() else "匿名买家"


def _money_sum(conn: sqlite3.Connection, package_ids: list[int], *, shop_id: int | None = None) -> dict[int, int]:
    if not package_ids:
        return {}
    placeholders = ",".join("?" for _ in package_ids)
    params: list[Any] = list(package_ids)
    shop_clause = ""
    if shop_id is not None:
        shop_clause = " AND shop_id=?"
        params.append(int(shop_id))
    rows = dbutil.all_rows(
        conn,
        f"""SELECT package_id, SUM(unit_price_cents * quantity) AS total
            FROM order_items WHERE package_id IN ({placeholders}){shop_clause}
            GROUP BY package_id""",
        tuple(params),
    )
    return {int(row["package_id"]): int(row["total"] or 0) for row in rows}


def _delta_pct(current: int, previous: int) -> float | None:
    if previous == 0:
        return 0.0 if current == 0 else None
    return round((current - previous) * 100.0 / previous, 1)


def _sku_names(conn: sqlite3.Connection, product_id: int) -> list[str]:
    rows = dbutil.all_rows(conn, "SELECT name FROM skus WHERE product_id=? ORDER BY sort_order,id", (int(product_id),))
    return [str(row["name"]) for row in rows]


def _sku_hint(names: list[str]) -> str:
    if not names:
        return "暂无规格"
    if len(names) <= 2:
        return "、".join(names)
    return names[0] + " 等 " + str(len(names)) + " 个规格"


def _offer_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    names = _sku_names(conn, int(row["product_id"]))
    group_price = int(row["group_price_cents"]) if row["group_price_cents"] is not None else None
    min_price = group_price + 1 if group_price is not None else 1
    market_price = int(row["market_price_cents"])
    if min_price > market_price:
        min_price = market_price
    return {
        "offer_id": int(row["id"]),
        "product_id": int(row["product_id"]),
        "product_slug": row["product_slug"],
        "product_title": row["product_title"],
        "product_brand": row["product_brand"],
        "cover": row["cover"],
        "cover_alt": row["cover_alt"],
        "name": row["name"],
        "kind": row["kind"],
        "kind_label": models.KIND_LABELS.get(row["kind"], row["kind"]),
        "is_primary": bool(row["is_primary"]),
        "price_cents": int(row["price_cents"]),
        "market_price_cents": market_price,
        "stock": int(row["stock"]),
        "status": row["status"],
        "status_label": _OFFER_STATUS_LABELS.get(row["status"], row["status"]),
        "group_size": int(row["group_size"]) if row["group_size"] is not None else None,
        "group_price_cents": group_price,
        "min_price_cents": min_price,
        "max_price_cents": market_price,
        "sku_hint": _sku_hint(names),
        "skus": [
            {
                "id": int(sku["id"]),
                "name": sku["name"],
                "options": dbutil.json_loads(sku["options_json"], {}),
                "price_delta_cents": int(sku["price_delta_cents"]),
            }
            for sku in dbutil.all_rows(
                conn,
                "SELECT * FROM skus WHERE product_id=? ORDER BY sort_order,id",
                (int(row["product_id"]),),
            )
        ],
        "low_stock": bool(row["status"] == "active" and int(row["stock"]) <= LOW_STOCK_THRESHOLD),
        "updated_at": row["updated_at"],
    }


def _offer_row(conn: sqlite3.Connection, offer_id: int, shop_id: int) -> sqlite3.Row | None:
    return dbutil.one(
        conn,
        """SELECT o.*, p.title AS product_title, p.brand AS product_brand, p.slug AS product_slug,
                  p.cover AS cover, p.cover_alt AS cover_alt
           FROM offers o JOIN products p ON p.id=o.product_id
           WHERE o.id=? AND o.shop_id=?""",
        (int(offer_id), int(shop_id)),
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def _current_window(now: datetime) -> tuple[date, list[str], datetime, datetime, datetime]:
    local_now = now.astimezone(CST)
    today = local_now.date()
    start_date = today - timedelta(days=RANGE_DAYS - 1)
    labels = [(start_date + timedelta(days=offset)).strftime("%m-%d") for offset in range(RANGE_DAYS)]
    current_start = datetime.combine(start_date, daytime.min, tzinfo=CST)
    current_end = datetime.combine(today + timedelta(days=1), daytime.min, tzinfo=CST)
    previous_start = current_start - timedelta(days=RANGE_DAYS)
    return start_date, labels, current_start, current_end, previous_start


def _paid_rows(conn: sqlite3.Connection, shop_id: int, lower: str, upper: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in _PAID_ORDER_STATUSES)
    return dbutil.all_rows(
        conn,
        f"""SELECT o.id AS order_id, o.paid_at, oi.unit_price_cents, oi.quantity
            FROM orders o JOIN order_items oi ON oi.order_id=o.id
            WHERE oi.shop_id=? AND o.status IN ({placeholders})
              AND o.paid_at IS NOT NULL AND o.paid_at>=? AND o.paid_at<?
            ORDER BY o.paid_at, o.id""",
        (int(shop_id), *_PAID_ORDER_STATUSES, lower, upper),
    )


def _view_rows(conn: sqlite3.Connection, shop_id: int, lower: str, upper: str) -> list[sqlite3.Row]:
    return dbutil.all_rows(
        conn,
        """SELECT be.created_at FROM browse_events be
           WHERE be.created_at>=? AND be.created_at<?
             AND EXISTS (SELECT 1 FROM offers o WHERE o.product_id=be.product_id AND o.shop_id=?)
           ORDER BY be.created_at""",
        (lower, upper, int(shop_id)),
    )


def _low_stock_rows(conn: sqlite3.Connection, shop_id: int, *, limit: int | None = None) -> list[sqlite3.Row]:
    sql = """SELECT o.id, o.name AS offer_name, o.stock, o.price_cents, p.id AS product_id,
                    p.title AS product_title, p.slug AS product_slug
             FROM offers o JOIN products p ON p.id=o.product_id
             WHERE o.shop_id=? AND o.status='active' AND o.stock<=?
             ORDER BY o.stock ASC, o.id ASC"""
    params: list[Any] = [int(shop_id), LOW_STOCK_THRESHOLD]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return dbutil.all_rows(conn, sql, tuple(params))


def _recent_packages(conn: sqlite3.Connection, shop_id: int, *, limit: int = 6) -> list[dict[str, Any]]:
    statuses = ",".join("?" for _ in _VISIBLE_ORDER_STATUSES)
    rows = dbutil.all_rows(
        conn,
        f"""SELECT p.id AS package_id, p.status AS package_status, p.carrier, p.tracking_no,
                   p.created_at AS package_created_at,
                   o.id AS order_id, o.order_no, o.status AS order_status, o.address_json,
                   o.created_at AS order_created_at
            FROM order_packages p JOIN orders o ON o.id=p.order_id
            WHERE p.shop_id=? AND p.status!='CANCELLED' AND o.status IN ({statuses})
            ORDER BY o.created_at DESC, p.id DESC
            LIMIT ?""",
        (int(shop_id), *_VISIBLE_ORDER_STATUSES, int(limit)),
    )
    if not rows:
        return []
    package_ids = [int(row["package_id"]) for row in rows]
    placeholders = ",".join("?" for _ in package_ids)
    item_rows = dbutil.all_rows(
        conn,
        f"SELECT package_id, title FROM order_items WHERE shop_id=? AND package_id IN ({placeholders}) ORDER BY id",
        (int(shop_id), *package_ids),
    )
    titles_by_package: dict[int, list[str]] = {pid: [] for pid in package_ids}
    for item in item_rows:
        titles = titles_by_package.setdefault(int(item["package_id"]), [])
        if item["title"] not in titles:
            titles.append(item["title"])
    totals = _money_sum(conn, package_ids, shop_id=int(shop_id))
    payload = []
    for row in rows:
        pid = int(row["package_id"])
        payload.append(
            {
                "order_no": row["order_no"],
                "package_id": pid,
                "status": row["order_status"],
                "status_label": models.status_label(row["order_status"]),
                "package_status": row["package_status"],
                "package_status_label": models.PACKAGE_STATUS_LABELS.get(row["package_status"], row["package_status"]),
                "buyer_display": _masked_receiver(row["address_json"]),
                "total_cents": totals.get(pid, 0),
                "created_at": row["order_created_at"],
                "item_titles": titles_by_package.get(pid, [])[:3],
                "carrier": row["carrier"],
                "tracking_no": row["tracking_no"],
            }
        )
    return payload


def overview(conn: sqlite3.Connection, seller_row: sqlite3.Row, *, server_time: str | None = None) -> dict[str, Any]:
    shop = seller_shop(conn, seller_row)
    shop_id = int(shop["id"])
    now = _now_utc(server_time)
    start_date, labels, current_start, current_end, previous_start = _current_window(now)
    date_to_index = {start_date + timedelta(days=index): index for index in range(RANGE_DAYS)}
    previous_date_to_index = {previous_start.date() + timedelta(days=index): index for index in range(RANGE_DAYS)}
    lower = dbutil.iso(previous_start)
    upper = dbutil.iso(current_end)

    gmv_current = [0] * RANGE_DAYS
    gmv_previous = [0] * RANGE_DAYS
    order_sets_current: list[set[int]] = [set() for _ in range(RANGE_DAYS)]
    order_sets_previous: list[set[int]] = [set() for _ in range(RANGE_DAYS)]
    for row in _paid_rows(conn, shop_id, lower, upper):
        paid_date = _parse_date(row["paid_at"])
        if paid_date is None:
            continue
        line_total = int(row["unit_price_cents"]) * int(row["quantity"])
        order_id = int(row["order_id"])
        current_index = date_to_index.get(paid_date)
        if current_index is not None:
            gmv_current[current_index] += line_total
            order_sets_current[current_index].add(order_id)
            continue
        previous_index = previous_date_to_index.get(paid_date)
        if previous_index is not None:
            gmv_previous[previous_index] += line_total
            order_sets_previous[previous_index].add(order_id)

    views_current = [0] * RANGE_DAYS
    views_previous = [0] * RANGE_DAYS
    for row in _view_rows(conn, shop_id, lower, upper):
        view_date = _parse_date(row["created_at"])
        if view_date is None:
            continue
        current_index = date_to_index.get(view_date)
        if current_index is not None:
            views_current[current_index] += 1
            continue
        previous_index = previous_date_to_index.get(view_date)
        if previous_index is not None:
            views_previous[previous_index] += 1

    current_order_count = sum(len(bucket) for bucket in order_sets_current)
    previous_order_count = sum(len(bucket) for bucket in order_sets_previous)
    current_views = sum(views_current)
    previous_views = sum(views_previous)
    current_gmv = sum(gmv_current)
    previous_gmv = sum(gmv_previous)
    order_series = [len(bucket) for bucket in order_sets_current]

    low_stock_rows = _low_stock_rows(conn, shop_id, limit=6)
    low_stock = [
        {
            "offer_id": int(row["id"]),
            "offer_name": row["offer_name"],
            "product_id": int(row["product_id"]),
            "product_title": row["product_title"],
            "product_slug": row["product_slug"],
            "sku_hint": _sku_hint(_sku_names(conn, int(row["product_id"]))),
            "stock": int(row["stock"]),
            "price_cents": int(row["price_cents"]),
        }
        for row in low_stock_rows
    ]
    low_stock_count = len(_low_stock_rows(conn, shop_id))
    to_ship = int(
        dbutil.scalar(
            conn,
            """SELECT COUNT(*) FROM order_packages p JOIN orders o ON o.id=p.order_id
               WHERE p.shop_id=? AND p.status='PENDING' AND o.status='PAID'""",
            (shop_id,),
        )
        or 0
    )
    open_refunds = int(
        dbutil.scalar(conn, "SELECT COUNT(*) FROM refunds WHERE shop_id=? AND status='REQUESTED'", (shop_id,)) or 0
    )

    return {
        "shop": models.shop_payload(shop),
        "range_days": RANGE_DAYS,
        "metrics": [
            {
                "key": "gmv",
                "label": "近 7 天成交额",
                "value_cents": current_gmv,
                "previous_cents": previous_gmv,
                "delta_pct": _delta_pct(current_gmv, previous_gmv),
                "spark": gmv_current,
            },
            {
                "key": "orders",
                "label": "近 7 天成交单",
                "value": current_order_count,
                "previous": previous_order_count,
                "delta_pct": _delta_pct(current_order_count, previous_order_count),
                "spark": order_series,
            },
            {
                "key": "views",
                "label": "近 7 天商品浏览",
                "value": current_views,
                "previous": previous_views,
                "delta_pct": _delta_pct(current_views, previous_views),
                "spark": views_current,
            },
        ],
        "series": {
            "labels": labels,
            "gmv_cents": gmv_current,
            "order_counts": order_series,
        },
        "todos": {
            "to_ship": to_ship,
            "refunds": open_refunds,
            "low_stock": low_stock_count,
        },
        "low_stock": low_stock,
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
        "recent_packages": _recent_packages(conn, shop_id, limit=6),
    }


# ---------------------------------------------------------------------------
# Products / offers
# ---------------------------------------------------------------------------


def _product_where(shop_id: int, query: str) -> tuple[str, list[Any]]:
    where = ["o.shop_id=?"]
    params: list[Any] = [int(shop_id)]
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        where.append("(p.title LIKE ? ESCAPE '\\' OR p.brand LIKE ? ESCAPE '\\' OR o.name LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern, pattern])
    return " AND ".join(where), params


def list_products(
    conn: sqlite3.Connection,
    shop_id: int,
    *,
    query: str = "",
    page: int = 1,
    page_size: int = 12,
) -> dict[str, Any]:
    query = (query or "").strip()
    if len(query) > MAX_SEARCH_LENGTH:
        raise ApiError(400, "VALIDATION_ERROR", "搜索词过长", {"q": f"最多 {MAX_SEARCH_LENGTH} 个字符"})
    where_sql, params = _product_where(int(shop_id), query)
    total = int(
        dbutil.scalar(
            conn,
            f"SELECT COUNT(*) FROM offers o JOIN products p ON p.id=o.product_id WHERE {where_sql}",
            tuple(params),
        )
        or 0
    )
    pages = (total + page_size - 1) // page_size if total else 0
    offset = (page - 1) * page_size
    rows = dbutil.all_rows(
        conn,
        f"""SELECT o.*, p.title AS product_title, p.brand AS product_brand, p.slug AS product_slug,
                   p.cover AS cover, p.cover_alt AS cover_alt
            FROM offers o JOIN products p ON p.id=o.product_id
            WHERE {where_sql}
            ORDER BY p.featured DESC, p.id ASC, o.is_primary DESC, o.id ASC
            LIMIT ? OFFSET ?""",
        (*params, int(page_size), int(offset)),
    )
    return {
        "items": [_offer_payload(conn, row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "query": query,
    }


def _bad_field(field: str, message: str) -> ApiError:
    return ApiError(400, "VALIDATION_ERROR", message, {field: message})


def _validate_patch(row: sqlite3.Row, patch: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(patch) - _PATCH_FIELDS)
    if unknown:
        raise _bad_field(unknown[0], "不支持修改该字段")
    if not patch:
        raise ApiError(400, "VALIDATION_ERROR", "至少提交一个需要修改的字段")
    values: dict[str, Any] = {}
    if "price_cents" in patch:
        price = patch["price_cents"]
        if isinstance(price, bool) or not isinstance(price, int):
            raise _bad_field("price_cents", "价格必须是整数分")
        group_price = int(row["group_price_cents"]) if row["group_price_cents"] is not None else None
        floor = group_price + 1 if group_price is not None else 1
        ceiling = int(row["market_price_cents"])
        if price < 1 or price > ceiling or price < floor:
            if group_price is not None:
                message = f"价格范围 {floor}-{ceiling} 分，且必须低于划线价"
            else:
                message = f"价格范围 1-{ceiling} 分"
            raise _bad_field("price_cents", message)
        values["price_cents"] = price
    if "stock" in patch:
        stock = patch["stock"]
        if isinstance(stock, bool) or not isinstance(stock, int):
            raise _bad_field("stock", "库存必须是整数")
        if stock < 0 or stock > MAX_STOCK:
            raise _bad_field("stock", f"库存范围 0-{MAX_STOCK}")
        values["stock"] = stock
    if "status" in patch:
        status = patch["status"]
        if status not in {"active", "paused"}:
            raise _bad_field("status", "状态只能是 active 或 paused")
        values["status"] = status
    if not values:
        raise ApiError(400, "VALIDATION_ERROR", "至少提交一个需要修改的字段")
    return values


def update_product(
    conn: sqlite3.Connection,
    shop_id: int,
    offer_id: int,
    patch: dict[str, Any],
) -> dict[str, Any]:
    with dbutil.transaction(conn):
        row = _offer_row(conn, int(offer_id), int(shop_id))
        if row is None:
            raise ApiError(404, "NOT_FOUND", "商品报价不存在或不属于当前店铺")
        values = _validate_patch(row, patch)
        updated_at = dbutil.iso()
        assignments = [f"{field}=?" for field in values]
        conn.execute(
            f"UPDATE offers SET {', '.join(assignments)}, updated_at=? WHERE id=? AND shop_id=?",
            (*values.values(), updated_at, int(offer_id), int(shop_id)),
        )
    fresh = _offer_row(conn, int(offer_id), int(shop_id))
    if fresh is None:
        raise ApiError(404, "NOT_FOUND", "商品报价不存在或不属于当前店铺")
    return _offer_payload(conn, fresh)


# ---------------------------------------------------------------------------
# Orders / shipping
# ---------------------------------------------------------------------------


def _order_filter(value: str | None) -> str:
    status = (value or "ALL").strip().upper()
    if status not in _ORDER_FILTERS:
        raise ApiError(400, "VALIDATION_ERROR", "订单筛选仅支持 PENDING、SHIPPED、ALL", {"status": "筛选值无效"})
    return status


def _orders_base_sql() -> str:
    visible = ",".join("?" for _ in _VISIBLE_ORDER_STATUSES)
    return f"""FROM order_packages p JOIN orders o ON o.id=p.order_id
               WHERE p.shop_id=? AND p.status!='CANCELLED' AND o.status IN ({visible})"""


def _orders_filter_sql(status: str) -> str:
    if status == "PENDING":
        return " AND p.status='PENDING' AND o.status='PAID'"
    if status == "SHIPPED":
        return " AND p.status IN ('SHIPPED','DELIVERED')"
    return ""


def _package_items(conn: sqlite3.Connection, package_ids: list[int], shop_id: int) -> dict[int, list[dict[str, Any]]]:
    if not package_ids:
        return {}
    placeholders = ",".join("?" for _ in package_ids)
    rows = dbutil.all_rows(
        conn,
        f"""SELECT package_id, title, cover, sku_label, quantity, unit_price_cents, offer_kind
            FROM order_items WHERE shop_id=? AND package_id IN ({placeholders}) ORDER BY id""",
        (int(shop_id), *package_ids),
    )
    grouped: dict[int, list[dict[str, Any]]] = {int(pid): [] for pid in package_ids}
    for row in rows:
        grouped[int(row["package_id"])].append(
            {
                "title": row["title"],
                "cover": row["cover"],
                "sku_label": row["sku_label"],
                "quantity": int(row["quantity"]),
                "unit_price_cents": int(row["unit_price_cents"]),
                "offer_kind": row["offer_kind"],
            }
        )
    return grouped


def list_orders(
    conn: sqlite3.Connection,
    shop_id: int,
    *,
    status: str | None = "ALL",
    page: int = 1,
    page_size: int = 12,
) -> dict[str, Any]:
    selected = _order_filter(status)
    visible = tuple(_VISIBLE_ORDER_STATUSES)
    base_sql = _orders_base_sql()
    counts_row = dbutil.one(
        conn,
        f"""SELECT COUNT(*) AS all_count,
                   COALESCE(SUM(CASE WHEN p.status='PENDING' AND o.status='PAID' THEN 1 ELSE 0 END),0) AS pending_count,
                   COALESCE(SUM(CASE WHEN p.status IN ('SHIPPED','DELIVERED') THEN 1 ELSE 0 END),0) AS shipped_count
            {base_sql}""",
        (int(shop_id), *visible),
    )
    counts = {
        "PENDING": int(counts_row["pending_count"]) if counts_row else 0,
        "SHIPPED": int(counts_row["shipped_count"]) if counts_row else 0,
        "ALL": int(counts_row["all_count"]) if counts_row else 0,
    }
    filter_sql = _orders_filter_sql(selected)
    params: tuple[Any, ...] = (int(shop_id), *visible)
    total = int(dbutil.scalar(conn, f"SELECT COUNT(*) {base_sql}{filter_sql}", params) or 0)
    rows = dbutil.all_rows(
        conn,
        f"""SELECT p.id AS package_id, p.status AS package_status, p.carrier, p.tracking_no,
                   p.shipping_method, p.shipped_at, p.created_at AS package_created_at,
                   o.order_no, o.status AS order_status, o.address_json, o.created_at AS order_created_at,
                   o.paid_at
            {base_sql}{filter_sql}
            ORDER BY CASE WHEN p.status='PENDING' THEN 0 ELSE 1 END, o.created_at DESC, p.id DESC
            LIMIT ? OFFSET ?""",
        (*params, int(page_size), int((page - 1) * page_size)),
    )
    package_ids = [int(row["package_id"]) for row in rows]
    items_by_package = _package_items(conn, package_ids, int(shop_id))
    items = []
    for row in rows:
        pid = int(row["package_id"])
        lines = items_by_package.get(pid, [])
        total_cents = sum(line["unit_price_cents"] * line["quantity"] for line in lines)
        can_ship = row["package_status"] == "PENDING" and row["order_status"] == "PAID"
        items.append(
            {
                "package_id": pid,
                "order_no": row["order_no"],
                "order_status": row["order_status"],
                "order_status_label": models.status_label(row["order_status"]),
                "package_status": row["package_status"],
                "package_status_label": models.PACKAGE_STATUS_LABELS.get(row["package_status"], row["package_status"]),
                "buyer_display": _masked_receiver(row["address_json"]),
                "created_at": row["order_created_at"],
                "paid_at": row["paid_at"],
                "shipped_at": row["shipped_at"],
                "carrier": row["carrier"],
                "tracking_no": row["tracking_no"],
                "shipping_method": row["shipping_method"],
                "items": lines,
                "item_count": sum(line["quantity"] for line in lines),
                "total_cents": total_cents,
                "can_ship": can_ship,
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "status": selected,
        "counts": counts,
        "carriers": list(models.CARRIERS),
    }


def _validate_shipment(carrier: Any, tracking_no: Any) -> tuple[str, str]:
    if not isinstance(carrier, str) or carrier not in models.CARRIERS:
        raise _bad_field("carrier", "请选择青鸟速运、江海快递或晨风同城")
    if not isinstance(tracking_no, str):
        raise _bad_field("tracking_no", "物流单号必须是 6-24 位字母或数字")
    tracking_no = tracking_no.strip()
    if not _TRACKING_RE.fullmatch(tracking_no):
        raise _bad_field("tracking_no", "物流单号必须是 6-24 位字母或数字")
    return carrier, tracking_no


def ship_package(
    conn: sqlite3.Connection,
    shop_id: int,
    package_id: int,
    *,
    carrier: Any,
    tracking_no: Any,
) -> dict[str, Any]:
    carrier, tracking_no = _validate_shipment(carrier, tracking_no)
    shop = dbutil.one(conn, "SELECT name FROM shops WHERE id=?", (int(shop_id),))
    shop_name = shop["name"] if shop else "店铺"
    shipped_at = dbutil.iso()
    final_order_status = ""
    all_shipped = False
    order_no = ""
    with dbutil.transaction(conn):
        package = dbutil.one(
            conn,
            """SELECT p.*, o.status AS order_status, o.order_no
               FROM order_packages p JOIN orders o ON o.id=p.order_id
               WHERE p.id=? AND p.shop_id=?""",
            (int(package_id), int(shop_id)),
        )
        if package is None:
            raise ApiError(404, "NOT_FOUND", "包裹不存在或不属于当前店铺")
        order_no = package["order_no"]
        if package["status"] in {"SHIPPED", "DELIVERED"}:
            raise ApiError(409, "ORDER_STATE", "这个包裹已经发货，请勿重复提交")
        if package["status"] != "PENDING":
            raise ApiError(409, "ORDER_STATE", "当前包裹状态不可发货")
        if package["order_status"] == "GROUP_FORMING":
            raise ApiError(409, "ORDER_STATE", "拼团尚未成团，暂时不能发货")
        if package["order_status"] == "REFUNDING":
            raise ApiError(409, "ORDER_STATE", "订单正在售后处理中，暂时不能发货")
        if package["order_status"] != "PAID":
            raise ApiError(409, "ORDER_STATE", "只有已付款待发货的订单可以发货")
        updated = conn.execute(
            """UPDATE order_packages
               SET status='SHIPPED', carrier=?, tracking_no=?, shipped_at=?, updated_at=?
               WHERE id=? AND shop_id=? AND status='PENDING'""",
            (carrier, tracking_no, shipped_at, shipped_at, int(package_id), int(shop_id)),
        )
        if updated.rowcount != 1:
            raise ApiError(409, "ORDER_STATE", "包裹状态已变化，请刷新后重试")
        conn.execute(
            "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
            (
                int(package["order_id"]),
                int(package_id),
                "商家已发货",
                f"{shop_name} · {carrier} {tracking_no}",
                shipped_at,
            ),
        )
        remaining = int(
            dbutil.scalar(
                conn,
                "SELECT COUNT(*) FROM order_packages WHERE order_id=? AND status NOT IN ('SHIPPED','DELIVERED')",
                (int(package["order_id"]),),
            )
            or 0
        )
        all_shipped = remaining == 0
        if all_shipped:
            moved = conn.execute(
                "UPDATE orders SET status='SHIPPED', shipped_at=?, updated_at=? WHERE id=? AND status='PAID'",
                (shipped_at, shipped_at, int(package["order_id"])),
            )
            if moved.rowcount == 1:
                conn.execute(
                    "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
                    (int(package["order_id"]), None, "订单已全部发货", "所有包裹已交由物流", shipped_at),
                )
        final = dbutil.one(conn, "SELECT status, shipped_at FROM orders WHERE id=?", (int(package["order_id"]),))
        final_order_status = final["status"] if final else package["order_status"]
    return {
        "package_id": int(package_id),
        "order_no": order_no,
        "status": "SHIPPED",
        "status_label": models.PACKAGE_STATUS_LABELS["SHIPPED"],
        "carrier": carrier,
        "tracking_no": tracking_no,
        "shipped_at": shipped_at,
        "order_status": final_order_status,
        "order_status_label": models.status_label(final_order_status),
        "all_shipped": all_shipped,
    }


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


def _refund_filter(value: str | None) -> str:
    status = (value or "ALL").strip().upper()
    if status not in _REFUND_FILTERS:
        raise ApiError(400, "VALIDATION_ERROR", "售后筛选值无效", {"status": "筛选值无效"})
    return status


def _refund_items(conn: sqlite3.Connection, refund_rows: list[sqlite3.Row], shop_id: int) -> dict[int, list[dict[str, Any]]]:
    order_ids = sorted({int(row["order_id"]) for row in refund_rows})
    if not order_ids:
        return {}
    placeholders = ",".join("?" for _ in order_ids)
    rows = dbutil.all_rows(
        conn,
        f"""SELECT order_id, title, cover, sku_label, quantity, unit_price_cents
            FROM order_items WHERE shop_id=? AND order_id IN ({placeholders}) ORDER BY id""",
        (int(shop_id), *order_ids),
    )
    grouped: dict[int, list[dict[str, Any]]] = {oid: [] for oid in order_ids}
    for row in rows:
        grouped[int(row["order_id"])].append(
            {
                "title": row["title"],
                "cover": row["cover"],
                "sku_label": row["sku_label"],
                "quantity": int(row["quantity"]),
                "unit_price_cents": int(row["unit_price_cents"]),
            }
        )
    return grouped


def _refund_payload(conn: sqlite3.Connection, row: sqlite3.Row, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "order_no": row["order_no"],
        "status": row["status"],
        "status_label": _REFUND_STATUS_LABELS.get(row["status"], row["status"]),
        "kind": row["kind"],
        "kind_label": _REFUND_KIND_LABELS.get(row["kind"], row["kind"]),
        "amount_cents": int(row["amount_cents"]),
        "reason": row["reason"],
        "detail": row["detail"],
        "decision_note": row["decision_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "buyer_display": _masked_receiver(row["address_json"]),
        "order_status": row["order_status"],
        "order_status_label": models.status_label(row["order_status"]),
        "items": items,
        "item_count": sum(item["quantity"] for item in items),
        "can_decide": row["status"] == "REQUESTED",
    }


def list_refunds(
    conn: sqlite3.Connection,
    shop_id: int,
    *,
    status: str | None = "ALL",
    page: int = 1,
    page_size: int = 12,
) -> dict[str, Any]:
    selected = _refund_filter(status)
    counts_rows = dbutil.all_rows(
        conn,
        "SELECT status, COUNT(*) AS count FROM refunds WHERE shop_id=? GROUP BY status",
        (int(shop_id),),
    )
    counts = {key: 0 for key in _REFUND_STATUS_LABELS}
    for row in counts_rows:
        if row["status"] in counts:
            counts[row["status"]] = int(row["count"])
    counts["ALL"] = sum(counts.values())
    where_status = ""
    params: list[Any] = [int(shop_id)]
    if selected != "ALL":
        where_status = " AND r.status=?"
        params.append(selected)
    total = int(
        dbutil.scalar(conn, f"SELECT COUNT(*) FROM refunds r WHERE r.shop_id=?{where_status}", tuple(params)) or 0
    )
    rows = dbutil.all_rows(
        conn,
        f"""SELECT r.*, o.status AS order_status, o.address_json
            FROM refunds r JOIN orders o ON o.id=r.order_id
            WHERE r.shop_id=?{where_status}
            ORDER BY CASE WHEN r.status='REQUESTED' THEN 0 ELSE 1 END, r.created_at DESC, r.id DESC
            LIMIT ? OFFSET ?""",
        (*params, int(page_size), int((page - 1) * page_size)),
    )
    items_by_order = _refund_items(conn, rows, int(shop_id))
    return {
        "items": [_refund_payload(conn, row, items_by_order.get(int(row["order_id"]), [])) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "status": selected,
        "counts": counts,
    }


def _decision_fields(action: Any, note: Any) -> tuple[str, str]:
    if action not in {"approve", "reject"}:
        raise ApiError(400, "VALIDATION_ERROR", "请选择同意或驳回售后", {"action": "只能是 approve 或 reject"})
    if note is None:
        note = ""
    if not isinstance(note, str):
        raise _bad_field("note", "处理备注必须是文字")
    note = note.strip()
    if len(note) > _MAX_DECISION_NOTE:
        raise _bad_field("note", f"处理备注最多 {_MAX_DECISION_NOTE} 个字符")
    return action, note


def _restore_order_stock(conn: sqlite3.Connection, order_id: int, updated_at: str) -> int:
    rows = dbutil.all_rows(conn, "SELECT offer_id, quantity FROM order_items WHERE order_id=?", (int(order_id),))
    restored = 0
    for row in rows:
        quantity = int(row["quantity"])
        conn.execute(
            "UPDATE offers SET stock=stock+?, updated_at=? WHERE id=?",
            (quantity, updated_at, int(row["offer_id"])),
        )
        restored += quantity
    return restored


def _restore_order_coupon(conn: sqlite3.Connection, order: sqlite3.Row, now_iso: str) -> str | None:
    coupon_id = order["user_coupon_id"]
    if coupon_id is None:
        return None
    coupon = dbutil.one(
        conn,
        "SELECT * FROM user_coupons WHERE id=? AND user_id=?",
        (int(coupon_id), int(order["user_id"])),
    )
    if coupon is None or coupon["status"] != "USED":
        return None
    if coupon["order_id"] is not None and int(coupon["order_id"]) != int(order["id"]):
        return None
    restored_status = "EXPIRED" if coupon["expires_at"] and coupon["expires_at"] <= now_iso else "AVAILABLE"
    conn.execute(
        "UPDATE user_coupons SET status=?, used_at=NULL, order_id=NULL WHERE id=? AND status='USED'",
        (restored_status, int(coupon_id)),
    )
    return restored_status


def decide_refund(
    conn: sqlite3.Connection,
    shop_id: int,
    refund_id: int,
    *,
    action: Any,
    note: Any = "",
) -> dict[str, Any]:
    action, note = _decision_fields(action, note)
    now_iso = dbutil.iso()
    restored_units = 0
    coupon_restored = None
    with dbutil.transaction(conn):
        row = dbutil.one(
            conn,
            """SELECT r.*, o.status AS order_status, o.address_json, o.user_id AS order_user_id,
                      o.user_coupon_id, o.coupon_code, o.last_state_before_refund
               FROM refunds r JOIN orders o ON o.id=r.order_id
               WHERE r.id=? AND r.shop_id=?""",
            (int(refund_id), int(shop_id)),
        )
        if row is None:
            raise ApiError(404, "NOT_FOUND", "售后申请不存在或不属于当前店铺")
        if row["status"] != "REQUESTED":
            raise ApiError(409, "ORDER_STATE", "该售后已经处理，不能重复决定")
        order = dbutil.one(conn, "SELECT * FROM orders WHERE id=?", (int(row["order_id"]),))
        if order is None:
            raise ApiError(404, "NOT_FOUND", "售后关联订单不存在")
        if order["status"] != "REFUNDING":
            raise ApiError(409, "ORDER_STATE", "订单当前不在售后处理中，无法决定")
        if action == "approve":
            moved = conn.execute(
                """UPDATE orders SET status='REFUNDED', refunded_at=?, updated_at=?
                   WHERE id=? AND status='REFUNDING'""",
                (now_iso, now_iso, int(order["id"])),
            )
            if moved.rowcount != 1:
                raise ApiError(409, "ORDER_STATE", "订单状态已变化，请刷新后重试")
            restored_units = _restore_order_stock(conn, int(order["id"]), now_iso)
            coupon_restored = _restore_order_coupon(conn, order, now_iso)
            detail = f"售后 #{int(refund_id)} 已同意" + (f" · {note}" if note else "")
            conn.execute(
                "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
                (int(order["id"]), None, "商家同意退款", detail, now_iso),
            )
            next_status = "APPROVED"
        else:
            next_status = "REJECTED"
            previous = order["last_state_before_refund"]
            if previous not in _RESTORABLE_ORDER_STATUSES:
                raise ApiError(409, "ORDER_STATE", "订单缺少退款前状态，无法驳回")
            moved = conn.execute(
                """UPDATE orders SET status=?, last_state_before_refund=NULL, refunded_at=NULL, updated_at=?
                   WHERE id=? AND status='REFUNDING'""",
                (previous, now_iso, int(order["id"])),
            )
            if moved.rowcount != 1:
                raise ApiError(409, "ORDER_STATE", "订单状态已变化，请刷新后重试")
            detail = f"售后 #{int(refund_id)} 已驳回" + (f" · {note}" if note else "")
            conn.execute(
                "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
                (int(order["id"]), None, "商家驳回退款", detail, now_iso),
            )
        changed = conn.execute(
            "UPDATE refunds SET status=?, decision_note=?, updated_at=? WHERE id=? AND shop_id=? AND status='REQUESTED'",
            (next_status, note, now_iso, int(refund_id), int(shop_id)),
        )
        if changed.rowcount != 1:
            raise ApiError(409, "ORDER_STATE", "该售后状态已变化，请刷新后重试")
        refreshed_order = dbutil.one(conn, "SELECT status FROM orders WHERE id=?", (int(row["order_id"]),))
    fresh = dbutil.one(
        conn,
        """SELECT r.*, o.status AS order_status, o.address_json
           FROM refunds r JOIN orders o ON o.id=r.order_id
           WHERE r.id=? AND r.shop_id=?""",
        (int(refund_id), int(shop_id)),
    )
    if fresh is None:
        raise ApiError(404, "NOT_FOUND", "售后申请不存在或不属于当前店铺")
    payload = _refund_payload(conn, fresh, _refund_items(conn, [fresh], int(shop_id)).get(int(row["order_id"]), []))
    payload["action"] = action
    payload["inventory_restored_units"] = restored_units
    payload["coupon_restored_status"] = coupon_restored
    payload["order_status"] = refreshed_order["status"] if refreshed_order else payload["order_status"]
    payload["order_status_label"] = models.status_label(payload["order_status"])
    return payload
