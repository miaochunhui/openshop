#!/usr/bin/env python3
"""Probe an OpenAI-compatible chat gateway without exposing credentials.

Used before enabling OpenShop's live AI mediator. It reads the API key from an
environment variable or a mode-restricted file, sends one tiny request, and
prints only status, timing, model, usage and a short visible answer. It never
prints authorization headers or the key.

    DEEPSEEK_API_KEY=... python3 -B tools/probe_ai_provider.py
    python3 -B tools/probe_ai_provider.py --key-file /path/to/token

This probe is development/deployment tooling; CI can skip it unless a key is
explicitly configured.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://proxy.high-five-ai.xyz:8443/v1"
DEFAULT_MODEL = "deepseek-flash"


def read_key(path: str | None) -> str:
    if path:
        key = Path(path).read_text(encoding="utf-8").strip()
    else:
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENSHOP_AI_API_KEY") or ""
    if not key:
        raise SystemExit("NO_API_KEY: use DEEPSEEK_API_KEY / OPENSHOP_AI_API_KEY or --key-file")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal OpenAI-compatible gateway probe")
    parser.add_argument("--base-url", default=os.environ.get("OPENSHOP_AI_BASE_URL", DEFAULT_BASE))
    parser.add_argument("--model", default=os.environ.get("OPENSHOP_AI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--key-file", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-tokens", type=int, default=32)
    args = parser.parse_args()
    key = read_key(args.key_file)
    base = args.base_url.rstrip("/")
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You are a connectivity probe. Reply with exactly: PONG"},
            {"role": "user", "content": "Connectivity check."},
        ],
        "max_tokens": args.max_tokens,
        "temperature": 0,
    }
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OpenShop-AI-Probe/1.0",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout, context=ssl.create_default_context()) as response:
            status = response.status
            raw = response.read(200000)
    except urllib.error.HTTPError as exc:
        body = exc.read(4000).decode("utf-8", errors="replace")
        print("PROBE verdict=FAIL scope=http_status=" + str(exc.code) + " body_sha256_len=" + str(len(body)))
        return 1
    except Exception as exc:
        print("PROBE verdict=FAIL scope=transport error=" + type(exc).__name__)
        return 1
    elapsed_ms = round((time.monotonic() - started) * 1000)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("PROBE verdict=FAIL scope=response_json status=" + str(status))
        return 1
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    message = choices[0].get("message", {}) if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        content = ""
    visible = content.strip().replace("\n", " ")[:120]
    usage = parsed.get("usage") if isinstance(parsed, dict) else None
    usage_keys = sorted(usage.keys()) if isinstance(usage, dict) else []
    print(json.dumps({
        "probe": "openai_compatible_gateway",
        "verdict": "PASS" if status == 200 and content.strip() else "FAIL",
        "http": status,
        "elapsed_ms": elapsed_ms,
        "model_requested": args.model,
        "model_returned": parsed.get("model") if isinstance(parsed, dict) else None,
        "finish_reason": choices[0].get("finish_reason") if isinstance(choices, list) and choices else None,
        "visible_chars": len(content),
        "visible_preview": visible,
        "usage_keys": usage_keys,
    }, ensure_ascii=False))
    return 0 if status == 200 and content.strip() else 1


if __name__ == "__main__":
    raise SystemExit(main())
