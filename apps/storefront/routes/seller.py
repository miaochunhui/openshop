"""Seller-only HTTP routes for the merchant workbench."""
from __future__ import annotations

import sqlite3
from typing import Any

from packages.commerce import seller_service
from packages.commerce.web import ApiError, RequestContext, Router


def _resource_id(raw: str, label: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(404, "NOT_FOUND", f"{label}不存在") from exc
    if value <= 0:
        raise ApiError(404, "NOT_FOUND", f"{label}不存在")
    return value


def _shop(ctx: RequestContext) -> tuple[sqlite3.Row, sqlite3.Row]:
    seller = ctx.require_seller()
    shop = seller_service.seller_shop(ctx.db, seller)
    return seller, shop


def register(router: Router) -> None:
    @router.add("GET", "/api/seller/overview", auth="seller")
    def seller_overview(ctx: RequestContext) -> dict[str, Any]:
        seller = ctx.require_seller()
        return seller_service.overview(ctx.db, seller, server_time=ctx.server_time)

    @router.add("GET", "/api/seller/products", auth="seller")
    def seller_products(ctx: RequestContext) -> dict[str, Any]:
        _, shop = _shop(ctx)
        page, page_size = ctx.pagination()
        return seller_service.list_products(
            ctx.db,
            int(shop["id"]),
            query=ctx.q("q", "") or "",
            page=page,
            page_size=page_size,
        )

    @router.add("PATCH", "/api/seller/products/{offer_id}", auth="seller")
    def seller_product_update(ctx: RequestContext, offer_id: str) -> dict[str, Any]:
        _, shop = _shop(ctx)
        return seller_service.update_product(
            ctx.db,
            int(shop["id"]),
            _resource_id(offer_id, "商品报价"),
            ctx.json_object(),
        )

    @router.add("GET", "/api/seller/orders", auth="seller")
    def seller_orders(ctx: RequestContext) -> dict[str, Any]:
        _, shop = _shop(ctx)
        page, page_size = ctx.pagination()
        return seller_service.list_orders(
            ctx.db,
            int(shop["id"]),
            status=ctx.q("status", "ALL"),
            page=page,
            page_size=page_size,
        )

    @router.add("POST", "/api/seller/orders/{package_id}/ship", auth="seller")
    def seller_ship(ctx: RequestContext, package_id: str) -> dict[str, Any]:
        _, shop = _shop(ctx)
        body = ctx.json_object()
        return seller_service.ship_package(
            ctx.db,
            int(shop["id"]),
            _resource_id(package_id, "包裹"),
            carrier=body.get("carrier"),
            tracking_no=body.get("tracking_no"),
        )

    @router.add("GET", "/api/seller/refunds", auth="seller")
    def seller_refunds(ctx: RequestContext) -> dict[str, Any]:
        _, shop = _shop(ctx)
        page, page_size = ctx.pagination()
        return seller_service.list_refunds(
            ctx.db,
            int(shop["id"]),
            status=ctx.q("status", "ALL"),
            page=page,
            page_size=page_size,
        )

    @router.add("POST", "/api/seller/refunds/{refund_id}/decision", auth="seller")
    def seller_refund_decision(ctx: RequestContext, refund_id: str) -> dict[str, Any]:
        _, shop = _shop(ctx)
        body = ctx.json_object()
        return seller_service.decide_refund(
            ctx.db,
            int(shop["id"]),
            _resource_id(refund_id, "售后申请"),
            action=body.get("action"),
            note=body.get("note", ""),
        )
