"""Configuration, quotas and short-lived AI plan storage for OpenShop."""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from packages.commerce import db as dbutil
from packages.commerce.web import ApiError, RequestContext

LOG = logging.getLogger("openshop.ai")
AI_TZ = timezone(timedelta(hours=8))
PLAN_TTL_SECONDS = 900
_MAX_PLAN_ITEMS = 256

DISCLOSURE = (
    "AI 建议由外部模型基于公开商品供给生成；你提交的需求和最近对话会发送给模型服务，"
    "不会发送账号、地址、订单或支付信息。页面上的价格、库存与时效由 OpenShop 服务端复核，"
    "AI 不会自动下单或支付。"
)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class AISettings:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    model_label: str
    timeout_seconds: float
    max_output_tokens: int
    global_daily_limit: int
    user_daily_limit: int
    ip_window_limit: int
    ip_window_seconds: int
    max_message_chars: int

    @classmethod
    def load(cls) -> "AISettings":
        base_url = os.environ.get("OPENSHOP_AI_BASE_URL", "").strip().rstrip("/")
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            base_url = ""
        key = os.environ.get("OPENSHOP_AI_API_KEY", "").strip()
        if not key:
            key_file = os.environ.get("OPENSHOP_AI_API_KEY_FILE", "").strip()
            if not key_file:
                cred_dir = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
                if cred_dir:
                    candidate = os.path.join(cred_dir, "openshop_ai_token")
                    if os.path.isfile(candidate):
                        key_file = candidate
            if key_file:
                try:
                    with open(key_file, "r", encoding="utf-8") as handle:
                        key = handle.read(4096).strip()
                except OSError:
                    LOG.warning("OPENSHOP_AI_KEYFILE unreadable")
        model = os.environ.get("OPENSHOP_AI_MODEL", "deepseek-flash").strip() or "deepseek-flash"
        label = os.environ.get("OPENSHOP_AI_MODEL_LABEL", "DeepSeek Flash").strip() or "DeepSeek Flash"
        enabled = bool(base_url and key) and _env_bool("OPENSHOP_AI_ENABLED", True)
        return cls(
            enabled=enabled,
            base_url=base_url,
            api_key=key,
            model=model,
            model_label=label,
            timeout_seconds=_env_float("OPENSHOP_AI_TIMEOUT_SECONDS", 55.0, 5.0, 90.0),
            max_output_tokens=_env_int("OPENSHOP_AI_MAX_OUTPUT_TOKENS", 4000, 512, 8000),
            global_daily_limit=_env_int("OPENSHOP_AI_GLOBAL_DAILY_LIMIT", 80, 1, 100000),
            user_daily_limit=_env_int("OPENSHOP_AI_USER_DAILY_LIMIT", 6, 1, 1000),
            ip_window_limit=_env_int("OPENSHOP_AI_IP_WINDOW_LIMIT", 4, 1, 1000),
            ip_window_seconds=_env_int("OPENSHOP_AI_IP_WINDOW_SECONDS", 600, 10, 86400),
            max_message_chars=_env_int("OPENSHOP_AI_MAX_MESSAGE_CHARS", 500, 20, 500),
        )

    def limits_public(self) -> dict[str, int]:
        return {
            "user_daily": self.user_daily_limit,
            "ip_window": self.ip_window_limit,
            "ip_window_seconds": self.ip_window_seconds,
            "global_daily": self.global_daily_limit,
        }


class IpWindowGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def note(self, ip: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[ip]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry = max(1, int(window_seconds - (now - events[0])) + 1)
                raise ApiError(429, "AI_RATE_LIMIT", f"AI 中介请求过于频繁，请在约 {retry} 秒后再试。")
            events.append(now)
            if len(self._events) > 20000:
                stale = [key for key, queue in self._events.items() if not queue]
                for key in stale[:1000]:
                    self._events.pop(key, None)


IP_GATE = IpWindowGate()
MODEL_SLOTS = threading.BoundedSemaphore(2)


@dataclass
class Plan:
    user_id: int
    offer_id: int
    sku_id: int
    buy_mode: str
    expires_at: float


class PlanStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plans: dict[str, Plan] = {}

    def _prune(self, now: float) -> None:
        expired = [key for key, plan in self._plans.items() if plan.expires_at <= now]
        for key in expired:
            self._plans.pop(key, None)
        if len(self._plans) > _MAX_PLAN_ITEMS:
            oldest = sorted(self._plans.items(), key=lambda item: item[1].expires_at)
            for key, _ in oldest[: max(1, len(self._plans) - _MAX_PLAN_ITEMS)]:
                self._plans.pop(key, None)

    def put(self, user_id: int, offer_id: int, sku_id: int, buy_mode: str) -> str:
        now = time.time()
        with self._lock:
            self._prune(now)
            plan_id = secrets.token_hex(16)
            while plan_id in self._plans:
                plan_id = secrets.token_hex(16)
            self._plans[plan_id] = Plan(
                user_id=user_id,
                offer_id=offer_id,
                sku_id=sku_id,
                buy_mode=buy_mode,
                expires_at=now + PLAN_TTL_SECONDS,
            )
            return plan_id

    def take(self, user_id: int, plan_id: str) -> Plan | None:
        with self._lock:
            self._prune(time.time())
            plan = self._plans.get(plan_id)
            if plan is None or plan.user_id != user_id:
                return None
            self._plans.pop(plan_id, None)
            return plan

    def restore(self, plan_id: str, plan: Plan) -> None:
        with self._lock:
            if plan.expires_at > time.time():
                self._plans[plan_id] = plan
                self._prune(time.time())


PLANS = PlanStore()


def day_key() -> str:
    return datetime.now(AI_TZ).date().isoformat()


def read_usage(ctx: RequestContext, settings: AISettings, user_id: int | None) -> dict[str, int | None]:
    day = day_key()
    global_used = int(dbutil.scalar(ctx.db, "SELECT value FROM meta WHERE key=?", (f"ai_quota:{day}:global",)) or 0)
    user_used: int | None = None
    if user_id is not None:
        user_used = int(dbutil.scalar(ctx.db, "SELECT value FROM meta WHERE key=?", (f"ai_quota:{day}:user:{user_id}",)) or 0)
    return {
        "global_used_today": global_used,
        "global_remaining_today": max(0, settings.global_daily_limit - global_used),
        "user_used_today": user_used,
        "user_remaining_today": None if user_used is None else max(0, settings.user_daily_limit - user_used),
    }


def consume_quota(ctx: RequestContext, settings: AISettings, user_id: int) -> dict[str, int]:
    day = day_key()
    global_key = f"ai_quota:{day}:global"
    user_key = f"ai_quota:{day}:user:{user_id}"
    with dbutil.transaction(ctx.db) as conn:
        global_used = int(dbutil.scalar(conn, "SELECT value FROM meta WHERE key=?", (global_key,)) or 0)
        user_used = int(dbutil.scalar(conn, "SELECT value FROM meta WHERE key=?", (user_key,)) or 0)
        if global_used >= settings.global_daily_limit:
            raise ApiError(429, "AI_RATE_LIMIT", "今天全站 AI 中介额度已用完，请明天再来。")
        if user_used >= settings.user_daily_limit:
            raise ApiError(429, "AI_RATE_LIMIT", "你今天的 AI 中介次数已用完，请明天再来。")
        new_global = global_used + 1
        new_user = user_used + 1
        for key, value in ((global_key, new_global), (user_key, new_user)):
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
    return {
        "user_remaining_today": max(0, settings.user_daily_limit - new_user),
        "global_remaining_today": max(0, settings.global_daily_limit - new_global),
    }


def get_status(ctx: RequestContext) -> dict[str, Any]:
    settings = AISettings.load()
    user = ctx.user
    user_id = int(user["id"]) if user is not None else None
    if settings.enabled:
        usage = read_usage(ctx, settings, user_id)
    else:
        usage = {
            "global_used_today": 0,
            "global_remaining_today": settings.global_daily_limit,
            "user_used_today": 0 if user_id is not None else None,
            "user_remaining_today": settings.user_daily_limit if user_id is not None else None,
        }
    return {
        "enabled": settings.enabled,
        "mode": "live" if settings.enabled else "unavailable",
        "model_label": settings.model_label,
        "requires_login": True,
        "disclosure": DISCLOSURE,
        "limits": settings.limits_public(),
        "usage": usage,
        "message": None if settings.enabled else "AI 中介当前未配置；传统搜索、购物、结算与商家功能不受影响。",
        "plan_ttl_seconds": PLAN_TTL_SECONDS,
    }
