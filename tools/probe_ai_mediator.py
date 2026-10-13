#!/usr/bin/env python3
"""One-shot real model probe for OpenShop's grounded AI mediator.

It reads the local demo database read-only, builds the same public catalog the
server uses, calls the configured OpenAI-compatible gateway, and validates the
JSON through the production parser. It never prints the API key. Run only as an
operator with an explicitly configured gateway.

Example:
  OPENSHOP_AI_API_KEY_FILE=/home/infra/.config/dsh-n600-token \
  python3 -B tools/probe_ai_mediator.py --db /path/to/openshop.sqlite3
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.commerce.ai_catalog import build_catalog
from packages.commerce.ai_config import AISettings
from packages.commerce.ai_provider import call_provider, extract_json
from packages.commerce.ai_service import _parse_model_result
from packages.commerce.web import ApiError


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the real OpenShop AI mediator")
    parser.add_argument("--db", required=True)
    parser.add_argument("--brief", default="我想买一副降噪耳机，预算 1000 元以内，希望两天内到，愿意考虑自营或品牌店。")
    parser.add_argument("--base-url", default=os.environ.get("OPENSHOP_AI_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("OPENSHOP_AI_MODEL", "deepseek-flash"))
    parser.add_argument("--model-label", default=os.environ.get("OPENSHOP_AI_MODEL_LABEL", "DeepSeek Flash"))
    parser.add_argument("--key-file", default=os.environ.get("OPENSHOP_AI_API_KEY_FILE", ""))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    def emit(payload: dict, exit_code: int) -> int:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return exit_code

    if args.base_url:
        os.environ["OPENSHOP_AI_BASE_URL"] = args.base_url
    if args.key_file:
        os.environ["OPENSHOP_AI_API_KEY_FILE"] = args.key_file
    os.environ["OPENSHOP_AI_MODEL"] = args.model
    os.environ["OPENSHOP_AI_MODEL_LABEL"] = args.model_label
    os.environ["OPENSHOP_AI_TIMEOUT_SECONDS"] = str(args.timeout)
    settings = AISettings.load()
    if not settings.enabled:
        return emit({"probe": "openshop_ai_mediator", "verdict": "FAIL", "reason": "not_configured"}, 2)
    db_path = Path(args.db).resolve()
    conn = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        snapshot = build_catalog(conn)
    finally:
        conn.close()
    pair_map = snapshot["pair_map"]
    if not pair_map:
        return emit({"probe": "openshop_ai_mediator", "verdict": "FAIL", "reason": "empty_catalog"}, 2)
    payload = {
        "brief": args.brief,
        "recent_messages": [],
        "catalog_units": "CNY cents",
        "catalog": snapshot["catalog"],
    }
    try:
        raw, usage, provider_ms = call_provider(settings, payload)
        parsed = extract_json(raw)
        result, recommendation = _parse_model_result(parsed, pair_map, user_id=1)
    except ApiError as exc:
        return emit({
            "probe": "openshop_ai_mediator",
            "verdict": "FAIL",
            "code": exc.code,
            "message": exc.message,
            "products": snapshot["product_count"],
            "offers": snapshot["offer_count"],
            "skus": snapshot["sku_count"],
        }, 1)
    except Exception as exc:
        return emit({
            "probe": "openshop_ai_mediator",
            "verdict": "FAIL",
            "error": type(exc).__name__,
        }, 1)
    safe_recommendation = None
    if recommendation is not None:
        safe_recommendation = {
            "product": recommendation["product"]["title"],
            "shop": recommendation["shop"]["name"],
            "offer_id": recommendation["offer_id"],
            "sku_id": recommendation["sku_id"],
            "buy_mode": recommendation["buy_mode"],
            "unit_price_cents": recommendation["unit_price_cents"],
            "market_price_cents": recommendation["market_price_cents"],
            "headline": recommendation["headline"],
            "why": recommendation["why"],
            "tradeoffs": recommendation["tradeoffs"],
            "plan_created": bool(recommendation.get("plan_id")),
        }
    output = {
        "probe": "openshop_ai_mediator",
        "verdict": "PASS",
        "model": settings.model,
        "provider_latency_ms": provider_ms,
        "usage": usage,
        "catalog": {
            "products": snapshot["product_count"],
            "offers": snapshot["offer_count"],
            "skus": snapshot["sku_count"],
        },
        "answer": result["answer"],
        "needs": result["needs"],
        "questions": result["questions"],
        "recommendation": safe_recommendation,
        "alternatives": [
            {
                "offer_id": alt["offer_id"],
                "sku_id": alt["sku_id"],
                "buy_mode": alt["buy_mode"],
                "product": alt["product"]["title"],
                "shop": alt["shop"]["name"],
                "unit_price_cents": alt["unit_price_cents"],
                "why": alt["why"],
            }
            for alt in result["alternatives"]
        ],
    }
    return emit(output, 0)


if __name__ == "__main__":
    raise SystemExit(main())
