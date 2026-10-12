"""Cart, coupon, checkout, order, group and review HTTP routes (trade seat)."""
from __future__ import annotations

from typing import Any

from packages.commerce import trade_service
from packages.commerce.web import ApiError, RequestContext, Router


def _path_int(raw: str, field: str) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "VALIDATION_ERROR", "路径参数必须是整数", {field: "必须是整数"}) from exc
    if value < 1:
        raise ApiError(400, "VALIDATION_ERROR", "路径参数必须为正整数", {field: "必须为正整数"})
    return value


def register(router: Router) -> None:
    @router.add("GET", "/api/cart", auth="session")
    def get_cart(ctx: RequestContext) -> dict[str, Any]:
        return trade_service.get_cart(ctx)

    @router.add("POST", "/api/cart/items", auth="session")
    def add_cart_item(ctx: RequestContext) -> dict[str, Any]:
        return trade_service.add_cart_item(ctx, ctx.json_object())

    @router.add("PATCH", "/api/cart/items/{item_id}", auth="session")
    def update_cart_item(ctx: RequestContext, item_id: str) -> dict[str, Any]:
        return trade_service.update_cart_item(ctx, _path_int(item_id, "item_id"), ctx.json_object())

    @router.add("DELETE", "/api/cart/items/{item_id}", auth="session")
    def delete_cart_item(ctx: RequestContext, item_id: str) -> dict[str, Any]:
        return trade_service.delete_cart_item(ctx, _path_int(item_id, "item_id"))

    @router.add("POST", "/api/cart/selection", auth="session")
    def select_cart_items(ctx: RequestContext) -> dict[str, Any]:
        return trade_service.select_cart_items(ctx, ctx.json_object())

    @router.add("POST", "/api/checkout/quote", auth="user")
    def checkout_quote(ctx: RequestContext) -> dict[str, Any]:
        return trade_service.checkout_quote(ctx, ctx.json_object())

    @router.add("GET", "/api/coupons", auth="user")
    def coupons(ctx: RequestContext) -> dict[str, Any]:
        return trade_service.list_coupons(ctx)

    @router.add("POST", "/api/coupons/{code}/claim", auth="user")
    def claim_coupon(ctx: RequestContext, code: str) -> dict[str, Any]:
        return trade_service.claim_coupon(ctx, code)

    @router.add("POST", "/api/orders", auth="user")
    def create_order(ctx: RequestContext) -> dict[str, Any]:
        return trade_service.create_order(ctx, ctx.json_object())

    @router.add("GET", "/api/orders", auth="user")
    def list_orders(ctx: RequestContext) -> dict[str, Any]:
        return trade_service.list_orders(ctx)

    @router.add("GET", "/api/orders/{order_no}", auth="user")
    def get_order(ctx: RequestContext, order_no: str) -> dict[str, Any]:
        return trade_service.get_order(ctx, order_no)

    @router.add("POST", "/api/orders/{order_no}/pay", auth="user")
    def pay_order(ctx: RequestContext, order_no: str) -> dict[str, Any]:
        return trade_service.pay_order(ctx, order_no, ctx.json_object())

    @router.add("POST", "/api/orders/{order_no}/cancel", auth="user")
    def cancel_order(ctx: RequestContext, order_no: str) -> dict[str, Any]:
        body = ctx.json_object() if ctx.body else {}
        return trade_service.cancel_order(ctx, order_no, body)

    @router.add("POST", "/api/orders/{order_no}/confirm", auth="user")
    def confirm_order(ctx: RequestContext, order_no: str) -> dict[str, Any]:
        return trade_service.confirm_order(ctx, order_no)

    @router.add("POST", "/api/orders/{order_no}/refund", auth="user")
    def request_refund(ctx: RequestContext, order_no: str) -> dict[str, Any]:
        return trade_service.request_refund(ctx, order_no, ctx.json_object())

    @router.add("POST", "/api/orders/{order_no}/reviews", auth="user")
    def submit_reviews(ctx: RequestContext, order_no: str) -> dict[str, Any]:
        return trade_service.submit_reviews(ctx, order_no, ctx.json_object())

    @router.add("GET", "/api/groups/{code}", auth="public")
    def group_view(ctx: RequestContext, code: str) -> dict[str, Any]:
        return trade_service.group_view(ctx, code)
