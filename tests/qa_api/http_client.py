#!/usr/bin/env python3
"""Cookie + CSRF aware HTTP client used by the real-backend API acceptance suite."""
from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

_OMIT = object()
_USE_SESSION_TOKEN = object()


class TransportFailure(RuntimeError):
    """Network-level failure. Never retried automatically."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass
class ApiResponse:
    method: str
    path: str
    status: int
    headers: Any
    text: str
    json_body: Any
    elapsed_ms: float

    def header_values(self, name: str) -> list[str]:
        try:
            return list(self.headers.get_all(name) or [])
        except AttributeError:
            return []

    def error_code(self) -> str | None:
        if isinstance(self.json_body, dict):
            error = self.json_body.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                return str(code) if code is not None else None
        return None

    def error_message(self) -> str:
        if isinstance(self.json_body, dict):
            error = self.json_body.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or "")
        return ""

    def data(self) -> Any:
        if isinstance(self.json_body, dict):
            return self.json_body.get("data")
        return None

    def meta(self) -> dict:
        if isinstance(self.json_body, dict) and isinstance(self.json_body.get("meta"), dict):
            return self.json_body["meta"]
        return {}

    def summary(self) -> str:
        code = self.error_code()
        suffix = f" code={code}" if code else ""
        return f"{self.method} {self.path} -> HTTP {self.status}{suffix}"


class QAClient:
    """One browser-like cookie jar and rotating CSRF token."""

    def __init__(self, base_url: str, *, timeout: float = 20.0, label: str = "client") -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError(f"QA API client must target local HTTP backend, got {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.label = label
        self.csrf = ""
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
            _NoRedirect(),
        )

    def _apply_csrf_from(self, payload: Any) -> None:
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            token = payload["data"].get("csrf")
            if isinstance(token, str) and token:
                self.csrf = token

    def request(
        self,
        method: str,
        path: str,
        body: Any = _OMIT,
        *,
        csrf: Any = _USE_SESSION_TOKEN,
        headers: dict[str, str] | None = None,
        apply_csrf: bool = True,
    ) -> ApiResponse:
        if not path.startswith("/"):
            raise ValueError(f"API path must start with '/': {path!r}")
        method = method.upper()
        url = self.base_url + path
        data = _OMIT
        req_headers = {
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": "OpenShop-QA/1.0",
        }
        if headers:
            req_headers.update({str(k): str(v) for k, v in headers.items()})
        if body is not _OMIT:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        if method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = self.csrf if csrf is _USE_SESSION_TOKEN else csrf
            if supplied:
                req_headers["X-OpenShop-CSRF"] = str(supplied)
        req = urllib.request.Request(url, data=None if data is _OMIT else data, method=method, headers=req_headers)
        import time

        started = time.monotonic()
        try:
            resp = self.opener.open(req, timeout=self.timeout)
            try:
                raw = resp.read(5 * 1024 * 1024)
                status = int(resp.status)
                resp_headers = resp.headers
            finally:
                resp.close()
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(5 * 1024 * 1024)
            finally:
                exc.close()
            status = int(exc.code)
            resp_headers = exc.headers
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise TransportFailure(
                f"{method} {path} transport failure after one attempt: {type(exc).__name__}: {exc}"
            ) from exc
        elapsed_ms = (time.monotonic() - started) * 1000.0
        text = raw.decode("utf-8", errors="replace")
        payload: Any = None
        content_type = ""
        try:
            content_type = (resp_headers.get("Content-Type") or "").lower()
        except AttributeError:
            pass
        if "json" in content_type or text.lstrip().startswith(("{", "[")):
            try:
                payload = json.loads(text)
            except (ValueError, TypeError):
                payload = None
        if apply_csrf:
            self._apply_csrf_from(payload)
        return ApiResponse(method, path, status, resp_headers, text, payload, elapsed_ms)

    def get(self, path: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
        return self.request("GET", path, headers=headers)

    def post(self, path: str, body: Any, *, csrf: Any = _USE_SESSION_TOKEN) -> ApiResponse:
        return self.request("POST", path, {} if body is _OMIT else body, csrf=csrf)

    def post_without_body(self, path: str, *, csrf: Any = _USE_SESSION_TOKEN) -> ApiResponse:
        return self.request("POST", path, _OMIT, csrf=csrf)

    def patch(self, path: str, body: Any) -> ApiResponse:
        return self.request("PATCH", path, body)

    def delete(self, path: str, body: Any = _OMIT) -> ApiResponse:
        return self.request("DELETE", path, body)

    def begin_session(self) -> ApiResponse:
        return self.get("/api/session")

    def cookie_value(self, name: str) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None

    def cookie_names(self) -> list[str]:
        return sorted({cookie.name for cookie in self.jar})

    def clear_state(self) -> None:
        self.jar.clear()
        self.csrf = ""
