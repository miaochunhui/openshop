"""AI mediator orchestration, validation and one-use plan adoption."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from packages.commerce import trade_service
from packages.commerce.ai_catalog import build_catalog, clean_text, public_selection
from packages.commerce.ai_config import (
    AISettings,
    DISCLOSURE,
    IP_GATE,
    MODEL_SLOTS,
    PLAN_TTL_SECONDS,
    PLANS,
    Plan,
    consume_quota,
)
from packages.commerce.ai_provider import call_provider, extract_json, invalid_response
from packages.commerce.web import ApiError, RequestContext

LOG = logging.getLogger("openshop.ai")
_MAX_HISTORY_ITEMS = 6
_MAX_HISTORY_CHARS = 2400
_PLAN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PRICE_TEXT_RE = re.compile(r"(?:[¥￥]\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*元)")


def _strip_prices(value: str) -> str:
    return _PRICE_TEXT_RE.sub("（金额以服务端卡片为准）", value)


def _validate_request(body: dict[str, Any], max_chars: int) -> tuple[str, list[dict[str, str]]]:
    message = body.get("message")
    if not isinstance(message, str):
        raise ApiError(400, "VALIDATION_ERROR", "请用文字描述你的购物需求", {"message": "请输入购物需求"})
    message = clean_text(message, max_chars + 1)
    if len(message) < 2:
        raise ApiError(400, "VALIDATION_ERROR", "请至少输入 2 个字的具体需求", {"message": "请补充购物需求"})
    if len(message) > max_chars:
        raise ApiError(400, "VALIDATION_ERROR", f"购物需求最多 {max_chars} 字", {"message": f"最多 {max_chars} 字"})
    raw_history = body.get("history", [])
    if raw_history is None:
        raw_history = []
    if not isinstance(raw_history, list):
        raise ApiError(400, "VALIDATION_ERROR", "对话历史格式无效", {"history": "必须是数组"})
    if len(raw_history) > _MAX_HISTORY_ITEMS:
        raise ApiError(400, "VALIDATION_ERROR", "对话历史过长，请开启新的委托")
    history: list[dict[str, str]] = []
    total_chars = 0
    for item in raw_history:
        if not isinstance(item, dict):
            raise ApiError(400, "VALIDATION_ERROR", "对话历史格式无效")
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise ApiError(400, "VALIDATION_ERROR", "对话历史格式无效")
        content = clean_text(content, 501)
        if not content or len(content) > 500:
            raise ApiError(400, "VALIDATION_ERROR", "对话历史每条最多 500 字")
        total_chars += len(content)
        if total_chars > _MAX_HISTORY_CHARS:
            raise ApiError(400, "VALIDATION_ERROR", "对话历史总长过大，请开启新的委托")
        history.append({"role": role, "content": content})
    return message, history


def _model_text(parsed: dict[str, Any], field: str, limit: int, *, required: bool = False, strip_prices: bool = True) -> str:
    value = parsed.get(field)
    text = clean_text(value, limit + 1)
    if strip_prices:
        text = _strip_prices(text)
    if len(text) > limit:
        raise invalid_response(f"text_too_long:{field}")
    if required and not text:
        raise invalid_response(f"missing_text:{field}")
    return text


def _parse_model_result(
    parsed: Any,
    pair_map: dict[tuple[int, int], dict[str, Any]],
    user_id: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(parsed, dict):
        raise invalid_response("top_level")
    answer = _model_text(parsed, "answer", 500, required=True)
    raw_needs = parsed.get("needs", [])
    if not isinstance(raw_needs, list):
        raw_needs = []
    needs: list[str] = []
    for item in raw_needs[:5]:
        text = clean_text(item, 41)
        if text:
            if len(text) > 40:
                text = text[:40].rstrip()
            needs.append(text)
    raw_questions = parsed.get("questions", [])
    if not isinstance(raw_questions, list):
        raw_questions = []
    questions: list[str] = []
    for item in raw_questions[:1]:
        text = _strip_prices(clean_text(item, 121))
        if text:
            questions.append(text[:120].rstrip())
    raw_recommendation = parsed.get("recommendation")
    recommendation: dict[str, Any] | None = None
    if raw_recommendation is not None:
        if not isinstance(raw_recommendation, dict):
            raise invalid_response("recommendation_type")
        offer_id = raw_recommendation.get("offer_id")
        sku_id = raw_recommendation.get("sku_id")
        if (
            isinstance(offer_id, bool) or isinstance(sku_id, bool)
            or not isinstance(offer_id, int) or not isinstance(sku_id, int)
        ):
            raise invalid_response("recommendation_ids")
        mode = raw_recommendation.get("buy_mode")
        if mode not in {"standard", "group"}:
            raise invalid_response("recommendation_mode")
        pair = pair_map.get((offer_id, sku_id))
        if pair is None:
            raise invalid_response("recommendation_pair")
        if mode == "group" and pair["offer"].get("group") is None:
            raise invalid_response("recommendation_group")
        try:
            recommendation = public_selection(pair, mode)
        except ValueError:
            raise invalid_response("recommendation_price")
        headline = _model_text(raw_recommendation, "headline", 80, required=True)
        why = _model_text(raw_recommendation, "why", 320, required=True)
        tradeoffs = _model_text(raw_recommendation, "tradeoffs", 260, required=True)
        plan_id = PLANS.put(user_id, offer_id, sku_id, mode)
        recommendation.update({
            "headline": headline,
            "why": why,
            "tradeoffs": tradeoffs,
            "plan_id": plan_id,
        })
    elif questions:
        recommendation = None
    alternatives: list[dict[str, Any]] = []
    raw_alternatives = parsed.get("alternatives", [])
    if not isinstance(raw_alternatives, list):
        raw_alternatives = []
    seen_offers = {int(recommendation["offer_id"])} if recommendation is not None else set()
    for item in raw_alternatives[:3]:
        if not isinstance(item, dict):
            continue
        offer_id = item.get("offer_id")
        sku_id = item.get("sku_id")
        mode = item.get("buy_mode")
        if (
            isinstance(offer_id, bool) or isinstance(sku_id, bool)
            or not isinstance(offer_id, int) or not isinstance(sku_id, int)
            or mode not in {"standard", "group"} or offer_id in seen_offers
        ):
            continue
        pair = pair_map.get((offer_id, sku_id))
        if pair is None:
            continue
        if mode == "group" and pair["offer"].get("group") is None:
            continue
        try:
            alternative = public_selection(pair, mode)
        except ValueError:
            continue
        why = _strip_prices(clean_text(item.get("why"), 161))
        if not why:
            continue
        alternative["why"] = why[:160].rstrip()
        alternatives.append(alternative)
        seen_offers.add(offer_id)
    return {
        "answer": answer,
        "needs": needs,
        "questions": questions,
        "alternatives": alternatives,
    }, recommendation


def mediate(ctx: RequestContext, body: dict[str, Any]) -> dict[str, Any]:
    settings = AISettings.load()
    user = ctx.user
    if user is None:
        raise ApiError(401, "AUTH_REQUIRED", "登录后才能使用 AI 中介")
    if not settings.enabled:
        raise ApiError(503, "AI_UNAVAILABLE", "AI 中介当前未配置，请稍后再来。")
    user_id = int(user["id"])
    message, history = _validate_request(body, settings.max_message_chars)
    IP_GATE.note(ctx.client_ip or "unknown", settings.ip_window_limit, settings.ip_window_seconds)
    if not MODEL_SLOTS.acquire(blocking=False):
        raise ApiError(429, "AI_BUSY", "AI 正在处理较多委托，请稍后再试。")
    started = time.monotonic()
    ok = False
    try:
        quota = consume_quota(ctx, settings, user_id)
        snapshot = build_catalog(ctx.db)
        pair_map: dict[tuple[int, int], dict[str, Any]] = snapshot["pair_map"]
        if not pair_map:
            raise ApiError(503, "AI_UNAVAILABLE", "当前没有可售供给，无法生成中介方案。")
        catalog_text = json.dumps(snapshot["catalog"], ensure_ascii=False, separators=(",", ":"))
        if len(catalog_text) > 80000:
            raise ApiError(503, "AI_UNAVAILABLE", "当前供给目录过大，AI 中介暂时无法处理。")
        user_payload = {
            "brief": message,
            "recent_messages": history,
            "catalog_units": "CNY cents",
            "catalog": snapshot["catalog"],
        }
        raw_text, usage, _provider_ms = call_provider(settings, user_payload)
        parsed = extract_json(raw_text)
        model_result, recommendation = _parse_model_result(parsed, pair_map, user_id)
        latency_ms = round((time.monotonic() - started) * 1000)
        ok = True
        LOG.info(
            "OPENSHOP_AI_MEDIATE request_id=%s user_id=%s status=ok latency_ms=%s products=%s offers=%s skus=%s prompt_tokens=%s completion_tokens=%s",
            ctx.request_id,
            user_id,
            latency_ms,
            snapshot["product_count"],
            snapshot["offer_count"],
            snapshot["sku_count"],
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )
        return {
            "request_id": ctx.request_id,
            "mode": "live",
            "model_label": settings.model_label,
            "latency_ms": latency_ms,
            "answer": model_result["answer"],
            "needs": model_result["needs"],
            "questions": model_result["questions"],
            "recommendation": recommendation,
            "alternatives": model_result["alternatives"],
            "disclosure": DISCLOSURE,
            "quota": quota,
            "plan_ttl_seconds": PLAN_TTL_SECONDS,
        }
    finally:
        if not ok:
            LOG.info("OPENSHOP_AI_MEDIATE request_id=%s user_id=%s status=failed", ctx.request_id, user_id)
        MODEL_SLOTS.release()


def adopt_plan(ctx: RequestContext, plan_id: str) -> dict[str, Any]:
    user = ctx.user
    if user is None:
        raise ApiError(401, "AUTH_REQUIRED", "登录后才能采纳 AI 中介方案")
    if not isinstance(plan_id, str) or not _PLAN_ID_RE.fullmatch(plan_id):
        raise ApiError(404, "NOT_FOUND", "方案不存在或已过期")
    plan: Plan | None = PLANS.take(int(user["id"]), plan_id)
    if plan is None:
        raise ApiError(409, "AI_PLAN_EXPIRED", "该 AI 方案已过期或已被使用，请重新生成。")
    try:
        cart = trade_service.add_cart_item(
            ctx,
            {
                "offer_id": plan.offer_id,
                "sku_id": plan.sku_id,
                "quantity": 1,
                "buy_mode": plan.buy_mode,
                "group_code": None,
            },
        )
    except Exception:
        PLANS.restore(plan_id, plan)
        raise
    LOG.info(
        "OPENSHOP_AI_ADOPT request_id=%s user_id=%s offer_id=%s sku_id=%s buy_mode=%s",
        ctx.request_id,
        int(user["id"]),
        plan.offer_id,
        plan.sku_id,
        plan.buy_mode,
    )
    return {
        "plan_id": plan_id,
        "cart": cart,
        "message": "方案已加入购物车；请确认商品、报价和数量后再继续。",
    }
