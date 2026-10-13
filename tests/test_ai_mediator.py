#!/usr/bin/env python3
"""CI-safe real-HTTP tests for the OpenShop AI mediator.

A local OpenAI-compatible fake provider makes the suite deterministic and free:
the production request parser, catalog grounding, auth/CSRF, quota, plan store
and cart adoption all run in the real server. No external API is used.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TMP_ROOT = Path(os.environ.get("OPENSHOP_TMP_BASE", str(Path(tempfile.gettempdir()) / "openshop-tests"))) / "ai-mediator"
_UNSET = object()


class FakeProvider:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0
        self.model_response: dict[str, Any] = {}
        provider = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "OpenShopFakeModel/1.0"

            def log_message(self, fmt: str, *args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length)
                try:
                    request = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    request = {}
                with provider._lock:
                    provider.count += 1
                    content = json.dumps(provider.model_response, ensure_ascii=False)
                if not isinstance(request, dict) or "messages" not in request:
                    self.send_response(400)
                    payload = b'{"error":"bad request"}'
                else:
                    self.send_response(200)
                    payload = json.dumps({
                        "id": "fake-completion",
                        "object": "chat.completion",
                        "model": request.get("model", "fake"),
                        "choices": [{
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                    }, ensure_ascii=False).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def request_count(self) -> int:
        with self._lock:
            return self.count

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class HttpClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.csrf = ""

    def request(self, method: str, path: str, body: Any = _UNSET, *, use_csrf: bool = True) -> dict[str, Any]:
        headers = {"Accept": "application/json", "Connection": "close"}
        data = None
        if body is not _UNSET and body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if method.upper() not in {"GET", "HEAD"} and use_csrf and self.csrf:
            headers["X-OpenShop-CSRF"] = self.csrf
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            response = self.opener.open(request, timeout=20)
            status = response.status
            raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        return {"status": status, "payload": payload}

    def get_session(self) -> dict[str, Any]:
        response = self.request("GET", "/api/session")
        if response["status"] == 200:
            self.csrf = str(((response.get("payload") or {}).get("data") or {}).get("csrf") or "")
        return response

    def login_demo(self) -> dict[str, Any]:
        self.get_session()
        response = self.request("POST", "/api/auth/demo", {"role": "buyer"})
        if response["status"] == 200:
            self.csrf = str(((response.get("payload") or {}).get("data") or {}).get("csrf") or "")
        return response


def _data(response: dict[str, Any]) -> dict[str, Any]:
    data = (response.get("payload") or {}).get("data")
    return data if isinstance(data, dict) else {}


def _error(response: dict[str, Any]) -> tuple[str, str]:
    error = (response.get("payload") or {}).get("error") or {}
    return str(error.get("code", "")), str(error.get("message", ""))


class AIMediatorAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        cls.run_dir = TMP_ROOT / ("run-" + stamp)
        cls.run_dir.mkdir(parents=True, exist_ok=False)
        cls.data_dir = cls.run_dir / "data"
        cls.data_dir.mkdir(parents=True, exist_ok=False)
        cls.log_path = cls.run_dir / "server.log"
        cls.provider = FakeProvider()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = int(sock.getsockname()[1])
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.log_handle = open(cls.log_path, "wb")
        cls.proc = None
        env = os.environ.copy()
        env.pop("OPENSHOP_AI_API_KEY_FILE", None)
        env.update({
            "OPENSHOP_AI_ENABLED": "1",
            "OPENSHOP_AI_BASE_URL": cls.provider.base_url,
            "OPENSHOP_AI_API_KEY": "ci-fake-provider-key",
            "OPENSHOP_AI_MODEL": "deepseek-flash",
            "OPENSHOP_AI_MODEL_LABEL": "CI Mediator",
            "OPENSHOP_AI_TIMEOUT_SECONDS": "15",
            "OPENSHOP_AI_MAX_OUTPUT_TOKENS": "512",
            "OPENSHOP_AI_GLOBAL_DAILY_LIMIT": "4",
            "OPENSHOP_AI_USER_DAILY_LIMIT": "1",
            "OPENSHOP_AI_IP_WINDOW_LIMIT": "50",
            "OPENSHOP_AI_IP_WINDOW_SECONDS": "600",
        })
        try:
            cls.proc = subprocess.Popen([
                sys.executable, "-u", "-B", "apps/storefront/server.py",
                "--host", "127.0.0.1", "--port", str(cls.port),
                "--data-dir", str(cls.data_dir), "--log-level", "INFO",
            ], cwd=str(REPO_ROOT), env=env, stdout=cls.log_handle, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 30
            healthy = False
            while time.monotonic() < deadline:
                if cls.proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(cls.base_url + "/api/health", timeout=1.5) as response:
                        if json.loads(response.read().decode("utf-8")).get("ok"):
                            healthy = True
                            break
                except Exception:
                    time.sleep(0.2)
            if not healthy:
                raise RuntimeError("AI test server did not start: " + cls._log_tail())
            db_path = cls.data_dir / "openshop.sqlite3"
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute(
                    "SELECT o.id, o.name, p.title, k.id, k.name, o.price_cents + k.price_delta_cents "
                    "FROM offers o JOIN products p ON p.id=o.product_id JOIN skus k ON k.product_id=p.id "
                    "WHERE p.status='active' AND o.status='active' AND o.stock > 0 "
                    "ORDER BY o.is_primary DESC, o.id, k.sort_order, k.id LIMIT 1"
                ).fetchone()
            if not row:
                raise RuntimeError("seed has no purchasable offer/sku")
            cls.offer_id = int(row[0])
            cls.offer_name = str(row[1])
            cls.product_title = str(row[2])
            cls.sku_id = int(row[3])
            cls.sku_name = str(row[4])
            cls.expected_unit_price = int(row[5])
            cls.provider.model_response = {
                "answer": "按真实目录筛选后，这是一套匹配当前需求的方案。",
                "needs": ["降噪耳机", "预算明确", "两天内到"],
                "recommendation": {
                    "offer_id": cls.offer_id,
                    "sku_id": cls.sku_id,
                    "buy_mode": "standard",
                    "headline": "真实供给中更合适的一件",
                    "why": "只要 ¥0.01。自营库存与到货时间符合本次委托。",
                    "tradeoffs": "不包含收纳配件；若选拼团需要等待成团。",
                },
                "alternatives": [],
                "questions": [],
            }
            print(
                "AI_TEST_SETUP verdict=PASS url=" + cls.base_url + " fake_provider=" + cls.provider.base_url
                + " offer_id=" + str(cls.offer_id) + " sku_id=" + str(cls.sku_id) + " log=" + str(cls.log_path),
                flush=True,
            )
        except Exception:
            cls._stop_server()
            cls.provider.stop()
            cls.log_handle.close()
            raise

    @classmethod
    def _stop_server(cls) -> None:
        proc = getattr(cls, "proc", None)
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=4)

    @classmethod
    def _log_tail(cls) -> str:
        try:
            return cls.log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError:
            return "<no log>"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stop_server()
        cls.provider.stop()
        try:
            cls.log_handle.close()
        except Exception:
            pass
        print("AI_TEST_EVIDENCE data_dir=" + str(cls.data_dir) + " log=" + str(cls.log_path), flush=True)

    def test_end_to_end_grounded_mediation(self) -> None:
        buyer = HttpClient(self.base_url)
        login = buyer.login_demo()
        self.assertEqual(login["status"], 200, _error(login))
        self.assertTrue(buyer.csrf)
        guest = HttpClient(self.base_url)
        self.assertEqual(guest.get_session()["status"], 200)
        with sqlite3.connect(str(self.data_dir / "openshop.sqlite3")) as conn:
            orders_before = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])

        # CSRF is required before auth or model work.
        no_csrf = buyer.request("POST", "/api/ai/mediate", {"message": "csrf negative"}, use_csrf=False)
        self.assertEqual(no_csrf["status"], 403, "missing CSRF must be rejected")
        self.assertEqual(self.provider.request_count(), 0)

        # Login is required even with a valid anonymous session + CSRF.
        unauth = guest.request("POST", "/api/ai/mediate", {"message": "auth negative"})
        self.assertEqual(unauth["status"], 401, "anonymous AI mediation must be rejected")
        self.assertEqual(guest.request("POST", "/api/ai/plans/" + "0" * 32 + "/adopt", {})["status"], 401)
        self.assertEqual(self.provider.request_count(), 0)

        status = _data(buyer.request("GET", "/api/ai/status"))
        self.assertTrue(status.get("enabled"))
        self.assertEqual(status.get("mode"), "live")
        self.assertEqual(status.get("model_label"), "CI Mediator")

        mediate = buyer.request("POST", "/api/ai/mediate", {
            "message": "预算 1000 元内挑一个两天内到的降噪耳机",
            "history": [],
        })
        self.assertEqual(mediate["status"], 200, _error(mediate))
        data = _data(mediate)
        self.assertEqual(data.get("mode"), "live")
        self.assertEqual(data.get("model_label"), "CI Mediator")
        rec = data.get("recommendation") or {}
        self.assertEqual(int(rec.get("offer_id")), self.offer_id)
        self.assertEqual(int(rec.get("sku_id")), self.sku_id)
        self.assertEqual(int(rec.get("unit_price_cents")), self.expected_unit_price)
        self.assertRegex(str(rec.get("plan_id") or ""), r"^[0-9a-f]{32}$")
        self.assertIn("why", rec)
        self.assertNotIn("¥0.01", json.dumps(data, ensure_ascii=False), "model-supplied price must be sanitized")
        self.assertIn("（金额以服务端卡片为准）", json.dumps(data, ensure_ascii=False))
        self.assertEqual(self.provider.request_count(), 1)
        quota = data.get("quota") or {}
        self.assertEqual(quota.get("user_remaining_today"), 0)

        cart_before = _data(buyer.request("GET", "/api/cart"))
        before_count = int((cart_before.get("summary") or {}).get("item_count") or 0)
        adopt = buyer.request("POST", "/api/ai/plans/" + str(rec["plan_id"]) + "/adopt", {})
        self.assertEqual(adopt["status"], 200, _error(adopt))
        adopted = _data(adopt)
        after_count = int(((adopted.get("cart") or {}).get("summary") or {}).get("item_count") or 0)
        self.assertEqual(after_count, before_count + 1)
        with sqlite3.connect(str(self.data_dir / "openshop.sqlite3")) as conn:
            orders_after = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        self.assertEqual(orders_before, orders_after, "AI adoption must not create an order")
        adopted_again = buyer.request("POST", "/api/ai/plans/" + str(rec["plan_id"]) + "/adopt", {})
        self.assertEqual(adopted_again["status"], 409, "plan must be one-use")
        self.assertEqual(_error(adopted_again)[0], "AI_PLAN_EXPIRED")

        limited = buyer.request("POST", "/api/ai/mediate", {"message": "预算内再推荐一个"})
        self.assertEqual(limited["status"], 429, "per-user daily quota must be enforced")
        self.assertEqual(_error(limited)[0], "AI_RATE_LIMIT")
        self.assertEqual(self.provider.request_count(), 1, "blocked calls must not reach the model")
        print(
            "AI_MEDIATOR_ACCEPTANCE verdict=PASS offer_id=" + str(self.offer_id) + " sku_id=" + str(self.sku_id)
            + " cart=" + str(before_count) + "->" + str(after_count) + " plan_reuse=409 quota=429 orders=0 provider_calls=1",
            flush=True,
        )


class AIHelpersTests(unittest.TestCase):
    def test_extract_json_accepts_fenced_and_prefixed_objects(self) -> None:
        from packages.commerce.ai_provider import extract_json
        fence = chr(96) * 3
        self.assertEqual(extract_json(fence + "json\n{\"a\": 1}\n" + fence), {"a": 1})
        self.assertEqual(extract_json("prefix {\"b\": 2} suffix"), {"b": 2})

    def test_extract_json_invalid_fails_closed(self) -> None:
        from packages.commerce.ai_provider import extract_json
        from packages.commerce.web import ApiError
        with self.assertRaises(ApiError) as raised:
            extract_json("not a json object")
        self.assertEqual(raised.exception.code, "AI_INVALID_RESPONSE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
