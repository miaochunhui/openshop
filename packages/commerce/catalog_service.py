"""Read-side catalog, favorites, addresses and browse-event services.

HTTP-independent query logic lives here. Shared row serializers remain in
'packages.commerce.models' so cards, reviews and addresses have one shape
across all surfaces.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Any, Iterable, Sequence

from packages.commerce import db as dbutil
from packages.commerce import models
from packages.commerce.web import ApiError

PRODUCT_KINDS = ("self", "brand", "factory")
PRODUCT_SORTS = ("featured", "newest", "price_asc", "price_desc", "rating", "sales")
REVIEW_SORTS = ("recent", "helpful", "rating_desc")

PRODUCT_MATCH_COLUMNS = (
    "p.title",
    "p.subtitle",
    "p.brand",
    "p.tags_json",
    "c.name",
    "c.subtitle",
    "c.slug",
)

_PHONE_RE = re.compile(r"^1[3-9][0-9]{9}$")
_ADDRESS_ALLOWED = {
    "label",
    "receiver",
    "phone",
    "province",
    "city",
    "district",
    "detail",
    "is_default",
}
_ADDRESS_REQUIRED = ("receiver", "phone", "province", "city", "district", "detail")
_ADDRESS_LABELS = {
    "label": "地址标签",
    "receiver": "收货人",
    "phone": "手机号",
    "province": "省份",
    "city": "城市",
    "district": "区县",
    "detail": "详细地址",
    "is_default": "默认地址",
}


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


def normalize_query(raw: str | None) -> str:
    """Trim and collapse whitespace; repeated tokens are searched only once."""
    if raw is None:
        return ""
    seen: list[str] = []
    seen_folded: set[str] = set()
    for token in str(raw).split():
        folded = token.casefold()
        if folded not in seen_folded:
            seen.append(token)
            seen_folded.add(folded)
    return " ".join(seen)


def _like_contains(token: str) -> str:
    escaped = token.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _search_rows(conn: sqlite3.Connection, normalized_q: str) -> list[sqlite3.Row]:
    where = ["p.status='active'"]
    params: list[Any] = []
    for token in normalized_q.split():
        pattern = _like_contains(token)
        alternatives = " OR ".join(
            f"LOWER({column}) LIKE LOWER(?) ESCAPE '!'" for column in PRODUCT_MATCH_COLUMNS
        )
        where.append(f"({alternatives})")
        params.extend([pattern] * len(PRODUCT_MATCH_COLUMNS))
    sql = f"""
        SELECT p.* FROM products p
        JOIN categories c ON c.slug = p.category_slug
        WHERE {' AND '.join(where)}
        ORDER BY p.featured DESC, p.rating DESC, p.sold_count DESC, p.id ASC
    """
    return dbutil.all_rows(conn, sql, tuple(params))


def _active_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return dbutil.all_rows(
        conn,
        "SELECT * FROM products WHERE status='active' ORDER BY featured DESC, rating DESC, sold_count DESC, id ASC",
    )


def _pairs(conn: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> list[tuple[sqlite3.Row, dict[str, Any]]]:
    return [(row, models.product_card(conn, row)) for row in rows]


def _cards(pairs: Iterable[tuple[sqlite3.Row, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [card for _row, card in pairs]


def _sort_cards(cards: Sequence[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "newest":
        return sorted(cards, key=lambda c: (str(c["created_at"]), int(c["id"])), reverse=True)
    if sort == "price_asc":
        return sorted(
            cards,
            key=lambda c: (int(c["price_cents"]), 0 if c["featured"] else 1, -float(c["rating"]), int(c["id"])),
        )
    if sort == "price_desc":
        return sorted(
            cards,
            key=lambda c: (-int(c["price_cents"]), 0 if c["featured"] else 1, -float(c["rating"]), int(c["id"])),
        )
    if sort == "rating":
        return sorted(
            cards,
            key=lambda c: (-float(c["rating"]), -int(c["review_count"]), -int(c["sold_count"]), int(c["id"])),
        )
    if sort == "sales":
        return sorted(
            cards,
            key=lambda c: (-int(c["sold_count"]), -float(c["rating"]), int(c["id"])),
        )
    return sorted(
        cards,
        key=lambda c: (0 if c["featured"] else 1, -float(c["rating"]), -int(c["sold_count"]), int(c["id"])),
    )


def _pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total else 0


def _category_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return dbutil.all_rows(conn, "SELECT * FROM categories ORDER BY sort_order, name")


def get_categories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return models.categories_with_counts(conn)


# ---------------------------------------------------------------------------
# Product listing / detail / reviews / suggestions
# ---------------------------------------------------------------------------


def list_products(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    category: str | None = None,
    kind: str | None = None,
    sort: str = "featured",
    min_price_cents: int | None = None,
    max_price_cents: int | None = None,
    min_rating: float | None = None,
    page: int = 1,
    page_size: int = 12,
) -> dict[str, Any]:
    normalized = normalize_query(query)
    category = category or None
    kind = kind or None
    pairs = _pairs(conn, _search_rows(conn, normalized))

    def filtered(skip: frozenset[str] = frozenset()) -> list[tuple[sqlite3.Row, dict[str, Any]]]:
        kept: list[tuple[sqlite3.Row, dict[str, Any]]] = []
        for pair in pairs:
            _row, card = pair
            if category is not None and "category" not in skip and card["category"]["slug"] != category:
                continue
            if kind is not None and "kind" not in skip and kind not in card["offer_kinds"]:
                continue
            if "price" not in skip:
                price = int(card["price_cents"])
                if min_price_cents is not None and price < int(min_price_cents):
                    continue
                if max_price_cents is not None and price > int(max_price_cents):
                    continue
            if min_rating is not None and float(card["rating"]) < float(min_rating):
                continue
            kept.append(pair)
        return kept

    matching = filtered()
    ordered = _sort_cards(_cards(matching), sort)
    total = len(ordered)
    offset = (page - 1) * page_size
    items = ordered[offset : offset + page_size]

    category_counts = Counter(
        str(card["category"]["slug"]) for _row, card in filtered(frozenset({"category"}))
    )
    facet_categories = [
        {"slug": row["slug"], "name": row["name"], "count": category_counts[row["slug"]]}
        for row in _category_rows(conn)
        if category_counts.get(row["slug"], 0) > 0
    ]

    kind_counts: Counter[str] = Counter()
    for _row, card in filtered(frozenset({"kind"})):
        for offer_kind in card["offer_kinds"]:
            kind_counts[str(offer_kind)] += 1
    facet_kinds = [
        {"kind": offer_kind, "count": kind_counts[offer_kind]}
        for offer_kind in PRODUCT_KINDS
        if kind_counts.get(offer_kind, 0) > 0
    ]

    price_pairs = filtered(frozenset({"price"}))
    prices = [int(card["price_cents"]) for _row, card in price_pairs]
    facet_price_min = min(prices) if prices else 0
    facet_price_max = max(prices) if prices else 0

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": _pages(total, page_size),
        "query": normalized,
        "sort": sort,
        "facets": {
            "categories": facet_categories,
            "kinds": facet_kinds,
            "price_min_cents": facet_price_min,
            "price_max_cents": facet_price_max,
        },
    }


def _product_or_404(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = models.get_product_row(conn, slug)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "商品不存在")
    return row


def _recent_review_rows(
    conn: sqlite3.Connection, product_id: int, *, limit: int | None = None
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM reviews WHERE product_id=? ORDER BY created_at DESC, id DESC"
    params: list[Any] = [int(product_id)]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return dbutil.all_rows(conn, sql, tuple(params))


def get_product_detail(
    conn: sqlite3.Connection, slug: str, viewer_id: int | None = None
) -> dict[str, Any]:
    row = _product_or_404(conn, slug)
    detail = models.product_detail(conn, row)
    detail["related"] = models.related_cards(conn, row)
    summary = detail["reviews"]["summary"]
    detail["reviews"] = {
        "summary": summary,
        "items": [
            models.review_payload(review, viewer_id=viewer_id)
            for review in _recent_review_rows(conn, int(row["id"]), limit=3)
        ],
    }
    return detail


def _review_tags(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in rows:
        tags = dbutil.json_loads(row["tags_json"], [])
        if not isinstance(tags, list):
            continue
        seen_tags: set[str] = set()
        for tag in tags:
            if isinstance(tag, str) and tag and tag not in seen_tags:
                seen_tags.add(tag)
                counts[tag] += 1
    return [
        {"label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def get_reviews(
    conn: sqlite3.Connection,
    slug: str,
    *,
    rating: int | None = None,
    sort: str = "recent",
    page: int = 1,
    page_size: int = 12,
    viewer_id: int | None = None,
) -> dict[str, Any]:
    row = _product_or_404(conn, slug)
    product_id = int(row["id"])
    summary, _ignored = models.review_summary(conn, product_id)

    where = ["product_id=?"]
    params: list[Any] = [product_id]
    if rating is not None:
        where.append("rating=?")
        params.append(int(rating))
    order_by = {
        "recent": "created_at DESC, id DESC",
        "helpful": "helpful_count DESC, created_at DESC, id DESC",
        "rating_desc": "rating DESC, created_at DESC, id DESC",
    }.get(sort, "created_at DESC, id DESC")
    all_matching = dbutil.all_rows(
        conn,
        f"SELECT * FROM reviews WHERE {' AND '.join(where)} ORDER BY {order_by}",
        tuple(params),
    )
    total = len(all_matching)
    offset = (page - 1) * page_size
    page_rows = all_matching[offset : offset + page_size]
    return {
        "summary": summary,
        "items": [models.review_payload(review, viewer_id=viewer_id) for review in page_rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": _pages(total, page_size),
        "facets": {"tags": _review_tags(all_matching)},
    }


def _hot_terms(conn: sqlite3.Connection, limit: int = 6) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: Any) -> None:
        text = str(term).strip() if term is not None else ""
        folded = text.casefold()
        if text and folded not in seen:
            seen.add(folded)
            terms.append(text)

    recent = dbutil.all_rows(
        conn,
        """SELECT query FROM browse_events
           WHERE event_type='search' AND query<>''
           GROUP BY LOWER(query) ORDER BY MAX(created_at) DESC LIMIT ?""",
        (int(limit),),
    )
    for row in recent:
        add(row["query"])

    tag_weights: Counter[str] = Counter()
    for row in dbutil.all_rows(
        conn, "SELECT tags_json, sold_count FROM products WHERE status='active'"
    ):
        tags = dbutil.json_loads(row["tags_json"], [])
        if not isinstance(tags, list):
            continue
        weight = max(1, int(row["sold_count"]))
        seen_tags: set[str] = set()
        for tag in tags:
            if isinstance(tag, str) and tag and tag not in seen_tags:
                seen_tags.add(tag)
                tag_weights[tag] += weight
    for tag, _weight in sorted(tag_weights.items(), key=lambda item: (-item[1], item[0])):
        add(tag)
        if len(terms) >= limit:
            break

    if len(terms) < limit:
        for row in dbutil.all_rows(
            conn, "SELECT name FROM categories ORDER BY sort_order, name"
        ):
            add(row["name"])
            if len(terms) >= limit:
                break
    return terms[:limit]


def suggest(conn: sqlite3.Connection, query: str | None) -> dict[str, Any]:
    normalized = normalize_query(query)
    if not normalized:
        return {"suggestions": [], "products": [], "hot": _hot_terms(conn)}

    cards = _cards(_pairs(conn, _search_rows(conn, normalized)))
    products = [
        {"slug": card["slug"], "title": card["title"], "cover": card["cover"]}
        for card in cards[:6]
    ]

    needles = [part.casefold() for part in normalized.split()]
    suggestions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(text: Any, kind: str) -> None:
        value = str(text).strip() if text is not None else ""
        key = (value.casefold(), kind)
        if value and key not in seen:
            seen.add(key)
            suggestions.append({"text": value, "kind": kind})

    for card in cards:
        add(card["title"], "product")
        category = card["category"]
        if any(needle in str(category["name"]).casefold() for needle in needles):
            add(category["name"], "category")
        if any(needle in str(card["brand"]).casefold() for needle in needles):
            add(card["brand"], "brand")
        for tag in card.get("tags") or []:
            if isinstance(tag, str) and any(needle in tag.casefold() for needle in needles):
                add(tag, "tag")

    return {"suggestions": suggestions[:8], "products": products, "hot": _hot_terms(conn)}


# ---------------------------------------------------------------------------
# Browse events and favorites
# ---------------------------------------------------------------------------


def record_view(conn: sqlite3.Connection, owner_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    extra = sorted(set(payload) - {"product_slug"})
    if extra:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "请求包含未知字段",
            {extra[0]: "未知字段"},
        )
    slug = payload.get("product_slug")
    if not isinstance(slug, str) or not slug.strip():
        raise ApiError(400, "VALIDATION_ERROR", "请提供商品 slug", {"product_slug": "必填"})
    slug = slug.strip()
    if len(slug) > 64:
        raise ApiError(400, "VALIDATION_ERROR", "商品 slug 无效", {"product_slug": "长度超出限制"})
    row = models.get_product_row(conn, slug)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "商品不存在")
    with dbutil.transaction(conn):
        conn.execute(
            "INSERT INTO browse_events(owner_key, product_id, event_type, query, created_at) VALUES(?,?, 'view', '', ?)",
            (owner_key, int(row["id"]), dbutil.iso()),
        )
    return {"recorded": True}


def list_favorites(conn: sqlite3.Connection, owner_key: str) -> dict[str, Any]:
    rows = dbutil.all_rows(
        conn,
        """SELECT p.* FROM favorites f JOIN products p ON p.id=f.product_id
           WHERE f.owner_key=? AND p.status='active'
           ORDER BY f.created_at DESC, p.id DESC""",
        (owner_key,),
    )
    items = models.cards_for_rows(conn, rows)
    return {"items": items, "total": len(items)}


def _active_favorite_total(conn: sqlite3.Connection, owner_key: str) -> int:
    return int(
        dbutil.scalar(
            conn,
            """SELECT COUNT(*) FROM favorites f JOIN products p ON p.id=f.product_id
               WHERE f.owner_key=? AND p.status='active'""",
            (owner_key,),
        )
        or 0
    )


def toggle_favorite(
    conn: sqlite3.Connection, owner_key: str, slug: str
) -> dict[str, Any]:
    row = _product_or_404(conn, slug)
    product_id = int(row["id"])
    with dbutil.transaction(conn):
        existing = dbutil.one(
            conn,
            "SELECT 1 FROM favorites WHERE owner_key=? AND product_id=?",
            (owner_key, product_id),
        )
        if existing is None:
            conn.execute(
                "INSERT INTO favorites(owner_key, product_id, created_at) VALUES(?,?,?)",
                (owner_key, product_id, dbutil.iso()),
            )
            favorited = True
        else:
            conn.execute(
                "DELETE FROM favorites WHERE owner_key=? AND product_id=?",
                (owner_key, product_id),
            )
            favorited = False
        total = _active_favorite_total(conn, owner_key)
    return {"favorited": favorited, "total": total}


# ---------------------------------------------------------------------------
# Home aggregation
# ---------------------------------------------------------------------------


def _recent_view_rows(conn: sqlite3.Connection, owner_key: str, limit: int) -> list[sqlite3.Row]:
    return dbutil.all_rows(
        conn,
        """SELECT p.* FROM products p JOIN (
               SELECT product_id, MAX(created_at) AS last_at
               FROM browse_events WHERE owner_key=? AND event_type='view'
               GROUP BY product_id
           ) b ON b.product_id=p.id
           WHERE p.status='active'
           ORDER BY b.last_at DESC, p.id DESC LIMIT ?""",
        (owner_key, int(limit)),
    )


def _recommendation_picks(
    conn: sqlite3.Connection,
    cards: Sequence[dict[str, Any]],
    owner_key: str | None,
    user_id: int | None,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    signal_rows: list[sqlite3.Row] = []
    if owner_key:
        signal_rows.extend(_recent_view_rows(conn, owner_key, 12))
        if user_id is not None:
            signal_rows.extend(
                dbutil.all_rows(
                    conn,
                    """SELECT p.* FROM favorites f JOIN products p ON p.id=f.product_id
                       WHERE f.owner_key=? AND p.status='active'
                       ORDER BY f.created_at DESC LIMIT 12""",
                    (owner_key,),
                )
            )
            signal_rows.extend(
                dbutil.all_rows(
                    conn,
                    """SELECT DISTINCT p.* FROM order_items oi
                       JOIN orders o ON o.id=oi.order_id
                       JOIN products p ON p.id=oi.product_id
                       WHERE o.user_id=? AND o.status='COMPLETED' AND p.status='active'
                       LIMIT 12""",
                    (int(user_id),),
                )
            )

    signal_ids: set[int] = set()
    preferred_categories: set[str] = set()
    for row in signal_rows:
        product_id = int(row["id"])
        signal_ids.add(product_id)
        preferred_categories.add(str(row["category_slug"]))

    ordered = _sort_cards(cards, "featured")
    candidates = [card for card in ordered if int(card["id"]) not in signal_ids]
    if preferred_categories:
        preferred = [
            card for card in candidates if str(card["category"]["slug"]) in preferred_categories
        ]
        rest = [
            card for card in candidates if str(card["category"]["slug"]) not in preferred_categories
        ]
        picks = preferred + rest
    else:
        picks = candidates
    if not picks:
        picks = ordered
    return picks[:limit]


def get_home(
    conn: sqlite3.Connection, *, owner_key: str | None = None, user_id: int | None = None
) -> dict[str, Any]:
    rows = _active_rows(conn)
    pairs = _pairs(conn, rows)
    cards = _cards(pairs)
    hero_pair: tuple[sqlite3.Row, dict[str, Any]] | None = None
    hero_card: dict[str, Any] | None = None
    for pair in pairs:
        if int(pair[0]["is_hero"]) == 1:
            hero_pair = pair
            hero_card = pair[1]
            break
    if hero_pair is None and pairs:
        hero_pair = pairs[0]
        hero_card = pairs[0][1]

    hero = None
    hero_id: int | None = None
    if hero_pair is not None and hero_card is not None:
        hero_row = hero_pair[0]
        hero_id = int(hero_row["id"])
        badges = hero_card.get("badges") or []
        hero = {
            "product_slug": hero_card["slug"],
            "image": hero_card["cover"],
            "eyebrow": "本周编辑选物",
            "title": hero_card["title"],
            "body": hero_card["subtitle"] or hero_row["description"] or hero_card["title"],
            "price_cents": int(hero_card["price_cents"]),
            "badge": badges[0] if badges else "编辑选物",
        }

    hero_side = [
        card for row, card in pairs if hero_id is None or int(row["id"]) != hero_id
    ][:2]

    ordered_cards = _sort_cards(cards, "featured")
    shelves: list[dict[str, Any]] = []
    if ordered_cards:
        shelves.append(
            {
                "key": "editors",
                "title": "编辑精选",
                "subtitle": "从真实评分与成交里挑出的当季好物",
                "href": "/search?sort=featured",
                "items": ordered_cards[:6],
            }
        )
    if hero is not None and hero_card is not None:
        hero_category = hero_card["category"]
        category_cards = [
            card for card in ordered_cards if card["category"]["slug"] == hero_category["slug"]
        ][:4]
        category_row = dbutil.one(
            conn, "SELECT * FROM categories WHERE slug=?", (hero_category["slug"],)
        )
        if category_cards and category_row is not None:
            shelves.append(
                {
                    "key": f"category:{hero_category['slug']}",
                    "title": category_row["name"],
                    "subtitle": category_row["subtitle"] or "这一类目里的当季选择",
                    "href": f"/search?category={hero_category['slug']}",
                    "items": category_cards,
                }
            )
    latest_cards = _sort_cards(cards, "newest")
    if latest_cards:
        shelves.append(
            {
                "key": "new",
                "title": "新到开集",
                "subtitle": "刚刚上架的编辑选物",
                "href": "/search?sort=newest",
                "items": latest_cards[:4],
            }
        )

    deals = sorted(
        (card for card in cards if int(card["discount_percent"]) > 0),
        key=lambda card: (
            -int(card["discount_percent"]),
            -int(card["saved_cents"]),
            -float(card["rating"]),
            int(card["id"]),
        ),
    )[:4]

    group_picks = sorted(
        (card for card in cards if card.get("group")),
        key=lambda card: (
            -(int(card["price_cents"]) - int(card["group"]["price_cents"])),
            -float(card["rating"]),
            int(card["id"]),
        ),
    )[:4]

    new_cards = sorted(
        (card for card in cards if card["is_new"]),
        key=lambda card: (str(card["created_at"]), int(card["id"])),
        reverse=True,
    )[:4]
    if not new_cards:
        new_cards = latest_cards[:4]

    recent_cards = (
        models.cards_for_rows(conn, _recent_view_rows(conn, owner_key, 4))
        if owner_key
        else []
    )
    recommended = _recommendation_picks(conn, cards, owner_key, user_id)

    stats = {
        "products": len(rows),
        "shops": int(dbutil.scalar(conn, "SELECT COUNT(*) FROM shops") or 0),
        "reviews": int(dbutil.scalar(conn, "SELECT COUNT(*) FROM reviews") or 0),
    }
    trust = [
        {
            "icon": "shield",
            "title": "正品可追溯",
            "detail": "每件商品都带店铺与供给来源信息。",
        },
        {
            "icon": "truck",
            "title": "履约信息透明",
            "detail": "预计时效与运费在供给报价中逐项展示。",
        },
        {
            "icon": "return",
            "title": "售后可追踪",
            "detail": "订单、物流与售后进度都有服务端记录。",
        },
    ]

    return {
        "categories": models.categories_with_counts(conn),
        "stats": stats,
        "hero": hero,
        "hero_side": hero_side,
        "shelves": shelves[:3],
        "deals": deals,
        "group_picks": group_picks,
        "new_arrivals": new_cards,
        "recently_viewed": recent_cards,
        "recommended": recommended,
        "trust": trust,
    }


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


def _address_error(field: str, detail: str, message: str | None = None) -> None:
    raise ApiError(
        400,
        "VALIDATION_ERROR",
        message or f"{_ADDRESS_LABELS.get(field, field)}格式不正确",
        {field: detail},
    )


def _address_text(payload: dict[str, Any], field: str, *, min_len: int, max_len: int) -> str:
    if field not in payload:
        _address_error(field, "必填")
    value = payload[field]
    if not isinstance(value, str):
        _address_error(field, "必须是文字")
    value = value.strip()
    if not (min_len <= len(value) <= max_len):
        _address_error(field, f"长度需为 {min_len}-{max_len} 个字符")
    return value


def _validated_address_payload(
    payload: dict[str, Any], *, partial: bool
) -> dict[str, Any]:
    unknown = sorted(set(payload) - _ADDRESS_ALLOWED)
    if unknown:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "请求包含未知字段",
            {unknown[0]: "未知字段"},
        )
    if partial and not payload:
        raise ApiError(400, "VALIDATION_ERROR", "没有可更新的地址字段", {"body": "至少提供一个字段"})

    if not partial:
        missing = {
            field: f"{_ADDRESS_LABELS[field]}必填"
            for field in _ADDRESS_REQUIRED
            if field not in payload
        }
        if missing:
            raise ApiError(400, "VALIDATION_ERROR", "请填写完整的收货地址", missing)

    values: dict[str, Any] = {}
    if "receiver" in payload:
        values["receiver"] = _address_text(payload, "receiver", min_len=2, max_len=20)
    if "phone" in payload:
        raw_phone = payload["phone"]
        if not isinstance(raw_phone, str):
            _address_error("phone", "必须是文字")
        phone = raw_phone.strip()
        if not _PHONE_RE.fullmatch(phone):
            _address_error("phone", "请输入 11 位大陆手机号")
        values["phone"] = phone
    for field in ("province", "city", "district"):
        if field in payload:
            values[field] = _address_text(payload, field, min_len=1, max_len=30)
    if "detail" in payload:
        values["detail"] = _address_text(payload, "detail", min_len=5, max_len=120)
    if "label" in payload:
        values["label"] = _address_text(payload, "label", min_len=0, max_len=10)
    elif not partial:
        values["label"] = ""
    if "is_default" in payload:
        if not isinstance(payload["is_default"], bool):
            _address_error("is_default", "必须是布尔值")
        values["is_default"] = payload["is_default"]
    elif not partial:
        values["is_default"] = False
    return values


def _current_owned_address(
    conn: sqlite3.Connection, user_id: int, address_id: int
) -> sqlite3.Row:
    row = dbutil.one(
        conn,
        "SELECT * FROM addresses WHERE id=? AND user_id=?",
        (int(address_id), int(user_id)),
    )
    if row is None:
        # Cross-user addresses are indistinguishable from missing addresses.
        raise ApiError(404, "NOT_FOUND", "地址不存在")
    return row


def list_addresses(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    rows = dbutil.all_rows(
        conn,
        """SELECT * FROM addresses WHERE user_id=?
           ORDER BY is_default DESC, updated_at DESC, id DESC""",
        (int(user_id),),
    )
    items = [models.address_payload(row) for row in rows]
    default_id = next((item["id"] for item in items if item["is_default"]), None)
    return {"items": items, "default_id": default_id}


def create_address(
    conn: sqlite3.Connection, user_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    values = _validated_address_payload(payload, partial=False)
    now = dbutil.iso()
    is_default = bool(values.get("is_default"))
    with dbutil.transaction(conn):
        if is_default:
            conn.execute(
                "UPDATE addresses SET is_default=0 WHERE user_id=? AND is_default=1",
                (int(user_id),),
            )
        cursor = conn.execute(
            """INSERT INTO addresses(user_id,label,receiver,phone,province,city,district,detail,
                 is_default,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(user_id),
                values["label"],
                values["receiver"],
                values["phone"],
                values["province"],
                values["city"],
                values["district"],
                values["detail"],
                1 if is_default else 0,
                now,
                now,
            ),
        )
        address_id = int(cursor.lastrowid)
        row = dbutil.one(conn, "SELECT * FROM addresses WHERE id=?", (address_id,))
    assert row is not None
    return models.address_payload(row)


def update_address(
    conn: sqlite3.Connection, user_id: int, address_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    values = _validated_address_payload(payload, partial=True)
    now = dbutil.iso()
    with dbutil.transaction(conn):
        _current_owned_address(conn, user_id, address_id)
        if values.get("is_default") is True:
            conn.execute(
                "UPDATE addresses SET is_default=0 WHERE user_id=? AND id<>?",
                (int(user_id), int(address_id)),
            )
        assignments: list[str] = []
        params: list[Any] = []
        for field in ("label", "receiver", "phone", "province", "city", "district", "detail"):
            if field in values:
                assignments.append(f"{field}=?")
                params.append(values[field])
        if "is_default" in values:
            assignments.append("is_default=?")
            params.append(1 if values["is_default"] else 0)
        assignments.append("updated_at=?")
        params.append(now)
        params.extend([int(address_id), int(user_id)])
        conn.execute(
            f"UPDATE addresses SET {', '.join(assignments)} WHERE id=? AND user_id=?",
            tuple(params),
        )
        row = dbutil.one(
            conn,
            "SELECT * FROM addresses WHERE id=? AND user_id=?",
            (int(address_id), int(user_id)),
        )
    assert row is not None
    return models.address_payload(row)


def delete_address(
    conn: sqlite3.Connection, user_id: int, address_id: int
) -> dict[str, Any]:
    with dbutil.transaction(conn):
        _current_owned_address(conn, user_id, address_id)
        conn.execute(
            "DELETE FROM addresses WHERE id=? AND user_id=?",
            (int(address_id), int(user_id)),
        )
    return {"deleted": True}
