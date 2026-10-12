"""Tiny HTTP API framework shared by all OpenShop route modules."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs

MAX_BODY_BYTES = 256 * 1024
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: Mapping[str, str] | None = None):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message
        self.fields = dict(fields or {})

    def payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fields:
            error["fields"] = self.fields
        return error


@dataclass
class Response:
    data: Any = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)
    raw: bytes | str | None = None
    content_type: str | None = None


def _cookie_header(name: str, value: str, *, max_age: int | None, secure: bool, http_only: bool = True, same_site: str = "Lax", path: str = "/") -> str:
    cookie = SimpleCookie()
    cookie[name] = value
    morsel = cookie[name]
    morsel["path"] = path
    morsel["samesite"] = same_site
    if http_only:
        morsel["httponly"] = True
    if secure:
        morsel["secure"] = True
    if max_age is not None:
        morsel["max-age"] = str(max_age)
    return morsel.OutputString()


class RequestContext:
    def __init__(
        self,
        *,
        method: str,
        path: str,
        raw_query: str,
        headers: Mapping[str, str],
        body: bytes,
        db: sqlite3.Connection,
        session: sqlite3.Row | None,
        session_token: str | None,
        request_id: str,
        server_time: str,
        client_ip: str,
        is_secure: bool,
        params: dict[str, str] | None = None,
    ):
        self.method = method.upper()
        self.path = path
        self.raw_query = raw_query
        self.query = parse_qs(raw_query, keep_blank_values=True, max_num_fields=100)
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body
        self.db = db
        self.session = session
        self.session_token = session_token
        self.request_id = request_id
        self.server_time = server_time
        self.client_ip = client_ip
        self.is_secure = is_secure
        self.params = params or {}
        self._json_cache: Any = ...
        self._cookie_headers: list[str] = []
        self._headers: dict[str, str] = {}
        self._user_row: sqlite3.Row | None | object = ...
        self._shop_row: sqlite3.Row | None | object = ...

    # ---------- query/body ----------
    def q(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def q_bool(self, name: str, default: bool = False) -> bool:
        raw = self.q(name)
        if raw is None or raw == "":
            return default
        return raw.lower() in {"1", "true", "yes", "on"}

    def q_int(self, name: str, default: int | None = None, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
        raw = self.q(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "VALIDATION_ERROR", f"参数 {name} 必须是整数", {name: "必须是整数"}) from exc
        if minimum is not None and value < minimum:
            raise ApiError(400, "VALIDATION_ERROR", f"参数 {name} 不能小于 {minimum}", {name: f"不能小于 {minimum}"})
        if maximum is not None and value > maximum:
            raise ApiError(400, "VALIDATION_ERROR", f"参数 {name} 不能大于 {maximum}", {name: f"不能大于 {maximum}"})
        return value

    def pagination(self, *, default_size: int = 12, max_size: int = 24) -> tuple[int, int]:
        page = self.q_int("page", 1, minimum=1, maximum=100000) or 1
        size = self.q_int("page_size", default_size, minimum=1, maximum=max_size) or default_size
        return page, size

    def json(self) -> Any:
        if self._json_cache is not ...:
            return self._json_cache
        if not self.body:
            raise ApiError(400, "VALIDATION_ERROR", "请求体必须是非空 JSON")
        ctype = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(415, "VALIDATION_ERROR", "Content-Type 必须是 application/json")
        try:
            self._json_cache = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "VALIDATION_ERROR", "JSON 格式无效") from exc
        return self._json_cache

    def json_object(self) -> dict[str, Any]:
        value = self.json()
        if not isinstance(value, dict):
            raise ApiError(400, "VALIDATION_ERROR", "请求体必须是 JSON 对象")
        return value

    # ---------- session/auth ----------
    @property
    def user_row(self) -> sqlite3.Row | None:
        if self._user_row is ...:
            if not self.session or self.session["user_id"] is None:
                self._user_row = None
            else:
                from packages.commerce.models import get_user_row

                self._user_row = get_user_row(self.db, int(self.session["user_id"]))
        return self._user_row  # type: ignore[return-value]

    @property
    def user(self) -> sqlite3.Row | None:
        return self.user_row

    def require_session(self) -> sqlite3.Row:
        if self.session is None:
            raise ApiError(401, "AUTH_REQUIRED", "请刷新页面建立会话")
        return self.session

    def require_user(self) -> sqlite3.Row:
        row = self.user
        if row is None:
            raise ApiError(401, "AUTH_REQUIRED", "请先登录后再继续")
        return row

    def require_seller(self) -> sqlite3.Row:
        row = self.require_user()
        if row["role"] != "seller" or row["shop_id"] is None:
            raise ApiError(403, "FORBIDDEN", "只有商家账号可以访问此功能")
        return row

    @property
    def owner_key(self) -> str:
        if self.session is None:
            return "anonymous"
        if self.session["user_id"] is not None:
            return f"u:{int(self.session['user_id'])}"
        return f"g:{self.session['id']}"

    # ---------- response side effects ----------
    def set_session_cookie(self, token: str, *, max_age: int = 30 * 24 * 3600) -> None:
        self._cookie_headers.append(
            _cookie_header("openshop_session", token, max_age=max_age, secure=self.is_secure)
        )

    def clear_session_cookie(self) -> None:
        self._cookie_headers.append(
            _cookie_header("openshop_session", "", max_age=0, secure=self.is_secure)
        )

    def add_cookie(self, name: str, value: str, *, max_age: int | None = None, http_only: bool = True) -> None:
        self._cookie_headers.append(
            _cookie_header(name, value, max_age=max_age, secure=self.is_secure, http_only=http_only)
        )

    def set_header(self, name: str, value: str) -> None:
        self._headers[name] = value

    def finish(self, data: Any, status: int = 200) -> Response:
        return Response(data=data, status=status, headers=dict(self._headers), cookies=list(self._cookie_headers))

    @property
    def response_cookies(self) -> list[str]:
        return list(self._cookie_headers)


Handler = Callable[..., Any]


@dataclass
class Route:
    method: str
    pattern: str
    handler: Handler
    auth: str
    regex: re.Pattern[str]
    names: tuple[str, ...]


class Router:
    """Explicit small router. Patterns use {name} path segments."""

    def __init__(self) -> None:
        self.routes: list[Route] = []
        self._path_signatures: dict[str, set[str]] = {}

    def add(self, method: str, pattern: str, handler: Handler | None = None, *, auth: str = "public") -> Handler:
        """Register directly or as a decorator.

        Both forms are supported:
          router.add("GET", "/api/x", handler)
          @router.add("GET", "/api/x", auth="user")
          def x(ctx): ...
        """
        def install(fn: Handler) -> Handler:
            method_upper = method.upper()
            auth_lower = auth.lower()
            if auth_lower not in {"public", "session", "user", "seller"}:
                raise ValueError(f"invalid route auth level: {auth_lower}")
            names: list[str] = []
            pieces: list[str] = []
            for segment in pattern.strip("/").split("/"):
                if segment.startswith("{") and segment.endswith("}"):
                    name = segment[1:-1]
                    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name):
                        raise ValueError(f"invalid path parameter {name}")
                    names.append(name)
                    pieces.append(r"(?P<" + name + r">[^/]+)")
                else:
                    pieces.append(re.escape(segment))
            body = "/".join(pieces)
            regex = re.compile("^/" + body + "/?$") if body else re.compile("^/?$")
            self.routes.append(Route(method_upper, pattern, fn, auth_lower, regex, tuple(names)))
            signature = regex.pattern
            self._path_signatures.setdefault(signature, set()).add(method_upper)
            return fn

        return install(handler) if handler is not None else install

    def dispatch(self, ctx: RequestContext) -> Response | Any:
        path_matches: list[tuple[Route, dict[str, str]]] = []
        for route in self.routes:
            match = route.regex.match(ctx.path)
            if match:
                path_matches.append((route, match.groupdict()))
        if not path_matches:
            raise ApiError(404, "NOT_FOUND", "接口不存在")
        allowed = sorted({route.method for route, _ in path_matches})
        for route, params in path_matches:
            if route.method != ctx.method:
                continue
            if route.auth == "session":
                ctx.require_session()
            elif route.auth == "user":
                ctx.require_user()
            elif route.auth == "seller":
                ctx.require_seller()
            ctx.params = params
            result = route.handler(ctx, **params)
            if isinstance(result, Response):
                return result
            return ctx.finish(result)
        raise ApiError(405, "METHOD_NOT_ALLOWED", f"此接口只接受 {', '.join(allowed)}")

    def has_route_prefix(self, prefix: str) -> bool:
        return any(route.pattern.startswith(prefix) for route in self.routes)


# ---------- validation helpers ----------
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
_HANDLE_RE = re.compile(r"^[a-z0-9_]{3,30}$")


def validate_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", "请输入有效邮箱", {"email": "邮箱格式不正确"})
    value = value.strip().lower()
    if len(value) > 120 or not _EMAIL_RE.fullmatch(value):
        raise ApiError(400, "VALIDATION_ERROR", "请输入有效邮箱", {"email": "邮箱格式不正确"})
    return value


def validate_password(value: Any) -> str:
    if not isinstance(value, str) or not (8 <= len(value) <= 72):
        raise ApiError(400, "VALIDATION_ERROR", "密码长度需为 8-72 位", {"password": "密码长度需为 8-72 位"})
    if value.strip() != value:
        raise ApiError(400, "VALIDATION_ERROR", "密码首尾不能有空格", {"password": "密码首尾不能有空格"})
    return value


def validate_text(value: Any, field_name: str, *, min_len: int = 1, max_len: int = 200, label: str | None = None) -> str:
    label = label or field_name
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", f"{label}格式不正确", {field_name: "必须是文字"})
    value = value.strip()
    if not (min_len <= len(value) <= max_len):
        if min_len == max_len:
            detail = f"必须为 {min_len} 个字符"
        else:
            detail = f"长度需为 {min_len}-{max_len} 个字符"
        raise ApiError(400, "VALIDATION_ERROR", f"{label}{detail}", {field_name: detail})
    return value


def validate_int(value: Any, field_name: str, *, minimum: int = 0, maximum: int = 2_000_000_000, label: str | None = None) -> int:
    label = label or field_name
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, "VALIDATION_ERROR", f"{label}必须是整数", {field_name: "必须是整数"})
    if not (minimum <= value <= maximum):
        raise ApiError(400, "VALIDATION_ERROR", f"{label}超出允许范围", {field_name: f"范围 {minimum}-{maximum}"})
    return value


def validate_handle(value: Any) -> str:
    if not isinstance(value, str) or not _HANDLE_RE.fullmatch(value.strip().lower()):
        raise ApiError(400, "VALIDATION_ERROR", "handle 需为 3-30 位小写字母、数字或下划线", {"handle": "格式不正确"})
    return value.strip().lower()
