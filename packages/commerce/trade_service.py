"""Trade domain service: cart, coupons, checkout, orders, groups and reviews.

The route module is intentionally thin. This service owns server-side pricing,
stock/coupon authority, transactional order creation and ownership checks.
All money is integer cents and all timestamps are UTC ISO-8601 strings.
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta
from typing import Any

from packages.commerce import db as dbutil, models
from packages.commerce.web import ApiError, RequestContext, validate_int, validate_text

CART_MAX_QUANTITY = 9999
GROUP_TTL_HOURS = 24
EXPRESS_FEE_CENTS = 1200
ORDER_TABS = ("all", "unpaid", "to_ship", "to_receive", "completed", "after_sale")
GROUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ORDER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
COUPON_CODE_RE = __import__("re").compile(r"[A-Z0-9]{3,30}")
GROUP_CODE_RE = __import__("re").compile(r"[A-Z0-9]{4,20}")
IDEMPOTENCY_RE = __import__("re").compile(r"[!-~]{16,64}")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return dbutil.iso()


def _after_hours_iso(hours: int) -> str:
    return dbutil.iso(dbutil.utc_now() + timedelta(hours=hours))


def _is_past(value: str | None) -> bool:
    if not value:
        return True
    try:
        return dbutil.parse_iso(value) <= dbutil.utc_now()
    except (TypeError, ValueError):
        return True


def _normalise_coupon_code(value: Any, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ApiError(400, "VALIDATION_ERROR", "请输入优惠码", {"coupon_code": "必填"})
        return None
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", "优惠码格式不正确", {"coupon_code": "必须是文字"})
    code = value.strip().upper()
    if not COUPON_CODE_RE.fullmatch(code):
        raise ApiError(400, "VALIDATION_ERROR", "优惠码格式不正确", {"coupon_code": "格式不正确"})
    return code


def _shipping_method(value: Any) -> str:
    if value not in ("standard", "express"):
        raise ApiError(400, "VALIDATION_ERROR", "请选择有效配送方式", {"shipping_method": "standard 或 express"})
    return str(value)


def _new_order_no(conn: sqlite3.Connection) -> str:
    for _ in range(30):
        stamp = dbutil.utc_now().strftime("%Y%m%d%H%M%S")
        suffix = "".join(secrets.choice(ORDER_CODE_ALPHABET) for _ in range(4))
        order_no = f"OS{stamp}{suffix}"
        if dbutil.one(conn, "SELECT 1 FROM orders WHERE order_no=?", (order_no,)) is None:
            return order_no
    raise ApiError(503, "ORDER_NUMBER_UNAVAILABLE", "无法生成订单号，请稍后重试")


def _new_group_code(conn: sqlite3.Connection) -> str:
    for _ in range(30):
        code = "G" + "".join(secrets.choice(GROUP_CODE_ALPHABET) for _ in range(6))
        if dbutil.one(conn, "SELECT 1 FROM groups WHERE code=?", (code,)) is None:
            return code
    raise ApiError(503, "GROUP_UNAVAILABLE", "无法创建拼团，请稍后重试")


def _add_event(
    conn: sqlite3.Connection,
    order_id: int,
    label: str,
    detail: str,
    *,
    package_id: int | None = None,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO order_events(order_id,package_id,label,detail,created_at) VALUES(?,?,?,?,?)",
        (order_id, package_id, label, detail, created_at or _now_iso()),
    )


def _require_active_refund_for_cancel(conn: sqlite3.Connection, order_id: int) -> None:
    active = dbutil.one(
        conn,
        "SELECT 1 FROM refunds WHERE order_id=? AND status='REQUESTED' LIMIT 1",
        (order_id,),
    )
    if active:
        raise ApiError(409, "ORDER_STATE", "售后处理中，暂时不能进行该操作")


# ---------------------------------------------------------------------------
# cart storage and serialization
# ---------------------------------------------------------------------------

_CART_ITEM_SELECT = """
SELECT ci.id AS item_id,
       ci.offer_id, ci.sku_id, ci.quantity, ci.selected, ci.buy_mode, ci.group_code,
       ci.created_at AS item_created_at, ci.updated_at AS item_updated_at,
       o.name AS offer_name, o.kind AS offer_kind, o.price_cents AS offer_price_cents,
       o.market_price_cents, o.shipping_fee_cents, o.stock, o.group_size,
       o.group_price_cents, o.delivery_eta_days, o.delivery_note, o.services_json,
       o.status AS offer_status,
       s.id AS shop_id, s.slug AS shop_slug, s.name AS shop_name, s.kind AS shop_kind,
       s.verified AS shop_verified, s.ship_from AS shop_ship_from,
       p.id AS product_id, p.slug AS product_slug, p.title AS product_title,
       p.brand AS product_brand, p.cover AS product_cover, p.cover_alt AS product_cover_alt,
       p.category_slug AS category_slug, p.status AS product_status,
       k.name AS sku_name, k.options_json AS sku_options_json,
       k.price_delta_cents AS sku_price_delta_cents
FROM cart_items ci
JOIN offers o ON o.id=ci.offer_id
JOIN shops s ON s.id=o.shop_id
JOIN products p ON p.id=o.product_id
JOIN skus k ON k.id=ci.sku_id AND k.product_id=o.product_id
WHERE ci.cart_id=?
"""


def _cart_id(conn: sqlite3.Connection, owner_key: str) -> int | None:
    row = dbutil.one(conn, "SELECT id FROM carts WHERE owner_key=?", (owner_key,))
    return int(row["id"]) if row else None


def _ensure_cart(conn: sqlite3.Connection, owner_key: str) -> int:
    now = _now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO carts(owner_key,created_at,updated_at) VALUES(?,?,?)",
        (owner_key, now, now),
    )
    cart = dbutil.one(conn, "SELECT id FROM carts WHERE owner_key=?", (owner_key,))
    if cart is None:
        raise ApiError(500, "CART_UNAVAILABLE", "购物车暂时不可用")
    return int(cart["id"])


def _cart_lines(conn: sqlite3.Connection, cart_id: int | None, *, selected_only: bool = False) -> list[sqlite3.Row]:
    if cart_id is None:
        return []
    sql = _CART_ITEM_SELECT
    if selected_only:
        sql += " AND ci.selected=1"
    sql += " ORDER BY ci.id"
    return dbutil.all_rows(conn, sql, (cart_id,))


def _line_unit_prices(row: sqlite3.Row) -> tuple[int, int]:
    delta = int(row["sku_price_delta_cents"])
    if row["buy_mode"] == "group":
        base = row["group_price_cents"]
        if base is None:
            raise ApiError(409, "GROUP_UNAVAILABLE", "该商品拼团报价已失效")
        unit = int(base) + delta
    else:
        unit = int(row["offer_price_cents"]) + delta
    market = int(row["market_price_cents"]) + delta
    if unit < 0 or market < 0:
        raise ApiError(409, "STOCK_CHANGED", "商品价格异常，请刷新购物车")
    return unit, market


def _line_total(row: sqlite3.Row) -> tuple[int, int]:
    unit, market = _line_unit_prices(row)
    quantity = int(row["quantity"])
    return unit * quantity, market * quantity


def _validate_selected_lines(conn: sqlite3.Connection, lines: list[sqlite3.Row], *, check_stock: bool = True) -> list[sqlite3.Row]:
    """Validate mode, sale status and stock for a selected checkout slice."""
    if not lines:
        raise ApiError(422, "CART_EMPTY", "请先选择要结算的商品")
    modes = {str(line["buy_mode"]) for line in lines}
    if len(modes) > 1:
        raise ApiError(422, "MIXED_BUY_MODE", "普通商品和拼团商品需分开结算")
    group_lines = [line for line in lines if line["buy_mode"] == "group"]
    if group_lines and len(group_lines) != 1:
        raise ApiError(422, "MIXED_BUY_MODE", "拼团商品一次只能结算一件，请只选择一个拼团商品")
    for line in lines:
        if line["offer_status"] != "active" or line["product_status"] != "active":
            raise ApiError(409, "STOCK_CHANGED", f"「{line['product_title']}」已下架，请从购物车移除")
        if check_stock:
            stock = int(line["stock"])
            quantity = int(line["quantity"])
            if stock <= 0 or quantity > stock:
                raise ApiError(409, "STOCK_CHANGED", f"「{line['product_title']}」库存不足，请调整数量")
        if line["buy_mode"] == "group":
            if int(line["quantity"]) != 1:
                raise ApiError(422, "MIXED_BUY_MODE", "拼团商品数量必须为 1")
            if line["group_size"] is None or line["group_price_cents"] is None:
                raise ApiError(409, "GROUP_UNAVAILABLE", "该商品暂无拼团报价")
    if check_stock:
        totals: dict[int, int] = {}
        for line in lines:
            offer_id = int(line["offer_id"])
            totals[offer_id] = totals.get(offer_id, 0) + int(line["quantity"])
        for offer_id, quantity in totals.items():
            line = next(line for line in lines if int(line["offer_id"]) == offer_id)
            if quantity > int(line["stock"]):
                raise ApiError(409, "STOCK_CHANGED", f"「{line['product_title']}」库存不足，请调整数量")
    return group_lines


def _validate_group_for_cart(
    conn: sqlite3.Connection,
    code: str,
    offer_id: int,
    sku_id: int,
    user_id: int | None,
) -> sqlite3.Row:
    group = dbutil.one(conn, "SELECT * FROM groups WHERE code=?", (code,))
    if group is None:
        raise ApiError(409, "GROUP_UNAVAILABLE", "拼团不存在或已结束")
    if group["offer_id"] != offer_id or group["sku_id"] != sku_id:
        raise ApiError(409, "GROUP_UNAVAILABLE", "这个团购的是另一个规格")
    if group["status"] != "FORMING" or _is_past(group["expires_at"]):
        raise ApiError(409, "GROUP_UNAVAILABLE", "这个拼团已结束，不能继续参与")
    if user_id is not None:
        member = dbutil.one(
            conn,
            "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
            (int(group["id"]), int(user_id)),
        )
        if member:
            raise ApiError(409, "GROUP_UNAVAILABLE", "你已参与这个拼团")
    member_count = int(
        dbutil.scalar(conn, "SELECT COUNT(*) FROM group_members WHERE group_id=?", (int(group["id"]),)) or 0
    )
    if member_count >= int(group["required_size"]):
        raise ApiError(409, "GROUP_FULL", "这个拼团已经没有可用名额")
    return group


def _group_for_warning(conn: sqlite3.Connection, code: str | None) -> str | None:
    if not code:
        return None
    group = dbutil.one(conn, "SELECT * FROM groups WHERE code=?", (code,))
    if group is None:
        return "关联的拼团已不存在，请更换结算方式"
    paid = int(
        dbutil.scalar(
            conn,
            "SELECT COUNT(*) FROM group_members WHERE group_id=? AND paid_at IS NOT NULL",
            (int(group["id"]),),
        )
        or 0
    )
    if group["status"] != "FORMING" or _is_past(group["expires_at"]):
        return "关联的拼团已结束，请更换结算方式"
    members = int(dbutil.scalar(conn, "SELECT COUNT(*) FROM group_members WHERE group_id=?", (int(group["id"]),)) or 0)
    if members >= int(group["required_size"]) or paid >= int(group["required_size"]):
        return "关联的拼团已满员，请更换结算方式"
    return None


def _offered_shop(shop_id: int, slug: str, name: str, kind: str, verified: int) -> dict[str, Any]:
    return {
        "id": int(shop_id),
        "slug": slug,
        "name": name,
        "kind": kind,
        "verified": bool(verified),
    }


def _cart_item_payload(row: sqlite3.Row, warnings: list[str]) -> dict[str, Any]:
    unit, _market = _line_unit_prices(row)
    quantity = int(row["quantity"])
    group = None
    if row["group_size"] is not None and row["group_price_cents"] is not None:
        group = {
            "size": int(row["group_size"]),
            "price_cents": int(row["group_price_cents"]),
            "stock": int(row["stock"]),
        }
    quantity_limit = int(row["stock"]) if row["buy_mode"] == "standard" else (1 if int(row["stock"]) > 0 else 0)
    return {
        "id": int(row["item_id"]),
        "offer_id": int(row["offer_id"]),
        "sku_id": int(row["sku_id"]),
        "quantity": quantity,
        "selected": bool(row["selected"]),
        "buy_mode": row["buy_mode"],
        "group_code": row["group_code"],
        "quantity_limit": max(0, quantity_limit),
        "line_price_cents": unit * quantity,
        "product": {
            "id": int(row["product_id"]),
            "slug": row["product_slug"],
            "title": row["product_title"],
            "brand": row["product_brand"],
            "cover": row["product_cover"],
            "cover_alt": row["product_cover_alt"],
        },
        "sku": {
            "id": int(row["sku_id"]),
            "name": row["sku_name"],
            "options": dbutil.json_loads(row["sku_options_json"], {}),
        },
        "offer": {
            "id": int(row["offer_id"]),
            "kind": row["offer_kind"],
            "kind_label": models.KIND_LABELS.get(row["offer_kind"], row["offer_kind"]),
            "name": row["offer_name"],
            "unit_price_cents": unit,
            "shipping_fee_cents": int(row["shipping_fee_cents"]),
            "delivery_label": f"预计{int(row['delivery_eta_days'])}天送达",
            "stock": int(row["stock"]),
            "group": group,
            "shop": _offered_shop(
                row["shop_id"], row["shop_slug"], row["shop_name"], row["shop_kind"], row["shop_verified"]
            ),
        },
        "warnings": warnings,
    }


def _cart_response(conn: sqlite3.Connection, owner_key: str) -> dict[str, Any]:
    cart_id = _cart_id(conn, owner_key)
    lines = _cart_lines(conn, cart_id)
    items: list[dict[str, Any]] = []
    shops: dict[int, dict[str, Any]] = {}
    item_count = 0
    selected_count = 0
    subtotal = 0
    market_subtotal = 0
    selected_has_group = False
    for row in lines:
        group_warning = _group_for_warning(conn, row["group_code"]) if row["buy_mode"] == "group" else None
        warnings: list[str] = []
        if row["offer_status"] != "active" or row["product_status"] != "active":
            warnings.append("商品已下架，请从购物车移除")
        if int(row["stock"]) < int(row["quantity"]):
            warnings.append("库存不足，请调整数量后结算")
        if group_warning:
            warnings.append(group_warning)
        items.append(_cart_item_payload(row, warnings))
        quantity = int(row["quantity"])
        item_count += quantity
        shop_id = int(row["shop_id"])
        entry = shops.get(shop_id)
        if entry is None:
            ship_from = row["shop_ship_from"] or ""
            kind_label = models.KIND_LABELS.get(row["shop_kind"], row["shop_kind"])
            delivery_note = f"{kind_label} · 由{ship_from}发货" if ship_from else f"{kind_label} · 全站包邮"
            entry = {
                "shop": _offered_shop(
                    shop_id, row["shop_slug"], row["shop_name"], row["shop_kind"], row["shop_verified"]
                ),
                "item_ids": [],
                "selected_subtotal_cents": 0,
                "delivery_note": delivery_note,
            }
            shops[shop_id] = entry
        entry["item_ids"].append(int(row["item_id"]))
        if row["selected"]:
            line_total, line_market = _line_total(row)
            selected_count += quantity
            subtotal += line_total
            market_subtotal += line_market
            entry["selected_subtotal_cents"] += line_total
            selected_has_group = selected_has_group or row["buy_mode"] == "group"
    all_selected = bool(items) and all(item["selected"] for item in items)
    savings = max(0, market_subtotal - subtotal)
    return {
        "items": items,
        "shops": [shops[sid] for sid in sorted(shops)],
        "summary": {
            "item_count": item_count,
            "selected_count": selected_count,
            "all_selected": all_selected,
            "subtotal_cents": subtotal,
            "market_subtotal_cents": market_subtotal,
            "savings_cents": savings,
            "shipping_cents": 0,
            "discount_cents": 0,
            "total_cents": subtotal,
            "has_group": selected_has_group,
            "buy_mode": "group" if selected_has_group else "standard",
        },
        "notices": ["优惠券与运费在结算页计算。"],
    }


def get_cart(ctx: RequestContext) -> dict[str, Any]:
    cart_id = _cart_id(ctx.db, ctx.owner_key)
    if cart_id is None:
        return {
            "items": [],
            "shops": [],
            "summary": {
                "item_count": 0,
                "selected_count": 0,
                "all_selected": False,
                "subtotal_cents": 0,
                "market_subtotal_cents": 0,
                "savings_cents": 0,
                "shipping_cents": 0,
                "discount_cents": 0,
                "total_cents": 0,
                "has_group": False,
                "buy_mode": "standard",
            },
            "notices": ["优惠券与运费在结算页计算。"],
        }
    return _cart_response(ctx.db, ctx.owner_key)


def _existing_group_lines(lines: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [line for line in lines if line["buy_mode"] == "group"]


def add_cart_item(ctx: RequestContext, body: dict[str, Any]) -> dict[str, Any]:
    offer_id = validate_int(body.get("offer_id"), "offer_id", minimum=1, label="报价")
    sku_id = validate_int(body.get("sku_id"), "sku_id", minimum=1, label="规格")
    buy_mode = body.get("buy_mode", "standard")
    if buy_mode not in ("standard", "group"):
        raise ApiError(400, "VALIDATION_ERROR", "购买方式无效", {"buy_mode": "standard 或 group"})
    quantity = validate_int(body.get("quantity", 1), "quantity", minimum=1, maximum=CART_MAX_QUANTITY, label="数量")
    raw_group_code = body.get("group_code")
    if raw_group_code is not None and (not isinstance(raw_group_code, str) or not raw_group_code.strip()):
        if buy_mode == "group" and raw_group_code is not None:
            raise ApiError(400, "VALIDATION_ERROR", "拼团邀请码无效", {"group_code": "格式不正确"})
        raw_group_code = None
    group_code = raw_group_code.strip().upper() if isinstance(raw_group_code, str) and raw_group_code.strip() else None

    user = ctx.user
    user_id = int(user["id"]) if user is not None else None
    with dbutil.transaction(ctx.db) as conn:
        offer = dbutil.one(
            conn,
            """SELECT o.*, p.title AS product_title, p.status AS product_status,
                      s.name AS shop_name
               FROM offers o JOIN products p ON p.id=o.product_id JOIN shops s ON s.id=o.shop_id
               WHERE o.id=? AND o.status='active' AND p.status='active'""",
            (offer_id,),
        )
        if offer is None:
            raise ApiError(404, "NOT_FOUND", "商品或报价不存在")
        sku = dbutil.one(conn, "SELECT * FROM skus WHERE id=? AND product_id=?", (sku_id, int(offer["product_id"])))
        if sku is None:
            raise ApiError(404, "NOT_FOUND", "商品规格不存在")
        if buy_mode == "group":
            if quantity != 1:
                raise ApiError(400, "VALIDATION_ERROR", "拼团商品数量必须为 1", {"quantity": "拼团数量固定为 1"})
            if offer["group_size"] is None or offer["group_price_cents"] is None:
                raise ApiError(409, "GROUP_UNAVAILABLE", "该报价不支持拼团")
            if group_code:
                _validate_group_for_cart(conn, group_code, offer_id, sku_id, user_id)
        else:
            if group_code:
                raise ApiError(400, "VALIDATION_ERROR", "普通购买不能携带拼团邀请码", {"group_code": "不适用"})
            group_code = None
        stock = int(offer["stock"])
        if stock <= 0:
            raise ApiError(409, "STOCK_CHANGED", f"「{offer['product_title']}」暂时无货")
        if quantity > stock:
            raise ApiError(409, "STOCK_CHANGED", f"「{offer['product_title']}」仅剩 {stock} 件")

        cart_id = _ensure_cart(conn, ctx.owner_key)
        all_lines = _cart_lines(conn, cart_id)
        has_standard = any(line["buy_mode"] == "standard" for line in all_lines)
        existing_groups = _existing_group_lines(all_lines)
        if buy_mode == "group" and has_standard:
            raise ApiError(422, "MIXED_BUY_MODE", "购物车已有普通商品，拼团商品需单独结算")
        if buy_mode == "standard" and existing_groups:
            raise ApiError(422, "MIXED_BUY_MODE", "购物车已有拼团商品，需单独结算")
        if buy_mode == "group":
            for line in existing_groups:
                if (
                    int(line["offer_id"]) != offer_id
                    or int(line["sku_id"]) != sku_id
                    or (line["group_code"] or None) != group_code
                ):
                    raise ApiError(422, "MIXED_BUY_MODE", "拼团商品一次只能保留一个团，请先清空购物车")

        existing = dbutil.one(
            conn,
            """SELECT * FROM cart_items
               WHERE cart_id=? AND offer_id=? AND sku_id=? AND buy_mode=?
                 AND IFNULL(group_code,'')=IFNULL(?,'') LIMIT 1""",
            (cart_id, offer_id, sku_id, buy_mode, group_code),
        )
        now = _now_iso()
        if existing:
            new_quantity = quantity if buy_mode == "group" else int(existing["quantity"]) + quantity
            if new_quantity > stock:
                raise ApiError(409, "STOCK_CHANGED", f"合并后数量超过库存（仅剩 {stock} 件）")
            conn.execute(
                "UPDATE cart_items SET quantity=?,selected=1,updated_at=? WHERE id=?",
                (new_quantity, now, int(existing["id"])),
            )
        else:
            conn.execute(
                """INSERT INTO cart_items(cart_id,offer_id,sku_id,quantity,selected,buy_mode,group_code,created_at,updated_at)
                   VALUES(?,?,?,?,1,?,?,?,?)""",
                (cart_id, offer_id, sku_id, quantity, buy_mode, group_code, now, now),
            )
        conn.execute("UPDATE carts SET updated_at=? WHERE id=?", (now, cart_id))
        return _cart_response(conn, ctx.owner_key)


def update_cart_item(ctx: RequestContext, item_id: int, body: dict[str, Any]) -> dict[str, Any]:
    has_quantity = "quantity" in body
    has_selected = "selected" in body
    if not has_quantity and not has_selected:
        raise ApiError(400, "VALIDATION_ERROR", "请提供要修改的数量或选择状态")
    with dbutil.transaction(ctx.db) as conn:
        cart_id = _cart_id(conn, ctx.owner_key)
        if cart_id is None:
            raise ApiError(404, "NOT_FOUND", "购物车商品不存在")
        row = dbutil.one(
            conn,
            "SELECT * FROM cart_items WHERE id=? AND cart_id=?",
            (item_id, cart_id),
        )
        if row is None:
            raise ApiError(404, "NOT_FOUND", "购物车商品不存在")
        offer = dbutil.one(conn, "SELECT stock,status FROM offers WHERE id=?", (int(row["offer_id"]),))
        if row["buy_mode"] == "group" and has_quantity:
            quantity = validate_int(body.get("quantity"), "quantity", minimum=1, maximum=1, label="拼团数量")
        elif has_quantity:
            quantity = validate_int(body.get("quantity"), "quantity", minimum=1, maximum=CART_MAX_QUANTITY, label="数量")
        else:
            quantity = int(row["quantity"])
        selected = bool(row["selected"])
        if has_selected:
            if not isinstance(body.get("selected"), bool):
                raise ApiError(400, "VALIDATION_ERROR", "选择状态必须是布尔值", {"selected": "必须是布尔值"})
            selected = bool(body["selected"])
        if has_quantity:
            if offer is None or offer["status"] != "active" or int(offer["stock"]) < int(quantity):
                raise ApiError(409, "STOCK_CHANGED", "库存不足，无法调整数量")
        now = _now_iso()
        conn.execute(
            "UPDATE cart_items SET quantity=?,selected=?,updated_at=? WHERE id=?",
            (quantity, 1 if selected else 0, now, item_id),
        )
        conn.execute("UPDATE carts SET updated_at=? WHERE id=?", (now, cart_id))
        return _cart_response(conn, ctx.owner_key)


def delete_cart_item(ctx: RequestContext, item_id: int) -> dict[str, Any]:
    with dbutil.transaction(ctx.db) as conn:
        cart_id = _cart_id(conn, ctx.owner_key)
        if cart_id is None:
            raise ApiError(404, "NOT_FOUND", "购物车商品不存在")
        cur = conn.execute("DELETE FROM cart_items WHERE id=? AND cart_id=?", (item_id, cart_id))
        if cur.rowcount != 1:
            raise ApiError(404, "NOT_FOUND", "购物车商品不存在")
        conn.execute("UPDATE carts SET updated_at=? WHERE id=?", (_now_iso(), cart_id))
        return _cart_response(conn, ctx.owner_key)


def select_cart_items(ctx: RequestContext, body: dict[str, Any]) -> dict[str, Any]:
    selected = body.get("selected")
    if not isinstance(selected, bool):
        raise ApiError(400, "VALIDATION_ERROR", "selected 必须是布尔值", {"selected": "必须是布尔值"})
    with dbutil.transaction(ctx.db) as conn:
        cart_id = _ensure_cart(conn, ctx.owner_key)
        now = _now_iso()
        conn.execute(
            "UPDATE cart_items SET selected=?,updated_at=? WHERE cart_id=?",
            (1 if selected else 0, now, cart_id),
        )
        conn.execute("UPDATE carts SET updated_at=? WHERE id=?", (now, cart_id))
        return _cart_response(conn, ctx.owner_key)


# ---------------------------------------------------------------------------
# coupons
# ---------------------------------------------------------------------------

def _effective_user_coupon_status(row: sqlite3.Row) -> str:
    status = str(row["uc_status"])
    if status == "AVAILABLE" and _is_past(row["uc_expires_at"]):
        return "EXPIRED"
    return status


def _coupon_base(coupon: sqlite3.Row, *, claimed: bool, claimable: bool, expires_at: str | None, remaining: int | None) -> dict[str, Any]:
    return {
        "code": coupon["code"],
        "title": coupon["title"],
        "description": coupon["description"],
        "threshold_cents": int(coupon["threshold_cents"]),
        "discount_cents": int(coupon["discount_cents"]),
        "scope": coupon["scope"],
        "expires_at": expires_at if claimed else None,
        "claimed": claimed,
        "claimable": claimable,
        "remaining": remaining,
    }


def _user_coupon_payload(row: sqlite3.Row) -> dict[str, Any]:
    claimed = True
    payload = _coupon_base(
        row,
        claimed=claimed,
        claimable=False,
        expires_at=row["uc_expires_at"],
        remaining=None,
    )
    payload.update(
        {
            "user_coupon_id": int(row["user_coupon_id"]),
            "status": _effective_user_coupon_status(row),
            "used_at": row["uc_used_at"],
        }
    )
    return payload


def _user_coupon_join(conn: sqlite3.Connection, user_id: int, code: str | None = None) -> list[sqlite3.Row]:
    sql = """SELECT uc.id AS user_coupon_id, uc.status AS uc_status, uc.claimed_at AS uc_claimed_at,
                    uc.expires_at AS uc_expires_at, uc.used_at AS uc_used_at, uc.order_id AS uc_order_id,
                    c.code, c.title, c.description, c.threshold_cents, c.discount_cents, c.scope,
                    c.valid_days, c.auto_claim_new_user, c.quantity, c.claimed_count, c.created_at AS coupon_created_at
             FROM user_coupons uc JOIN coupons c ON c.code=uc.coupon_code
             WHERE uc.user_id=?"""
    params: list[Any] = [int(user_id)]
    if code is not None:
        sql += " AND uc.coupon_code=?"
        params.append(code)
    sql += " ORDER BY uc.id DESC"
    return dbutil.all_rows(conn, sql, tuple(params))


def list_coupons(ctx: RequestContext) -> dict[str, Any]:
    user = ctx.require_user()
    user_id = int(user["id"])
    mine_rows = _user_coupon_join(ctx.db, user_id)
    mine_by_code = {row["code"]: row for row in mine_rows}
    available: list[dict[str, Any]] = []
    coupons = dbutil.all_rows(ctx.db, "SELECT * FROM coupons ORDER BY created_at,code")
    for coupon in coupons:
        mine = mine_by_code.get(coupon["code"])
        claimed = mine is not None
        total = int(coupon["quantity"])
        used_count = int(coupon["claimed_count"])
        capacity_left = max(0, total - used_count) if total > 0 else None
        claimable = (not claimed) and (capacity_left is None or capacity_left > 0)
        available.append(
            _coupon_base(
                coupon,
                claimed=claimed,
                claimable=claimable,
                expires_at=mine["uc_expires_at"] if mine is not None else None,
                remaining=None if claimed else capacity_left,
            )
        )
    mine = [_user_coupon_payload(row) for row in mine_rows if _effective_user_coupon_status(row) in {"AVAILABLE", "USED"}]
    return {"available": available, "mine": mine}


def claim_coupon(ctx: RequestContext, code: str) -> dict[str, Any]:
    user = ctx.require_user()
    user_id = int(user["id"])
    normalised = str(code or "").strip().upper()
    if not COUPON_CODE_RE.fullmatch(normalised):
        raise ApiError(404, "NOT_FOUND", "优惠券不存在")
    with dbutil.transaction(ctx.db) as conn:
        coupon = dbutil.one(conn, "SELECT * FROM coupons WHERE code=?", (normalised,))
        if coupon is None:
            raise ApiError(404, "NOT_FOUND", "优惠券不存在")
        existing = dbutil.one(
            conn,
            "SELECT 1 FROM user_coupons WHERE user_id=? AND coupon_code=?",
            (user_id, normalised),
        )
        if existing:
            raise ApiError(409, "COUPON_ALREADY_CLAIMED", "这张优惠券已经领取过了")
        total = int(coupon["quantity"])
        if total > 0 and int(coupon["claimed_count"]) >= total:
            raise ApiError(409, "COUPON_SOLD_OUT", "这张优惠券已经领完了")
        now = _now_iso()
        expires_at = dbutil.iso(dbutil.utc_now() + timedelta(days=int(coupon["valid_days"])))
        try:
            cur = conn.execute(
                "INSERT INTO user_coupons(user_id,coupon_code,status,claimed_at,expires_at) VALUES(?,?,'AVAILABLE',?,?)",
                (user_id, normalised, now, expires_at),
            )
            user_coupon_id = int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "COUPON_ALREADY_CLAIMED", "这张优惠券已经领取过了") from exc
        if total > 0:
            updated = conn.execute(
                "UPDATE coupons SET claimed_count=claimed_count+1 WHERE code=? AND claimed_count<?",
                (normalised, total),
            )
            if updated.rowcount != 1:
                raise ApiError(409, "COUPON_SOLD_OUT", "这张优惠券已经领完了")
        else:
            conn.execute("UPDATE coupons SET claimed_count=claimed_count+1 WHERE code=?", (normalised,))
        row = dbutil.one(
            conn,
            """SELECT uc.id AS user_coupon_id, uc.status AS uc_status, uc.claimed_at AS uc_claimed_at,
                      uc.expires_at AS uc_expires_at, uc.used_at AS uc_used_at, uc.order_id AS uc_order_id,
                      c.code, c.title, c.description, c.threshold_cents, c.discount_cents, c.scope,
                      c.valid_days, c.auto_claim_new_user, c.quantity, c.claimed_count, c.created_at AS coupon_created_at
               FROM user_coupons uc JOIN coupons c ON c.code=uc.coupon_code
               WHERE uc.id=?""",
            (user_coupon_id,),
        )
        assert row is not None
        return {"coupon": _user_coupon_payload(row)}


def _coupon_quote(
    conn: sqlite3.Connection,
    user_id: int,
    code: str | None,
    lines: list[sqlite3.Row],
    subtotal_cents: int,
) -> tuple[dict[str, Any] | None, sqlite3.Row | None, str | None]:
    if not code:
        return None, None, None
    rows = _user_coupon_join(conn, user_id, code)
    if not rows:
        return None, None, "这张优惠券不在你的账户中"
    row = rows[0]
    status = _effective_user_coupon_status(row)
    if status == "USED":
        return None, None, "这张优惠券已使用"
    if status == "EXPIRED":
        return None, None, "这张优惠券已过期"
    if status != "AVAILABLE":
        return None, None, "这张优惠券当前不可用"
    scope = str(row["scope"])
    if scope != "all":
        if not scope.startswith("category:"):
            return None, None, "这张优惠券的使用范围无效"
        category = scope.split(":", 1)[1]
        if not any(line["category_slug"] == category for line in lines):
            return None, None, "这张优惠券仅限指定分类商品使用"
    threshold = int(row["threshold_cents"])
    if subtotal_cents < threshold:
        shortfall = threshold - subtotal_cents
        return None, None, f"还差 ¥{shortfall / 100:.2f} 才能使用这张优惠券"
    discount = min(int(row["discount_cents"]), max(0, subtotal_cents))
    return (
        {
            "code": row["code"],
            "title": row["title"],
            "discount_cents": discount,
        },
        row,
        None,
    )


# ---------------------------------------------------------------------------
# pricing / quote
# ---------------------------------------------------------------------------

def _subtotal_for_lines(lines: list[sqlite3.Row]) -> tuple[int, int]:
    subtotal = 0
    market = 0
    for line in lines:
        line_total, line_market = _line_total(line)
        subtotal += line_total
        market += line_market
    return subtotal, market


def _eta_detail(days: int) -> str:
    if days <= 1:
        return "预计明天送达"
    if days == 2:
        return "预计后天送达"
    return f"预计{days}天送达"


def _shipping_options(lines: list[sqlite3.Row]) -> list[dict[str, Any]]:
    eta = max((int(line["delivery_eta_days"]) for line in lines), default=2)
    return [
        {
            "code": "standard",
            "label": "标准快递",
            "fee_cents": 0,
            "detail": f"全站包邮 · {_eta_detail(eta)}",
        },
        {
            "code": "express",
            "label": "顺丰加急",
            "fee_cents": EXPRESS_FEE_CENTS,
            "detail": "预计明天送达 · 加急",
        },
    ]


def checkout_quote(ctx: RequestContext, body: dict[str, Any]) -> dict[str, Any]:
    user = ctx.require_user()
    # Quoting is a recovery-friendly read: a mistyped or unknown coupon must not
    # fail the whole checkout with a 4xx. Return the undiscounted quote plus a
    # human-readable coupon_error; order creation remains fail-closed.
    coupon_input_error: str | None = None
    try:
        code = _normalise_coupon_code(body.get("coupon_code"))
    except ApiError as exc:
        code = None
        coupon_input_error = exc.message
    shipping = _shipping_method(body.get("shipping_method"))
    conn = ctx.db
    cart_id = _cart_id(conn, ctx.owner_key)
    lines = _cart_lines(conn, cart_id, selected_only=True)
    _validate_selected_lines(conn, lines)
    group_lines = [line for line in lines if line["buy_mode"] == "group"]
    has_group = bool(group_lines)
    if has_group:
        shipping = "standard"
    subtotal, market_subtotal = _subtotal_for_lines(lines)
    if coupon_input_error is not None:
        coupon, _coupon_row, coupon_error = None, None, coupon_input_error
    else:
        coupon, _coupon_row, coupon_error = _coupon_quote(conn, int(user["id"]), code, lines, subtotal)
    discount = int(coupon["discount_cents"]) if coupon else 0
    shipping_cents = 0 if shipping == "standard" else EXPRESS_FEE_CENTS
    total = max(0, subtotal - discount + shipping_cents)
    options = _shipping_options(lines)
    chosen = next(option for option in options if option["code"] == shipping)
    selected_count = sum(int(line["quantity"]) for line in lines)
    return {
        "subtotal_cents": subtotal,
        "market_subtotal_cents": market_subtotal,
        "savings_cents": max(0, market_subtotal - subtotal),
        "shipping_cents": shipping_cents,
        "discount_cents": discount,
        "total_cents": total,
        "selected_count": selected_count,
        "shipping_method": chosen,
        "shipping_options": options,
        "coupon": coupon,
        "coupon_error": coupon_error,
        "has_group": has_group,
        "address_required": True,
    }


# ---------------------------------------------------------------------------
# order request parsing, stock/coupon side effects
# ---------------------------------------------------------------------------

def _parse_create_order_body(body: dict[str, Any]) -> dict[str, Any]:
    address_id = validate_int(body.get("address_id"), "address_id", minimum=1, label="收货地址")
    coupon_code = _normalise_coupon_code(body.get("coupon_code"))
    shipping = _shipping_method(body.get("shipping_method"))
    payment_method = body.get("payment_method")
    if payment_method != "demo":
        raise ApiError(400, "VALIDATION_ERROR", "仅支持模拟支付（demo）", {"payment_method": "仅支持 demo"})
    raw_note = body.get("note", "")
    if raw_note is None:
        raw_note = ""
    if not isinstance(raw_note, str):
        raise ApiError(400, "VALIDATION_ERROR", "订单备注格式不正确", {"note": "必须是文字"})
    note = raw_note.strip()
    if len(note) > 200:
        raise ApiError(400, "VALIDATION_ERROR", "订单备注最多 200 个字符", {"note": "最多 200 个字符"})
    idempotency_key = body.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not IDEMPOTENCY_RE.fullmatch(idempotency_key):
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "幂等键需为 16-64 位可打印非空白字符",
            {"idempotency_key": "格式不正确"},
        )
    return {
        "address_id": address_id,
        "coupon_code": coupon_code,
        "shipping_method": shipping,
        "payment_method": "demo",
        "note": note,
        "idempotency_key": idempotency_key,
    }


def _address_snapshot(conn: sqlite3.Connection, user_id: int, address_id: int) -> dict[str, Any]:
    row = dbutil.one(conn, "SELECT * FROM addresses WHERE id=? AND user_id=?", (address_id, int(user_id)))
    if row is None:
        raise ApiError(400, "VALIDATION_ERROR", "请选择有效的收货地址", {"address_id": "地址不存在"})
    return {
        "id": int(row["id"]),
        "label": row["label"],
        "receiver": row["receiver"],
        "phone": row["phone"],
        "province": row["province"],
        "city": row["city"],
        "district": row["district"],
        "detail": row["detail"],
    }


def _quantity_by_offer(lines: list[sqlite3.Row]) -> dict[int, int]:
    totals: dict[int, int] = {}
    for line in lines:
        offer_id = int(line["offer_id"])
        totals[offer_id] = totals.get(offer_id, 0) + int(line["quantity"])
    return totals


def _decrement_stock(conn: sqlite3.Connection, lines: list[sqlite3.Row]) -> None:
    now = _now_iso()
    for offer_id, quantity in _quantity_by_offer(lines).items():
        cur = conn.execute(
            "UPDATE offers SET stock=stock-?, updated_at=? WHERE id=? AND stock>=? AND status='active'",
            (quantity, now, offer_id, quantity),
        )
        if cur.rowcount != 1:
            product_title = next(
                (line["product_title"] for line in lines if int(line["offer_id"]) == offer_id),
                "商品",
            )
            raise ApiError(409, "STOCK_CHANGED", f"「{product_title}」库存不足，订单未创建")


def _restore_coupon_for_order(conn: sqlite3.Connection, order: sqlite3.Row) -> None:
    coupon_id = order["user_coupon_id"]
    if coupon_id is None:
        return
    user_coupon = dbutil.one(conn, "SELECT * FROM user_coupons WHERE id=?", (int(coupon_id),))
    if user_coupon is None:
        return
    if user_coupon["status"] != "USED" or user_coupon["order_id"] != int(order["id"]):
        return
    restored_status = "AVAILABLE" if not _is_past(user_coupon["expires_at"]) else "EXPIRED"
    conn.execute(
        "UPDATE user_coupons SET status=?,used_at=NULL,order_id=NULL WHERE id=?",
        (restored_status, int(coupon_id)),
    )


def restore_order_resources(conn: sqlite3.Connection, order_id: int) -> None:
    """Restore stock and coupon for a cancelled/approved order.

    Caller must be inside a write transaction and ensure this transition only
    happens once. Used by cancel and shared with seller approval integration.
    """
    order = dbutil.one(conn, "SELECT * FROM orders WHERE id=?", (int(order_id),))
    if order is None:
        return
    totals = dbutil.all_rows(
        conn,
        "SELECT offer_id, SUM(quantity) AS quantity FROM order_items WHERE order_id=? GROUP BY offer_id",
        (int(order_id),),
    )
    now = _now_iso()
    for row in totals:
        conn.execute(
            "UPDATE offers SET stock=stock+?, updated_at=? WHERE id=?",
            (int(row["quantity"]), now, int(row["offer_id"])),
        )
    _restore_coupon_for_order(conn, order)


def _same_idempotent_request(order: sqlite3.Row, fields: dict[str, Any], effective_shipping: str) -> bool:
    try:
        address = dbutil.json_loads(order["address_json"], {})
    except Exception:
        address = {}
    return (
        int(address.get("id", -1)) == int(fields["address_id"])
        and (order["coupon_code"] or None) == fields["coupon_code"]
        and order["shipping_method"] == effective_shipping
        and (order["note"] or "") == fields["note"]
        and order["payment_method"] == "demo"
    )


def _order_for_owner(conn: sqlite3.Connection, user_id: int, order_no: str) -> sqlite3.Row:
    order = dbutil.one(conn, "SELECT * FROM orders WHERE order_no=? AND user_id=?", (order_no, int(user_id)))
    if order is None:
        raise ApiError(404, "NOT_FOUND", "订单不存在")
    return order


# ---------------------------------------------------------------------------
# order creation
# ---------------------------------------------------------------------------

def create_order(ctx: RequestContext, body: dict[str, Any]) -> dict[str, Any]:
    user = ctx.require_user()
    user_id = int(user["id"])
    fields = _parse_create_order_body(body)
    with dbutil.transaction(ctx.db) as conn:
        existing = dbutil.one(
            conn,
            "SELECT * FROM orders WHERE user_id=? AND idempotency_key=?",
            (user_id, fields["idempotency_key"]),
        )
        if existing is not None:
            effective_shipping = fields["shipping_method"]
            if existing["group_code"]:
                effective_shipping = "standard"
            if not _same_idempotent_request(existing, fields, effective_shipping):
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "这个幂等键已经用于另一笔订单")
            return _order_detail(conn, existing)

        cart_id = _cart_id(conn, ctx.owner_key)
        lines = _cart_lines(conn, cart_id, selected_only=True)
        _validate_selected_lines(conn, lines)
        group_lines = [line for line in lines if line["buy_mode"] == "group"]
        has_group = bool(group_lines)
        shipping = "standard" if has_group else fields["shipping_method"]
        subtotal, market_subtotal = _subtotal_for_lines(lines)
        coupon, coupon_row, coupon_error = _coupon_quote(
            conn, user_id, fields["coupon_code"], lines, subtotal
        )
        if fields["coupon_code"] and coupon_error:
            raise ApiError(422, "COUPON_INVALID", coupon_error)
        discount = int(coupon["discount_cents"]) if coupon else 0
        shipping_cents = 0 if shipping == "standard" else EXPRESS_FEE_CENTS
        total_cents = max(0, subtotal - discount + shipping_cents)
        address = _address_snapshot(conn, user_id, fields["address_id"])
        now = _now_iso()

        group_code: str | None = None
        group_id: int | None = None
        group_was_new = False
        if has_group:
            group_line = group_lines[0]
            if int(group_line["quantity"]) != 1:
                raise ApiError(422, "MIXED_BUY_MODE", "拼团商品数量必须为 1")
            if group_line["group_code"]:
                group = _validate_group_for_cart(
                    conn,
                    str(group_line["group_code"]),
                    int(group_line["offer_id"]),
                    int(group_line["sku_id"]),
                    user_id,
                )
                group_code = str(group["code"])
                group_id = int(group["id"])
            else:
                if group_line["group_size"] is None or group_line["group_price_cents"] is None:
                    raise ApiError(409, "GROUP_UNAVAILABLE", "该报价已不支持拼团")
                group_code = _new_group_code(conn)
                group_was_new = True

        _decrement_stock(conn, lines)

        if has_group and group_was_new:
            group_line = group_lines[0]
            cur = conn.execute(
                """INSERT INTO groups(code,offer_id,sku_id,initiator_user_id,required_size,group_price_cents,
                     status,expires_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,'FORMING',?,?,?)""",
                (
                    group_code,
                    int(group_line["offer_id"]),
                    int(group_line["sku_id"]),
                    user_id,
                    int(group_line["group_size"]),
                    int(group_line["group_price_cents"]),
                    _after_hours_iso(GROUP_TTL_HOURS),
                    now,
                    now,
                ),
            )
            group_id = int(cur.lastrowid)

        order_no = _new_order_no(conn)
        cur = conn.execute(
            """INSERT INTO orders(order_no,user_id,status,subtotal_cents,discount_cents,shipping_cents,
                 total_cents,market_subtotal_cents,coupon_code,user_coupon_id,address_json,shipping_method,
                 payment_method,note,created_at,updated_at,idempotency_key,group_code)
               VALUES(?,?,'PENDING_PAYMENT',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order_no,
                user_id,
                subtotal,
                discount,
                shipping_cents,
                total_cents,
                market_subtotal,
                coupon["code"] if coupon else None,
                int(coupon_row["user_coupon_id"]) if coupon_row is not None else None,
                dbutil.json_dumps(address),
                shipping,
                "demo",
                fields["note"],
                now,
                now,
                fields["idempotency_key"],
                group_code,
            ),
        )
        order_id = int(cur.lastrowid)

        if coupon_row is not None:
            updated = conn.execute(
                """UPDATE user_coupons SET status='USED',used_at=?,order_id=?
                   WHERE id=? AND status='AVAILABLE'""",
                (now, order_id, int(coupon_row["user_coupon_id"])),
            )
            if updated.rowcount != 1:
                raise ApiError(422, "COUPON_INVALID", "优惠券已不可用，请刷新后重试")

        if has_group:
            assert group_id is not None
            try:
                conn.execute(
                    "INSERT INTO group_members(group_id,user_id,order_id,paid_at,created_at) VALUES(?,?,?,NULL,?)",
                    (group_id, user_id, order_id, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "GROUP_UNAVAILABLE", "你已参与这个拼团") from exc

        by_shop: dict[int, list[sqlite3.Row]] = {}
        for line in lines:
            by_shop.setdefault(int(line["shop_id"]), []).append(line)
        for shop_id in sorted(by_shop):
            package_cur = conn.execute(
                """INSERT INTO order_packages(order_id,shop_id,status,shipping_method,created_at,updated_at)
                   VALUES(?,?,'PENDING',?,?,?)""",
                (order_id, shop_id, shipping, now, now),
            )
            package_id = int(package_cur.lastrowid)
            for line in by_shop[shop_id]:
                unit, _market = _line_unit_prices(line)
                conn.execute(
                    """INSERT INTO order_items(order_id,package_id,product_id,offer_id,sku_id,shop_id,title,cover,
                         sku_label,unit_price_cents,quantity,buy_mode,offer_kind,services_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        order_id,
                        package_id,
                        int(line["product_id"]),
                        int(line["offer_id"]),
                        int(line["sku_id"]),
                        int(line["shop_id"]),
                        line["product_title"],
                        line["product_cover"],
                        line["sku_name"],
                        unit,
                        int(line["quantity"]),
                        line["buy_mode"],
                        line["offer_kind"],
                        line["services_json"],
                    ),
                )
        _add_event(conn, order_id, "订单已创建", "等待买家付款", created_at=now)
        selected_ids = [int(line["item_id"]) for line in lines]
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            conn.execute(
                f"DELETE FROM cart_items WHERE cart_id=? AND id IN ({placeholders})",
                (cart_id, *selected_ids),
            )
            conn.execute("UPDATE carts SET updated_at=? WHERE id=?", (now, cart_id))
        created_order = dbutil.one(conn, "SELECT * FROM orders WHERE id=?", (order_id,))
        assert created_order is not None
        return _order_detail(conn, created_order)


# ---------------------------------------------------------------------------
# group derived state and order actions
# ---------------------------------------------------------------------------

def _group_paid_count(conn: sqlite3.Connection, group_id: int) -> int:
    return int(
        dbutil.scalar(
            conn,
            "SELECT COUNT(*) FROM group_members WHERE group_id=? AND paid_at IS NOT NULL",
            (int(group_id),),
        )
        or 0
    )


def _group_member_count(conn: sqlite3.Connection, group_id: int) -> int:
    return int(dbutil.scalar(conn, "SELECT COUNT(*) FROM group_members WHERE group_id=?", (int(group_id),)) or 0)


def _group_effective_status(group: sqlite3.Row, paid_count: int) -> str:
    if group["status"] == "FORMING":
        if _is_past(group["expires_at"]):
            return "EXPIRED"
        if paid_count >= int(group["required_size"]):
            return "FULL"
    return str(group["status"])


def _group_order_summary(conn: sqlite3.Connection, order: sqlite3.Row) -> dict[str, Any] | None:
    code = order["group_code"]
    if not code:
        return None
    group = dbutil.one(conn, "SELECT * FROM groups WHERE code=?", (code,))
    if group is None:
        return None
    paid = _group_paid_count(conn, int(group["id"]))
    return {
        "code": group["code"],
        "status": _group_effective_status(group, paid),
        "required_size": int(group["required_size"]),
        "joined_count": paid,
        "expires_at": group["expires_at"],
    }


def _group_payable(conn: sqlite3.Connection, order: sqlite3.Row) -> bool:
    if order["status"] != "PENDING_PAYMENT":
        return False
    if not order["group_code"]:
        return True
    group = dbutil.one(conn, "SELECT * FROM groups WHERE code=?", (order["group_code"],))
    if group is None or group["status"] != "FORMING" or _is_past(group["expires_at"]):
        return False
    member = dbutil.one(conn, "SELECT * FROM group_members WHERE order_id=?", (int(order["id"]),))
    if member is None or member["paid_at"] is not None:
        return False
    return _group_paid_count(conn, int(group["id"])) < int(group["required_size"])


# ---------------------------------------------------------------------------
# order serialization and actions
# ---------------------------------------------------------------------------

def _order_item_rows(conn: sqlite3.Connection, order_id: int) -> list[sqlite3.Row]:
    return dbutil.all_rows(
        conn,
        """SELECT oi.*, p.slug AS product_slug, p.status AS product_status
           FROM order_items oi JOIN products p ON p.id=oi.product_id
           WHERE oi.order_id=? ORDER BY oi.id""",
        (int(order_id),),
    )


def _package_rows(conn: sqlite3.Connection, order_id: int) -> list[sqlite3.Row]:
    return dbutil.all_rows(conn, "SELECT * FROM order_packages WHERE order_id=? ORDER BY id", (int(order_id),))


def _reviewed_item_ids(conn: sqlite3.Connection, order_id: int) -> set[int]:
    rows = dbutil.all_rows(
        conn,
        """SELECT r.order_item_id FROM reviews r JOIN order_items oi ON oi.id=r.order_item_id
           WHERE oi.order_id=? AND r.order_item_id IS NOT NULL""",
        (int(order_id),),
    )
    return {int(row["order_item_id"]) for row in rows}


def _has_requested_refund(conn: sqlite3.Connection, order_id: int) -> bool:
    return (
        dbutil.one(
            conn,
            "SELECT 1 FROM refunds WHERE order_id=? AND status='REQUESTED' LIMIT 1",
            (int(order_id),),
        )
        is not None
    )


def _order_actions(
    conn: sqlite3.Connection,
    order: sqlite3.Row,
    packages: list[sqlite3.Row],
    items: list[sqlite3.Row],
    reviewed: set[int],
) -> dict[str, bool]:
    status = str(order["status"])
    requested_refund = _has_requested_refund(conn, int(order["id"]))
    all_shipped = bool(packages) and all(pkg["status"] in {"SHIPPED", "DELIVERED"} for pkg in packages)
    item_reviewable = status == "COMPLETED" and any(int(item["id"]) not in reviewed for item in items)
    can_pay = status == "PENDING_PAYMENT" and _group_payable(conn, order)
    can_cancel = status in {"PENDING_PAYMENT", "GROUP_FORMING"} and not requested_refund
    can_confirm = status in {"PAID", "SHIPPED"} and all_shipped and not requested_refund
    can_refund = (
        status in {"PAID", "SHIPPED", "COMPLETED"}
        and len(packages) == 1
        and not requested_refund
    )
    return {
        "pay": bool(can_pay),
        "cancel": bool(can_cancel),
        "confirm": bool(can_confirm),
        "review": bool(item_reviewable),
        "refund": bool(can_refund),
        "view_group": bool(order["group_code"]),
    }


def _order_summary(
    conn: sqlite3.Connection,
    order: sqlite3.Row,
    *,
    items: list[sqlite3.Row] | None = None,
    packages: list[sqlite3.Row] | None = None,
    reviewed: set[int] | None = None,
) -> dict[str, Any]:
    item_rows = _order_item_rows(conn, int(order["id"])) if items is None else items
    package_rows = _package_rows(conn, int(order["id"])) if packages is None else packages
    reviewed_ids = _reviewed_item_ids(conn, int(order["id"])) if reviewed is None else reviewed
    preview = [
        {
            "title": item["title"],
            "cover": item["cover"],
            "sku_label": item["sku_label"],
            "quantity": int(item["quantity"]),
        }
        for item in item_rows
    ]
    return {
        "order_no": order["order_no"],
        "status": order["status"],
        "status_label": models.status_label(order["status"]),
        "created_at": order["created_at"],
        "paid_at": order["paid_at"],
        "total_cents": int(order["total_cents"]),
        "preview_items": preview,
        "item_count": sum(int(item["quantity"]) for item in item_rows),
        "shop_count": len(package_rows),
        "group": _group_order_summary(conn, order),
        "actions": _order_actions(conn, order, package_rows, item_rows, reviewed_ids),
    }


def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {"label": row["label"], "detail": row["detail"], "created_at": row["created_at"]}


def _safe_address(order: sqlite3.Row) -> dict[str, Any]:
    raw = dbutil.json_loads(order["address_json"], {})
    keys = ("id", "receiver", "phone", "province", "city", "district", "detail", "label")
    return {key: raw.get(key) for key in keys}


def _order_packages_payload(
    conn: sqlite3.Connection,
    order: sqlite3.Row,
    package_rows: list[sqlite3.Row],
    item_rows: list[sqlite3.Row],
    reviewed: set[int],
    events: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for package in package_rows:
        shop = dbutil.one(conn, "SELECT id,name,kind FROM shops WHERE id=?", (int(package["shop_id"]),))
        package_items: list[dict[str, Any]] = []
        for item in item_rows:
            if int(item["package_id"]) != int(package["id"]):
                continue
            is_reviewed = int(item["id"]) in reviewed
            package_items.append(
                {
                    "id": int(item["id"]),
                    "product_id": int(item["product_id"]),
                    "slug": item["product_slug"],
                    "title": item["title"],
                    "cover": item["cover"],
                    "sku_label": item["sku_label"],
                    "unit_price_cents": int(item["unit_price_cents"]),
                    "quantity": int(item["quantity"]),
                    "offer_kind": item["offer_kind"],
                    "services": dbutil.json_loads(item["services_json"], []),
                    "reviewable": order["status"] == "COMPLETED" and not is_reviewed,
                    "reviewed": is_reviewed,
                }
            )
        package_events = [
            event
            for event in events
            if event["package_id"] is None or int(event["package_id"]) == int(package["id"])
        ]
        packages.append(
            {
                "id": int(package["id"]),
                "shop": {"id": int(shop["id"]), "name": shop["name"], "kind": shop["kind"]} if shop else None,
                "status": package["status"],
                "status_label": models.PACKAGE_STATUS_LABELS.get(package["status"], package["status"]),
                "carrier": package["carrier"],
                "tracking_no": package["tracking_no"],
                "shipped_at": package["shipped_at"],
                "items": package_items,
                "events": [_event_payload(event) for event in package_events],
            }
        )
    return packages


def _order_detail(conn: sqlite3.Connection, order: sqlite3.Row) -> dict[str, Any]:
    items = _order_item_rows(conn, int(order["id"]))
    packages = _package_rows(conn, int(order["id"]))
    reviewed = _reviewed_item_ids(conn, int(order["id"]))
    events = dbutil.all_rows(
        conn,
        "SELECT * FROM order_events WHERE order_id=? ORDER BY created_at,id",
        (int(order["id"]),),
    )
    summary = _order_summary(conn, order, items=items, packages=packages, reviewed=reviewed)
    refund_rows = dbutil.all_rows(
        conn,
        """SELECT id,reason,kind,amount_cents,status,created_at FROM refunds
           WHERE order_id=? ORDER BY created_at,id""",
        (int(order["id"]),),
    )
    summary.update(
        {
            "subtotal_cents": int(order["subtotal_cents"]),
            "shipping_cents": int(order["shipping_cents"]),
            "discount_cents": int(order["discount_cents"]),
            "market_savings_cents": max(0, int(order["market_subtotal_cents"]) - int(order["subtotal_cents"])),
            "address": _safe_address(order),
            "shipping_method": order["shipping_method"],
            "payment_method": order["payment_method"],
            "note": order["note"],
            "paid_at": order["paid_at"],
            "shipped_at": order["shipped_at"],
            "completed_at": order["completed_at"],
            "cancelled_at": order["cancelled_at"],
            "refunds": [
                {
                    "id": int(row["id"]),
                    "reason": row["reason"],
                    "kind": row["kind"],
                    "amount_cents": int(row["amount_cents"]),
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
                for row in refund_rows
            ],
            "packages": _order_packages_payload(conn, order, packages, items, reviewed, events),
            "timeline": [_event_payload(event) for event in events],
        }
    )
    return summary


# ---------------------------------------------------------------------------
# order list/detail
# ---------------------------------------------------------------------------

def _tab_condition(tab: str) -> str:
    if tab == "unpaid":
        return "o.status='PENDING_PAYMENT'"
    if tab == "to_ship":
        return "o.status='PAID'"
    if tab == "to_receive":
        return "o.status='SHIPPED'"
    if tab == "completed":
        return "o.status='COMPLETED'"
    if tab == "after_sale":
        return "(o.status IN ('REFUNDING','REFUNDED') OR EXISTS (SELECT 1 FROM refunds r WHERE r.order_id=o.id AND r.status!='CANCELLED'))"
    return "1=1"


def list_orders(ctx: RequestContext) -> dict[str, Any]:
    user = ctx.require_user()
    user_id = int(user["id"])
    tab = ctx.q("tab", "all") or "all"
    if tab not in ORDER_TABS:
        raise ApiError(400, "VALIDATION_ERROR", "订单筛选无效", {"tab": "选项无效"})
    page, page_size = ctx.pagination()
    conn = ctx.db
    rows_all = dbutil.all_rows(
        conn,
        """SELECT o.status,
                  EXISTS(SELECT 1 FROM refunds r WHERE r.order_id=o.id AND r.status!='CANCELLED') AS has_refund
           FROM orders o WHERE o.user_id=?""",
        (user_id,),
    )
    counts = {key: 0 for key in ORDER_TABS}
    counts["all"] = len(rows_all)
    for row in rows_all:
        status = row["status"]
        has_refund = bool(row["has_refund"])
        if status == "PENDING_PAYMENT":
            counts["unpaid"] += 1
        if status == "PAID":
            counts["to_ship"] += 1
        if status == "SHIPPED":
            counts["to_receive"] += 1
        if status == "COMPLETED":
            counts["completed"] += 1
        if has_refund or status in {"REFUNDING", "REFUNDED"}:
            counts["after_sale"] += 1
    condition = _tab_condition(tab)
    total = int(
        dbutil.scalar(
            conn,
            f"SELECT COUNT(*) FROM orders o WHERE o.user_id=? AND {condition}",
            (user_id,),
        )
        or 0
    )
    pages = (total + page_size - 1) // page_size if total else 0
    offset = (page - 1) * page_size
    rows = dbutil.all_rows(
        conn,
        f"SELECT o.* FROM orders o WHERE o.user_id=? AND {condition} ORDER BY o.created_at DESC,o.id DESC LIMIT ? OFFSET ?",
        (user_id, page_size, offset),
    )
    return {
        "items": [_order_summary(conn, row) for row in rows],
        "counts": counts,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "tab": tab,
    }


def get_order(ctx: RequestContext, order_no: str) -> dict[str, Any]:
    user = ctx.require_user()
    order = _order_for_owner(ctx.db, int(user["id"]), order_no)
    return _order_detail(ctx.db, order)


# ---------------------------------------------------------------------------
# payment / cancellation / confirmation
# ---------------------------------------------------------------------------

def pay_order(ctx: RequestContext, order_no: str, body: dict[str, Any]) -> dict[str, Any]:
    user = ctx.require_user()
    payment_method = body.get("payment_method")
    if payment_method != "demo":
        raise ApiError(400, "VALIDATION_ERROR", "仅支持模拟支付（demo）", {"payment_method": "仅支持 demo"})
    user_id = int(user["id"])
    with dbutil.transaction(ctx.db) as conn:
        order = _order_for_owner(conn, user_id, order_no)
        if order["status"] != "PENDING_PAYMENT":
            raise ApiError(409, "ORDER_STATE", "当前订单状态不能支付")
        if _has_requested_refund(conn, int(order["id"])):
            raise ApiError(409, "ORDER_STATE", "售后处理中，暂时不能支付")
        now = _now_iso()
        if order["group_code"]:
            group = dbutil.one(conn, "SELECT * FROM groups WHERE code=?", (order["group_code"],))
            if group is None or group["status"] != "FORMING" or _is_past(group["expires_at"]):
                raise ApiError(409, "GROUP_UNAVAILABLE", "拼团已结束，无法支付")
            member = dbutil.one(
                conn, "SELECT * FROM group_members WHERE order_id=? AND group_id=?",
                (int(order["id"]), int(group["id"])),
            )
            if member is None:
                raise ApiError(409, "ORDER_STATE", "拼团报名记录缺失，无法支付")
            if member["paid_at"] is not None:
                raise ApiError(409, "ORDER_STATE", "该订单已支付")
            if _group_paid_count(conn, int(group["id"])) >= int(group["required_size"]):
                raise ApiError(409, "GROUP_FULL", "这个拼团已经满员")
            conn.execute("UPDATE group_members SET paid_at=? WHERE id=?", (now, int(member["id"])))
            paid_count = _group_paid_count(conn, int(group["id"]))
            group_full = paid_count >= int(group["required_size"])
            final_status = "PAID" if group_full else "GROUP_FORMING"
            conn.execute(
                "UPDATE orders SET status=?,payment_method='demo',paid_at=?,updated_at=? WHERE id=?",
                (final_status, now, now, int(order["id"])),
            )
            _add_event(
                conn,
                int(order["id"]),
                "支付成功",
                "演示支付完成，等待拼团成团" if not group_full else "演示支付完成，拼团已满员",
                created_at=now,
            )
            if group_full:
                conn.execute(
                    "UPDATE groups SET status='FULL',updated_at=? WHERE id=?",
                    (now, int(group["id"])),
                )
                member_orders = dbutil.all_rows(
                    conn,
                    """SELECT o.id FROM group_members gm JOIN orders o ON o.id=gm.order_id
                       WHERE gm.group_id=? AND gm.paid_at IS NOT NULL ORDER BY o.id""",
                    (int(group["id"]),),
                )
                for member_order in member_orders:
                    member_order_id = int(member_order["id"])
                    conn.execute(
                        """UPDATE orders SET status='PAID',updated_at=?
                           WHERE id=? AND status='GROUP_FORMING'""",
                        (now, member_order_id),
                    )
                    _add_event(
                        conn, member_order_id, "拼团成功", "拼团人数已满，订单转入待发货", created_at=now
                    )
        else:
            conn.execute(
                "UPDATE orders SET status='PAID',payment_method='demo',paid_at=?,updated_at=? WHERE id=?",
                (now, now, int(order["id"])),
            )
            _add_event(conn, int(order["id"]), "支付成功", "演示支付完成，等待商家发货", created_at=now)
        refreshed = dbutil.one(conn, "SELECT * FROM orders WHERE id=?", (int(order["id"]),))
        assert refreshed is not None
        return _order_detail(conn, refreshed)


def cancel_order(ctx: RequestContext, order_no: str, body: dict[str, Any]) -> dict[str, Any]:
    user = ctx.require_user()
    if not isinstance(body, dict):
        raise ApiError(400, "VALIDATION_ERROR", "请求体必须是 JSON 对象")
    reason = body.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str) or len(reason.strip()) > 200:
        raise ApiError(400, "VALIDATION_ERROR", "取消原因最多 200 个字符", {"reason": "最多 200 个字符"})
    reason = reason.strip()
    user_id = int(user["id"])
    with dbutil.transaction(ctx.db) as conn:
        order = _order_for_owner(conn, user_id, order_no)
        if order["status"] not in {"PENDING_PAYMENT", "GROUP_FORMING"}:
            raise ApiError(409, "ORDER_STATE", "当前订单状态不能取消")
        _require_active_refund_for_cancel(conn, int(order["id"]))
        now = _now_iso()
        was_paid = order["paid_at"] is not None
        group = None
        if order["group_code"]:
            group = dbutil.one(conn, "SELECT * FROM groups WHERE code=?", (order["group_code"],))
            if group is not None:
                if group["status"] == "FULL":
                    raise ApiError(409, "ORDER_STATE", "拼团已满，不能取消订单，请发起售后")
                member = dbutil.one(
                    conn, "SELECT * FROM group_members WHERE order_id=?", (int(order["id"]),)
                )
                if member is not None:
                    conn.execute("DELETE FROM group_members WHERE id=?", (int(member["id"]),))
                remaining = _group_member_count(conn, int(group["id"]))
                if remaining == 0 and group["status"] == "FORMING":
                    conn.execute(
                        "UPDATE groups SET status='CANCELLED',updated_at=? WHERE id=?",
                        (now, int(group["id"])),
                    )
        restore_order_resources(conn, int(order["id"]))
        conn.execute(
            """UPDATE orders SET status='CANCELLED',cancelled_at=?,refunded_at=?,updated_at=? WHERE id=?""",
            (now, now if was_paid else None, now, int(order["id"])),
        )
        conn.execute(
            "UPDATE order_packages SET status='CANCELLED',updated_at=? WHERE order_id=? AND status!='SHIPPED'",
            (now, int(order["id"])),
        )
        detail = "拼团未成团，演示退款完成，库存与优惠券已退回" if was_paid else "未付款订单已取消，库存与优惠券已释放"
        if reason:
            detail += f"；原因：{reason}"
        _add_event(conn, int(order["id"]), "订单已取消", detail, created_at=now)
        refreshed = dbutil.one(conn, "SELECT * FROM orders WHERE id=?", (int(order["id"]),))
        assert refreshed is not None
        return _order_detail(conn, refreshed)


def confirm_order(ctx: RequestContext, order_no: str) -> dict[str, Any]:
    user = ctx.require_user()
    user_id = int(user["id"])
    with dbutil.transaction(ctx.db) as conn:
        order = _order_for_owner(conn, user_id, order_no)
        if order["status"] not in {"PAID", "SHIPPED"}:
            raise ApiError(409, "ORDER_STATE", "当前订单状态不能确认收货")
        _require_active_refund_for_cancel(conn, int(order["id"]))
        packages = _package_rows(conn, int(order["id"]))
        if not packages or any(pkg["status"] not in {"SHIPPED", "DELIVERED"} for pkg in packages):
            raise ApiError(409, "ORDER_STATE", "还有包裹未发货，暂时不能确认收货")
        now = _now_iso()
        shipped_times = [pkg["shipped_at"] for pkg in packages if pkg["shipped_at"]]
        shipped_at = order["shipped_at"] or (max(shipped_times) if shipped_times else now)
        conn.execute(
            """UPDATE order_packages SET status='DELIVERED',delivered_at=COALESCE(delivered_at,?),updated_at=?
               WHERE order_id=? AND status!='CANCELLED'""",
            (now, now, int(order["id"])),
        )
        conn.execute(
            """UPDATE orders SET status='COMPLETED',shipped_at=?,completed_at=?,updated_at=? WHERE id=?""",
            (shipped_at, now, now, int(order["id"])),
        )
        _add_event(conn, int(order["id"]), "交易完成", "买家已确认收货，评价窗口已开启", created_at=now)
        refreshed = dbutil.one(conn, "SELECT * FROM orders WHERE id=?", (int(order["id"]),))
        assert refreshed is not None
        return _order_detail(conn, refreshed)


# ---------------------------------------------------------------------------
# after-sales request
# ---------------------------------------------------------------------------

def request_refund(ctx: RequestContext, order_no: str, body: dict[str, Any]) -> dict[str, Any]:
    user = ctx.require_user()
    if not isinstance(body, dict):
        raise ApiError(400, "VALIDATION_ERROR", "请求体必须是 JSON 对象")
    reason_raw = body.get("reason")
    if not isinstance(reason_raw, str) or not (1 <= len(reason_raw.strip()) <= 300):
        raise ApiError(400, "VALIDATION_ERROR", "请填写售后原因（1-300 字）", {"reason": "1-300 字"})
    reason = reason_raw.strip()
    kind = body.get("kind")
    if kind not in {"refund_only", "return_refund"}:
        raise ApiError(400, "VALIDATION_ERROR", "售后类型无效", {"kind": "refund_only 或 return_refund"})
    detail_raw = body.get("detail", "")
    if detail_raw is None:
        detail_raw = ""
    if not isinstance(detail_raw, str) or len(detail_raw.strip()) > 1000:
        raise ApiError(400, "VALIDATION_ERROR", "售后说明最多 1000 个字符", {"detail": "最多 1000 个字符"})
    detail = detail_raw.strip()
    user_id = int(user["id"])
    with dbutil.transaction(ctx.db) as conn:
        order = _order_for_owner(conn, user_id, order_no)
        if order["status"] not in {"PAID", "SHIPPED", "COMPLETED"}:
            raise ApiError(409, "ORDER_STATE", "当前订单状态不能发起售后")
        if _has_requested_refund(conn, int(order["id"])):
            raise ApiError(409, "ORDER_STATE", "已有处理中的售后申请")
        packages = dbutil.all_rows(
            conn, "SELECT * FROM order_packages WHERE order_id=? ORDER BY id", (int(order["id"]),)
        )
        if len(packages) != 1:
            raise ApiError(422, "AFTER_SALES_UNSUPPORTED", "MVP 仅支持单店铺整单售后")
        package = packages[0]
        now = _now_iso()
        previous_status = str(order["status"])
        conn.execute(
            """INSERT INTO refunds(order_id,order_no,user_id,shop_id,kind,reason,detail,amount_cents,
                 status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,'REQUESTED',?,?)""",
            (
                int(order["id"]),
                order["order_no"],
                user_id,
                int(package["shop_id"]),
                kind,
                reason,
                detail,
                int(order["total_cents"]),
                now,
                now,
            ),
        )
        conn.execute(
            """UPDATE orders SET status='REFUNDING',last_state_before_refund=?,updated_at=? WHERE id=?""",
            (previous_status, now, int(order["id"])),
        )
        _add_event(conn, int(order["id"]), "售后申请已提交", f"{kind} · {reason}", created_at=now)
        refreshed = dbutil.one(conn, "SELECT * FROM orders WHERE id=?", (int(order["id"]),))
        assert refreshed is not None
        return _order_detail(conn, refreshed)


# ---------------------------------------------------------------------------
# reviews
# ---------------------------------------------------------------------------

def _validate_review_entries(body: dict[str, Any]) -> list[dict[str, Any]]:
    raw_reviews = body.get("reviews")
    if not isinstance(raw_reviews, list) or not (1 <= len(raw_reviews) <= 20):
        raise ApiError(400, "VALIDATION_ERROR", "每次需提交 1-20 条评价", {"reviews": "1-20 条"})
    parsed: list[dict[str, Any]] = []
    seen_item_ids: set[int] = set()
    for raw in raw_reviews:
        if not isinstance(raw, dict):
            raise ApiError(400, "VALIDATION_ERROR", "评价条目格式不正确", {"reviews": "每条必须是对象"})
        item_id = validate_int(raw.get("item_id"), "item_id", minimum=1, label="订单商品")
        if item_id in seen_item_ids:
            raise ApiError(409, "DUPLICATE_REVIEW", "同一次提交不能重复评价同一商品")
        seen_item_ids.add(item_id)
        rating = validate_int(raw.get("rating"), "rating", minimum=1, maximum=5, label="评分")
        raw_title = raw.get("title", "")
        if raw_title is None:
            raw_title = ""
        title = validate_text(raw_title, "title", min_len=0, max_len=40, label="标题")
        body_text = validate_text(raw.get("body"), "body", min_len=10, max_len=600, label="评价内容")
        raw_tags = raw.get("tags", [])
        if raw_tags is None:
            raw_tags = []
        if not isinstance(raw_tags, list) or len(raw_tags) > 8:
            raise ApiError(400, "VALIDATION_ERROR", "标签最多 8 个", {"tags": "需为字符串数组"})
        tags: list[str] = []
        for tag in raw_tags:
            label = validate_text(tag, "tags", min_len=1, max_len=20, label="标签")
            if label not in tags:
                tags.append(label)
        parsed.append(
            {
                "item_id": item_id,
                "rating": rating,
                "title": title,
                "body": body_text,
                "tags": tags,
            }
        )
    return parsed


def submit_reviews(ctx: RequestContext, order_no: str, body: dict[str, Any]) -> dict[str, Any]:
    user = ctx.require_user()
    user_id = int(user["id"])
    entries = _validate_review_entries(body)
    with dbutil.transaction(ctx.db) as conn:
        order = _order_for_owner(conn, user_id, order_no)
        if order["status"] != "COMPLETED":
            raise ApiError(409, "ORDER_STATE", "只有已完成订单可以评价")
        order_items = dbutil.all_rows(conn, "SELECT * FROM order_items WHERE order_id=?", (int(order["id"]),))
        by_id = {int(item["id"]): item for item in order_items}
        for entry in entries:
            if entry["item_id"] not in by_id:
                raise ApiError(400, "VALIDATION_ERROR", "评价商品不属于此订单", {"item_id": "不是本订单商品"})
        existing = dbutil.all_rows(
            conn,
            """SELECT r.order_item_id FROM reviews r JOIN order_items oi ON oi.id=r.order_item_id
               WHERE oi.order_id=? AND r.order_item_id IS NOT NULL""",
            (int(order["id"]),),
        )
        existing_ids = {int(row["order_item_id"]) for row in existing}
        for entry in entries:
            if entry["item_id"] in existing_ids:
                raise ApiError(409, "DUPLICATE_REVIEW", "这件商品已经评价过了")
        now = _now_iso()
        review_ids: list[int] = []
        product_ids: set[int] = set()
        author = models.mask_name(user["display_name"])
        try:
            for entry in entries:
                item = by_id[entry["item_id"]]
                cur = conn.execute(
                    """INSERT INTO reviews(product_id,user_id,order_item_id,author,rating,title,body,tags_json,
                         verified,helpful_count,sku_label,created_at)
                       VALUES(?,?,?,?,?,?,?,?,1,0,?,?)""",
                    (
                        int(item["product_id"]),
                        user_id,
                        int(item["id"]),
                        author,
                        entry["rating"],
                        entry["title"],
                        entry["body"],
                        dbutil.json_dumps(entry["tags"]),
                        item["sku_label"],
                        now,
                    ),
                )
                review_ids.append(int(cur.lastrowid))
                product_ids.add(int(item["product_id"]))
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "DUPLICATE_REVIEW", "这件商品已经评价过了") from exc
        for product_id in product_ids:
            aggregate = dbutil.one(
                conn,
                "SELECT COUNT(*) AS count, COALESCE(AVG(rating),0) AS avg_rating FROM reviews WHERE product_id=?",
                (product_id,),
            )
            assert aggregate is not None
            conn.execute(
                "UPDATE products SET rating=?,review_count=? WHERE id=?",
                (round(float(aggregate["avg_rating"]), 2), int(aggregate["count"]), product_id),
            )
        review_rows: list[sqlite3.Row] = []
        for review_id in review_ids:
            row = dbutil.one(conn, "SELECT * FROM reviews WHERE id=?", (review_id,))
            if row is not None:
                review_rows.append(row)
        return {"reviews": [models.review_payload(row, viewer_id=user_id) for row in review_rows]}


# ---------------------------------------------------------------------------
# public group detail
# ---------------------------------------------------------------------------

def group_view(ctx: RequestContext, code: str) -> dict[str, Any]:
    normalised = str(code or "").strip().upper()
    if not GROUP_CODE_RE.fullmatch(normalised):
        raise ApiError(404, "NOT_FOUND", "拼团不存在")
    row = dbutil.one(
        ctx.db,
        """SELECT g.*, o.name AS offer_name, o.kind AS offer_kind,
                  s.id AS shop_id, s.name AS shop_name,
                  p.slug AS product_slug, p.title AS product_title, p.cover AS product_cover, p.brand AS product_brand,
                  k.name AS sku_name
           FROM groups g
           JOIN offers o ON o.id=g.offer_id
           JOIN shops s ON s.id=o.shop_id
           JOIN products p ON p.id=o.product_id
           JOIN skus k ON k.id=g.sku_id
           WHERE g.code=?""",
        (normalised,),
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "拼团不存在")
    viewer = ctx.user
    viewer_id = int(viewer["id"]) if viewer is not None else None
    paid_members = dbutil.all_rows(
        ctx.db,
        """SELECT gm.user_id,gm.paid_at,gm.created_at,u.display_name
           FROM group_members gm JOIN users u ON u.id=gm.user_id
           WHERE gm.group_id=? AND gm.paid_at IS NOT NULL
           ORDER BY gm.paid_at,gm.id""",
        (int(row["id"]),),
    )
    member_count = _group_member_count(ctx.db, int(row["id"]))
    paid_count = len(paid_members)
    status = _group_effective_status(row, paid_count)
    joined_by_me = False
    if viewer_id is not None:
        joined_by_me = (
            dbutil.one(
                ctx.db,
                "SELECT 1 FROM group_members WHERE group_id=? AND user_id=? LIMIT 1",
                (int(row["id"]), viewer_id),
            )
            is not None
        )
    can_join = False
    reason: str | None = None
    if status == "FULL":
        reason = "拼团已满员"
    elif status == "EXPIRED":
        reason = "拼团已过期"
    elif status == "CANCELLED":
        reason = "拼团已取消"
    elif status != "FORMING":
        reason = "拼团当前不可参与"
    elif joined_by_me:
        reason = "你已参与该拼团"
    elif member_count >= int(row["required_size"]):
        reason = "名额已被待付款订单暂占，请稍后再试"
    else:
        can_join = True
    members = [
        {
            "display_name": member["display_name"],
            "joined_at": member["paid_at"] or member["created_at"],
            "is_initiator": int(member["user_id"]) == int(row["initiator_user_id"]),
        }
        for member in paid_members
    ]
    return {
        "code": row["code"],
        "status": status,
        "required_size": int(row["required_size"]),
        "joined_count": paid_count,
        "expires_at": row["expires_at"],
        "group_price_cents": int(row["group_price_cents"]),
        "joined_by_me": joined_by_me,
        "product": {
            "slug": row["product_slug"],
            "title": row["product_title"],
            "cover": row["product_cover"],
            "brand": row["product_brand"],
        },
        "sku": {"id": int(row["sku_id"]), "name": row["sku_name"]},
        "offer": {
            "id": int(row["offer_id"]),
            "name": row["offer_name"],
            "kind": row["offer_kind"],
            "shop": {"id": int(row["shop_id"]), "name": row["shop_name"]},
        },
        "members": members,
        "actions": {
            "can_join": can_join,
            "needs_login": viewer_id is None,
            "reason": None if can_join else reason,
        },
    }
