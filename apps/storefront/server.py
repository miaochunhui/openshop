#!/usr/bin/env python3
"""OpenShop standard-library web server.

Run:
  python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780 \
      --data-dir /hf3fs-jd/data/tmp/openshop/dev-db

No external Python packages, no build pipeline, no third-party CDN.
"""
from __future__ import annotations

import argparse
import http.cookies
import ipaddress
import json
import logging
import mimetypes
import os
import signal
import sqlite3
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.commerce import auth, db as dbutil  # noqa: E402
from packages.commerce.seed import SeedMismatchError  # noqa: E402
from packages.commerce.web import ApiError, MAX_BODY_BYTES, RequestContext, Response, Router  # noqa: E402

APP_VERSION = "0.2.0"
WEB_ROOT = (REPO_ROOT / "web").resolve()
LOG = logging.getLogger("openshop")

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self'; font-src 'self' data:; object-src 'none'"
    ),
}

def resolve_client_ip(peer_ip: str, forwarded_for: str | None, trusted_raw: str | None = None) -> str:
    """Resolve the real client IP only when the direct peer is a trusted proxy.

    X-Forwarded-For is untrusted unless the TCP peer is explicitly configured in
    OPENSHOP_TRUSTED_PROXIES. We walk the chain from right to left and return the
    first address that is not itself a trusted proxy; malformed entries are
    ignored and an absent/malformed chain falls back to the direct peer. This
    keeps spoofed headers from controlling auth/AI rate-limit buckets.
    """
    peer_text = str(peer_ip or "").strip()
    try:
        peer = ipaddress.ip_address(peer_text)
    except ValueError:
        return peer_text or "unknown"
    if trusted_raw is None:
        trusted_raw = os.environ.get("OPENSHOP_TRUSTED_PROXIES", "")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for token in str(trusted_raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    if not networks or not any(peer in network for network in networks):
        return str(peer)
    chain: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for token in str(forwarded_for or "").split(",")[:20]:
        try:
            chain.append(ipaddress.ip_address(token.strip()))
        except ValueError:
            continue
    for address in reversed(chain):
        if not any(address in network for network in networks):
            return str(address)
    return str(chain[0]) if chain else str(peer)


STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}

SPA_PREFIXES = ("/", "/search", "/p/", "/cart", "/checkout", "/account", "/orders/", "/g/", "/seller", "/about", "/help")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_router() -> Router:
    router = Router()
    from apps.storefront.routes import core

    core.register(router)
    # Domain route modules are implemented by independent seats. They are
    # optional at boot so the shell can be inspected while work is in flight.
    for module_name in ("catalog", "trade", "seller", "ai"):
        try:
            module = __import__(f"apps.storefront.routes.{module_name}", fromlist=["register"])
            register = getattr(module, "register", None)
            if callable(register):
                register(router)
        except ModuleNotFoundError as exc:
            if exc.name == f"apps.storefront.routes.{module_name}":
                LOG.warning("route module not installed yet: %s", module_name)
                continue
            raise
    return router


class OpenShopHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler_cls, *, db_path: Path, router: Router):
        self.db_path = db_path
        self.router = router
        super().__init__(server_address, handler_cls)


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenShop/0.1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> OpenShopHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def version_string(self) -> str:
        return self.server_version

    def _send(self, status: int, body: bytes | str, *, content_type: str, extra_headers: dict[str, str] | None = None, cookies: list[str] | None = None, cache: str | None = None) -> None:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.close_connection = True
        headers = dict(SECURITY_HEADERS)
        headers["Connection"] = "close"
        if cache is not None:
            headers["Cache-Control"] = cache
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(payload))
        if extra_headers:
            headers.update(extra_headers)
        for key, value in headers.items():
            if "\n" not in str(value) and "\r" not in str(value):
                self.send_header(key, str(value))
        for cookie in cookies or []:
            if "\n" not in cookie and "\r" not in cookie:
                self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, status: int, obj: dict, *, cookies: list[str] | None = None, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send(status, body, content_type="application/json; charset=utf-8", cookies=cookies, extra_headers=extra_headers)

    def _error(self, status: int, code: str, message: str, *, fields: dict | None = None, cookies: list[str] | None = None, request_id: str | None = None) -> None:
        error: dict = {"code": code, "message": message}
        if fields:
            error["fields"] = fields
        self._json(
            status,
            {
                "error": error,
                "meta": {"request_id": request_id or uuid.uuid4().hex, "server_time": utc_now_iso(), "currency": "CNY", "demo": True},
            },
            cookies=cookies,
        )

    def _parse_cookies(self) -> dict[str, str]:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return {}
        try:
            jar = http.cookies.SimpleCookie()
            jar.load(raw)
            return {key: morsel.value for key, morsel in jar.items()}
        except http.cookies.CookieError:
            return {}

    def _is_secure(self) -> bool:
        if os.environ.get("OPENSHOP_SECURE_COOKIES", "") == "1":
            return True
        return (self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https")

    def _read_body(self) -> bytes:
        length_raw = self.headers.get("Content-Length")
        if length_raw is None:
            return b""
        try:
            length = int(length_raw)
        except ValueError as exc:
            raise ApiError(400, "VALIDATION_ERROR", "Content-Length 无效") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "请求体过大")
        return self.rfile.read(length)

    def _csrf_ok(self, method: str, session: sqlite3.Row | None, headers: dict[str, str]) -> bool:
        if method in {"GET", "HEAD", "OPTIONS"}:
            return True
        if session is None:
            return False
        supplied = headers.get("x-openshop-csrf", "")
        expected = session["csrf"]
        import hmac

        return bool(supplied) and hmac.compare_digest(str(supplied), str(expected))

    def _client_ip(self, headers: dict[str, str]) -> str:
        return resolve_client_ip(self.client_address[0], headers.get("x-forwarded-for", ""))

    def _handle_api(self, parsed) -> None:
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        cookies = self._parse_cookies()
        token = cookies.get(auth.SESSION_COOKIE)
        headers = {k.lower(): v for k, v in self.headers.items()}
        conn = None
        try:
            body = self._read_body()
            conn = dbutil.connect(self.app.db_path)
            session = auth.load_session(conn, token)
            if self.command not in {"GET", "HEAD", "OPTIONS"} and not self._csrf_ok(self.command, session, headers):
                raise ApiError(403, "CSRF_FAILED", "会话或安全令牌已失效，请刷新页面后重试")
            ctx = RequestContext(
                method=self.command,
                path=parsed.path,
                raw_query=parsed.query,
                headers=headers,
                body=body,
                db=conn,
                session=session,
                session_token=token,
                request_id=request_id,
                server_time=utc_now_iso(),
                client_ip=self._client_ip(headers),
                is_secure=self._is_secure(),
            )
            result = self.app.router.dispatch(ctx)
            if not isinstance(result, Response):
                result = ctx.finish(result)
            if result.raw is not None:
                payload = result.raw.encode("utf-8") if isinstance(result.raw, str) else result.raw
                self._send(result.status, payload, content_type=result.content_type or "application/json; charset=utf-8", cookies=result.cookies, extra_headers=result.headers)
                return
            data = result.data if result.data is not None else {}
            envelope = {
                "data": data,
                "meta": {
                    "request_id": request_id,
                    "server_time": ctx.server_time,
                    "currency": "CNY",
                    "demo": True,
                },
            }
            body_bytes = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self._send(result.status, body_bytes, content_type="application/json; charset=utf-8", extra_headers=result.headers, cookies=result.cookies)
        except ApiError as exc:
            self._error(exc.status, exc.code, exc.message, fields=exc.fields, request_id=request_id)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            LOG.error("Unhandled API error request_id=%s method=%s path=%s\n%s", request_id, self.command, parsed.path, traceback.format_exc())
            self._error(500, "INTERNAL_ERROR", "服务暂时不可用，请稍后再试", request_id=request_id)
        finally:
            if conn is not None:
                conn.close()
            LOG.info("request_id=%s method=%s path=%s status_ms=%.1f", request_id, self.command, parsed.path, (time.monotonic() - started) * 1000)

    def _serve_static_or_spa(self, parsed) -> None:
        path = unquote(parsed.path)
        # Reject control characters (notably NUL) before Path touches them.
        # Without this, Path.resolve() raises ValueError outside the API error
        # boundary and the client gets an empty connection reset.
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
            self._send(400, "Bad Request\n", content_type="text/plain; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.send_response(302)
            self.send_header("Location", "/web/assets/brand/favicon.svg")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            return
        if path == "/robots.txt":
            self._send(200, "User-agent: *\nAllow: /\nDisallow: /api/\n", content_type="text/plain; charset=utf-8", cache="public, max-age=3600")
            return
        if path in {"", "/"}:
            rel = "index.html"
        elif path.startswith("/web/"):
            rel = path.lstrip("/")
        else:
            rel = "index.html"
        try:
            candidate = (REPO_ROOT / rel).resolve()
        except (OSError, ValueError):
            self._send(404, "Not Found\n", content_type="text/plain; charset=utf-8")
            return
        is_asset = path.startswith("/web/")
        try:
            inside = candidate.is_relative_to(REPO_ROOT) and candidate.is_relative_to(WEB_ROOT)
        except (OSError, ValueError):
            inside = False
        if inside and candidate.is_file() and candidate.name != "index.html":
            content_type = STATIC_MIME.get(candidate.suffix.lower()) or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self._send(200, candidate.read_bytes(), content_type=content_type, cache="public, max-age=600")
            return
        if path == "/" or (not is_asset and not Path(path).suffix and path.startswith(SPA_PREFIXES)):
            index = WEB_ROOT / "index.html"
            if index.is_file():
                self._send(200, index.read_bytes(), content_type="text/html; charset=utf-8", cache="no-store")
                return
        self._send(404, "Not Found\n", content_type="text/plain; charset=utf-8")

    def _handle_health(self) -> None:
        if self.path.startswith("/healthz"):
            self._send(200, "ok\n", content_type="text/plain; charset=utf-8")
            return
        self._send(
            200,
            json.dumps({"ok": True, "app": "openshop", "version": APP_VERSION, "demo": True}, separators=(",", ":")),
            content_type="application/json; charset=utf-8",
        )

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path in {"/healthz", "/api/health"}:
            self._handle_health()
        elif parsed.path == "/api" or parsed.path.startswith("/api/"):
            self._handle_api(parsed)
        else:
            self._serve_static_or_spa(parsed)

    def do_HEAD(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path in {"/healthz", "/api/health"}:
            self._handle_health()
        elif parsed.path == "/api" or parsed.path.startswith("/api/"):
            self._handle_api(parsed)
        else:
            self._serve_static_or_spa(parsed)

    def do_POST(self) -> None:
        self._handle_api(urlsplit(self.path))

    def do_PATCH(self) -> None:
        self._handle_api(urlsplit(self.path))

    def do_DELETE(self) -> None:
        self._handle_api(urlsplit(self.path))

    def do_PUT(self) -> None:
        self._handle_api(urlsplit(self.path))

    def do_OPTIONS(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api"):
            self._error(405, "METHOD_NOT_ALLOWED", "此服务不支持跨域请求")
        else:
            self._send(405, "Method Not Allowed\n", content_type="text/plain; charset=utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenShop storefront server")
    parser.add_argument("--host", default=os.environ.get("OPENSHOP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OPENSHOP_PORT", "8780")))
    parser.add_argument("--data-dir", default=os.environ.get("OPENSHOP_DATA_DIR", str(Path.home() / ".local/share/openshop")))
    parser.add_argument("--catalog", default=os.environ.get("OPENSHOP_CATALOG", str(REPO_ROOT / "data/catalog.json")))
    parser.add_argument("--log-level", default=os.environ.get("OPENSHOP_LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    if not (1 <= args.port <= 65535):
        print("OPENSHOP_START FAIL reason=invalid_port", file=sys.stderr)
        return 2
    try:
        db_path = dbutil.init_database(args.data_dir, args.catalog)
    except SeedMismatchError as exc:
        print(f"OPENSHOP_START FAIL reason=seed_mismatch detail={exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        LOG.exception("database initialization failed")
        print(f"OPENSHOP_START FAIL reason=db_init detail={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    router = build_router()
    server = OpenShopHTTPServer((args.host, args.port), Handler, db_path=db_path, router=router)
    LOG.info("OPENSHOP_START app=openshop version=%s host=%s port=%s data_dir=%s db=%s", APP_VERSION, args.host, args.port, args.data_dir, db_path)
    LOG.info("OPENSHOP_READY url=http://%s:%s/ routes=%s", args.host, args.port, len(router.routes))

    def _stop(_signum, _frame):
        LOG.info("OPENSHOP_STOP requested")
        import threading

        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        server.serve_forever(poll_interval=0.4)
    finally:
        server.server_close()
        LOG.info("OPENSHOP_STOPPED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
