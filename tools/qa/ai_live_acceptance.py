#!/usr/bin/env python3
"""Live API acceptance for OpenShop AI mediation against a running server.

It uses only the public demo-buyer flow, checks missing-CSRF rejection, invokes
the real configured model through /api/ai/mediate, validates the grounded plan,
and adopts it into the cart. It never creates an order, pays, or reads provider
credentials. Safe to run against the public loopback/host once configured.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class Client:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base = base_url.rstrip("/")
        if not (self.base.startswith("http://") or self.base.startswith("https://")):
            raise ValueError("base URL must be http(s)")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def request(self, method: str, path: str, body: dict | None = None, *, csrf: bool = True) -> tuple[int, dict]:
        headers = {"Accept": "application/json", "User-Agent": "OpenShop-AI-Live-Acceptance/1.0"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if method != "GET" and csrf and self.csrf:
            headers["X-OpenShop-CSRF"] = self.csrf
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024)
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read(2 * 1024 * 1024)
            status = exc.code
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return status, payload["data"]
        return status, payload

    def login_demo_buyer(self) -> None:
        status, session = self.request("GET", "/api/session")
        if status != 200:
            raise RuntimeError("session bootstrap failed")
        self.csrf = str(session.get("csrf") or "")
        if not self.csrf:
            raise RuntimeError("session did not return csrf")
        status, data = self.request("POST", "/api/auth/demo", {"role": "buyer"})
        if status != 200:
            raise RuntimeError("demo login failed")
        self.csrf = str(data.get("csrf") or "")
        if not self.csrf or not data.get("user"):
            raise RuntimeError("demo login did not return user/csrf")


def emit(payload: dict, out: str, code: int) -> int:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Live OpenShop AI API acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8895")
    parser.add_argument("--brief", default="我想买一副降噪耳机，预算 1000 元以内，希望两天内到，愿意比较自营和品牌店。")
    parser.add_argument("--timeout", type=float, default=110.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    client = Client(args.base_url, args.timeout)
    started = time.monotonic()
    try:
        status_code, health = client.request("GET", "/api/health")
        client.login_demo_buyer()
        # Missing CSRF must fail before any model/DB AI work.
        original_csrf = client.csrf
        client.csrf = ""
        csrf_status, _ = client.request("POST", "/api/ai/mediate", {"message": "csrf guard probe"})
        client.csrf = original_csrf
        if csrf_status != 403:
            return emit({
                "probe": "openshop_ai_live_api",
                "verdict": "FAIL",
                "reason": "csrf_guard",
                "observed_status": csrf_status,
            }, args.out, 1)
        status_code, ai_status = client.request("GET", "/api/ai/status")
        if status_code != 200 or not ai_status.get("enabled"):
            return emit({
                "probe": "openshop_ai_live_api",
                "verdict": "FAIL",
                "reason": "ai_not_configured",
                "mode": ai_status.get("mode"),
                "message": ai_status.get("message"),
            }, args.out, 1)
        cart_before_status, cart_before = client.request("GET", "/api/cart")
        before_count = int((cart_before.get("summary") or {}).get("item_count") or 0)
        mediate_started = time.monotonic()
        status_code, data = client.request("POST", "/api/ai/mediate", {
            "message": args.brief,
            "history": [],
        })
        mediate_elapsed_ms = round((time.monotonic() - mediate_started) * 1000)
        if status_code != 200:
            return emit({
                "probe": "openshop_ai_live_api",
                "verdict": "FAIL",
                "reason": "mediate_http",
                "http": status_code,
                "error": data.get("error") if isinstance(data, dict) else None,
                "elapsed_ms": mediate_elapsed_ms,
            }, args.out, 1)
        rec = data.get("recommendation")
        if not isinstance(rec, dict) or not rec.get("plan_id"):
            return emit({
                "probe": "openshop_ai_live_api",
                "verdict": "CLARIFY",
                "reason": "model_asked_clarification",
                "answer": data.get("answer"),
                "questions": data.get("questions"),
                "elapsed_ms": mediate_elapsed_ms,
            }, args.out, 2)
        plan_id = str(rec.get("plan_id"))
        adopt_status, adopted = client.request("POST", "/api/ai/plans/" + plan_id + "/adopt", {})
        if adopt_status != 200:
            return emit({
                "probe": "openshop_ai_live_api",
                "verdict": "FAIL",
                "reason": "adopt_http",
                "http": adopt_status,
                "error": adopted.get("error") if isinstance(adopted, dict) else adopted,
            }, args.out, 1)
        cart = adopted.get("cart") if isinstance(adopted, dict) else {}
        after_count = int(((cart or {}).get("summary") or {}).get("item_count") or 0)
        if after_count <= before_count:
            return emit({
                "probe": "openshop_ai_live_api",
                "verdict": "FAIL",
                "reason": "cart_not_incremented",
                "before": before_count,
                "after": after_count,
            }, args.out, 1)
        # Confirm the plan cannot be adopted a second time.
        second_status, second = client.request("POST", "/api/ai/plans/" + plan_id + "/adopt", {})
        reused = second_status == 409 and isinstance(second, dict) and ((second.get("error") or {}).get("code") == "AI_PLAN_EXPIRED")
        if not reused:
            return emit({
                "probe": "openshop_ai_live_api",
                "verdict": "FAIL",
                "reason": "plan_reuse_guard",
                "http": second_status,
            }, args.out, 1)
        output = {
            "probe": "openshop_ai_live_api",
            "verdict": "PASS",
            "base_url": args.base_url,
            "health_version": health.get("version"),
            "mode": data.get("mode"),
            "model_label": data.get("model_label"),
            "server_latency_ms_reported": data.get("latency_ms"),
            "wall_elapsed_ms": mediate_elapsed_ms,
            "csrf_guard": "403 before model",
            "status": {
                "limits": ai_status.get("limits"),
                "usage": ai_status.get("usage"),
            },
            "mediator": {
                "answer": data.get("answer"),
                "needs": data.get("needs"),
                "questions": data.get("questions"),
                "recommendation": {
                    "product": rec.get("product", {}).get("title"),
                    "shop": rec.get("shop", {}).get("name"),
                    "shop_kind": rec.get("shop", {}).get("kind"),
                    "offer_id": rec.get("offer_id"),
                    "sku_id": rec.get("sku_id"),
                    "buy_mode": rec.get("buy_mode"),
                    "unit_price_cents": rec.get("unit_price_cents"),
                    "headline": rec.get("headline"),
                    "why": rec.get("why"),
                    "tradeoffs": rec.get("tradeoffs"),
                },
                "alternatives_count": len(data.get("alternatives") or []),
                "quota": data.get("quota"),
            },
            "adoption": {
                "cart_item_count_before": before_count,
                "cart_item_count_after": after_count,
                "second_adoption_http": second_status,
                "created_order": False,
                "payment": "not attempted",
            },
            "elapsed_ms_total": round((time.monotonic() - started) * 1000),
        }
        return emit(output, args.out, 0)
    except Exception as exc:
        return emit({
            "probe": "openshop_ai_live_api",
            "verdict": "FAIL",
            "reason": "exception",
            "error": type(exc).__name__,
            "message": str(exc)[:300],
        }, args.out, 1)


if __name__ == "__main__":
    raise SystemExit(main())
