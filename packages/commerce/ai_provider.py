"""OpenAI-compatible model transport for the OpenShop AI mediator."""
from __future__ import annotations

import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from packages.commerce.ai_config import AISettings
from packages.commerce.ai_prompt import SYSTEM_PROMPT
from packages.commerce.web import ApiError

LOG = logging.getLogger("openshop.ai")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_FENCE = chr(96) * 3


def invalid_response(reason: str) -> ApiError:
    LOG.warning("OPENSHOP_AI_INVALID reason=%s", reason)
    return ApiError(502, "AI_INVALID_RESPONSE", "AI 返回了无法校验的方案，未修改购物车。请重试。")


def extract_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith(_FENCE):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith(_FENCE):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except (TypeError, ValueError):
        pass
    start = cleaned.find("{")
    if start < 0:
        raise invalid_response("no_json_object")
    try:
        value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        return value
    except (TypeError, ValueError):
        raise invalid_response("invalid_json")


def call_provider(settings: AISettings, user_payload: dict[str, Any]) -> tuple[str, dict[str, int], int]:
    request_body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        "max_tokens": settings.max_output_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    encoded = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        settings.base_url + "/chat/completions",
        data=encoded,
        method="POST",
        headers={
            "Authorization": "Bearer " + settings.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OpenShop-AI-Mediation/1.0",
        },
    )
    started = time.monotonic()
    try:
        context = ssl.create_default_context() if settings.base_url.startswith("https://") else None
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds, context=context) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise invalid_response("provider_too_large")
    except urllib.error.HTTPError as exc:
        LOG.warning("OPENSHOP_AI_PROVIDER upstream_http=%s", exc.code)
        if exc.code == 429:
            raise ApiError(429, "AI_RATE_LIMIT", "AI 模型服务当前繁忙，请稍后再试。")
        raise ApiError(503, "AI_UNAVAILABLE", "AI 中介暂时不可用，请稍后再试。")
    except (TimeoutError, socket.timeout):
        LOG.warning("OPENSHOP_AI_PROVIDER timeout")
        raise ApiError(504, "AI_TIMEOUT", "AI 中介响应超时，请稍后重试；购物车未改动。")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            LOG.warning("OPENSHOP_AI_PROVIDER timeout")
            raise ApiError(504, "AI_TIMEOUT", "AI 中介响应超时，请稍后重试；购物车未改动。")
        LOG.warning("OPENSHOP_AI_PROVIDER transport=%s", type(reason).__name__)
        raise ApiError(503, "AI_UNAVAILABLE", "AI 中介暂时不可用，请稍后再试。")
    except ApiError:
        raise
    except Exception as exc:
        LOG.warning("OPENSHOP_AI_PROVIDER transport=%s", type(exc).__name__)
        raise ApiError(503, "AI_UNAVAILABLE", "AI 中介暂时不可用，请稍后再试。")
    elapsed_ms = round((time.monotonic() - started) * 1000)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise invalid_response("provider_body")
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    if not isinstance(choices, list) or not choices:
        raise invalid_response("provider_choices")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    finish_reason = first.get("finish_reason")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    usage_raw = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage_raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            usage[key] = int(value)
    if not isinstance(content, str) or not content.strip():
        reasoning = message.get("reasoning_content")
        LOG.warning(
            "OPENSHOP_AI_PROVIDER empty_content finish=%s message_keys=%s content_len=%s reasoning_len=%s prompt_tokens=%s completion_tokens=%s",
            finish_reason,
            ",".join(sorted(str(key) for key in message.keys()))[:120],
            len(content) if isinstance(content, str) else -1,
            len(reasoning) if isinstance(reasoning, str) else -1,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )
        raise invalid_response("provider_content_empty")
    if len(content) > 120000:
        raise invalid_response("provider_content_too_large")
    LOG.info(
        "OPENSHOP_AI_PROVIDER http=200 latency_ms=%s prompt_tokens=%s completion_tokens=%s",
        elapsed_ms,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )
    return content, usage, elapsed_ms
