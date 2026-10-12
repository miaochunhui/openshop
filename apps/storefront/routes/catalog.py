"""Catalog HTTP routes registered into the shared router."""
from __future__ import annotations

import re
from typing import Any

from packages.commerce import catalog_service
from packages.commerce.web import ApiError, RequestContext, Router

_MIN_RATING_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def _trimmed(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _query_enum(
    ctx: RequestContext,
    name: str,
    allowed: tuple[str, ...],
    *,
    default: str | None = None,
) -> str | None:
    raw = _trimmed(ctx.q(name))
    if raw is None:
        return default
    if raw not in allowed:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            f"参数 {name} 取值无效",
            {name: f"可选值：{', '.join(allowed)}"},
        )
    return raw


def _min_rating(ctx: RequestContext) -> float | None:
    raw = _trimmed(ctx.q("min_rating"))
    if raw is None:
        return None
    if not _MIN_RATING_RE.fullmatch(raw):
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "参数 min_rating 必须是 0-5 的数字",
            {"min_rating": "请输入 0-5 的数字"},
        )
    value = float(raw)
    if not 0 <= value <= 5:
        raise ApiError(
            400,
            "VALIDATION_ERROR",
            "参数 min_rating 必须是 0-5 的数字",
            {"min_rating": "请输入 0-5 的数字"},
        )
    return value


def _viewer_id(ctx: RequestContext) -> int | None:
    user = ctx.user
    return int(user["id"]) if user is not None else None


def _user_owner_key(ctx: RequestContext) -> str:
    user = ctx.require_user()
    return f"u:{int(user['id'])}"


def _address_id(raw: str) -> int:
    if not raw.isascii() or not raw.isdigit():
        raise ApiError(400, "VALIDATION_ERROR", "地址 ID 必须是正整数", {"address_id": "格式无效"})
    return int(raw)


def register(router: Router) -> None:
    @router.add("GET", "/api/categories")
    def categories(ctx: RequestContext) -> list[dict[str, Any]]:
        return catalog_service.get_categories(ctx.db)

    @router.add("GET", "/api/home")
    def home(ctx: RequestContext) -> dict[str, Any]:
        owner_key = ctx.owner_key if ctx.session is not None else None
        return catalog_service.get_home(
            ctx.db,
            owner_key=owner_key,
            user_id=_viewer_id(ctx),
        )

    @router.add("GET", "/api/products")
    def products(ctx: RequestContext) -> dict[str, Any]:
        kind = _query_enum(ctx, "kind", catalog_service.PRODUCT_KINDS)
        sort = _query_enum(
            ctx, "sort", catalog_service.PRODUCT_SORTS, default="featured"
        ) or "featured"
        page, page_size = ctx.pagination(default_size=12, max_size=24)
        return catalog_service.list_products(
            ctx.db,
            query=ctx.q("q"),
            category=_trimmed(ctx.q("category")),
            kind=kind,
            sort=sort,
            min_price_cents=ctx.q_int("min_price_cents", None, minimum=0),
            max_price_cents=ctx.q_int("max_price_cents", None, minimum=0),
            min_rating=_min_rating(ctx),
            page=page,
            page_size=page_size,
        )

    @router.add("GET", "/api/products/{slug}")
    def product_detail(ctx: RequestContext, slug: str) -> dict[str, Any]:
        return catalog_service.get_product_detail(ctx.db, slug, viewer_id=_viewer_id(ctx))

    @router.add("GET", "/api/products/{slug}/reviews")
    def product_reviews(ctx: RequestContext, slug: str) -> dict[str, Any]:
        sort = _query_enum(
            ctx, "sort", catalog_service.REVIEW_SORTS, default="recent"
        ) or "recent"
        page, page_size = ctx.pagination(default_size=12, max_size=24)
        return catalog_service.get_reviews(
            ctx.db,
            slug,
            rating=ctx.q_int("rating", None, minimum=1, maximum=5),
            sort=sort,
            page=page,
            page_size=page_size,
            viewer_id=_viewer_id(ctx),
        )

    @router.add("GET", "/api/search/suggest")
    def suggest(ctx: RequestContext) -> dict[str, Any]:
        return catalog_service.suggest(ctx.db, ctx.q("q"))

    @router.add("POST", "/api/events/view", auth="session")
    def record_view(ctx: RequestContext) -> dict[str, Any]:
        ctx.require_session()
        return catalog_service.record_view(ctx.db, ctx.owner_key, ctx.json_object())

    @router.add("GET", "/api/favorites", auth="user")
    def favorites(ctx: RequestContext) -> dict[str, Any]:
        return catalog_service.list_favorites(ctx.db, _user_owner_key(ctx))

    @router.add("POST", "/api/favorites/{slug}/toggle", auth="user")
    def toggle_favorite(ctx: RequestContext, slug: str) -> dict[str, Any]:
        return catalog_service.toggle_favorite(ctx.db, _user_owner_key(ctx), slug)

    @router.add("GET", "/api/addresses", auth="user")
    def addresses(ctx: RequestContext) -> dict[str, Any]:
        user = ctx.require_user()
        return catalog_service.list_addresses(ctx.db, int(user["id"]))

    @router.add("POST", "/api/addresses", auth="user")
    def create_address(ctx: RequestContext) -> dict[str, Any]:
        user = ctx.require_user()
        return catalog_service.create_address(ctx.db, int(user["id"]), ctx.json_object())

    @router.add("PATCH", "/api/addresses/{address_id}", auth="user")
    def update_address(ctx: RequestContext, address_id: str) -> dict[str, Any]:
        user = ctx.require_user()
        return catalog_service.update_address(
            ctx.db,
            int(user["id"]),
            _address_id(address_id),
            ctx.json_object(),
        )

    @router.add("DELETE", "/api/addresses/{address_id}", auth="user")
    def delete_address(ctx: RequestContext, address_id: str) -> dict[str, Any]:
        user = ctx.require_user()
        return catalog_service.delete_address(
            ctx.db,
            int(user["id"]),
            _address_id(address_id),
        )
