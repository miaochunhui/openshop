"""Session and authentication API routes. Lead-owned.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from packages.commerce import auth, db as dbutil, models
from packages.commerce.web import ApiError, RequestContext, Router, validate_email, validate_password, validate_text


def _cookie_raw(ctx: RequestContext) -> str | None:
    return ctx.session_token


def _set_new_session(ctx: RequestContext, raw: str) -> None:
    ctx.set_session_cookie(raw)


def register(router: Router) -> None:
    @router.add("GET", "/api/session", auth="public")
    def get_session(ctx: RequestContext) -> dict[str, Any]:
        session = ctx.session
        if session is None:
            with dbutil.transaction(ctx.db):
                raw, row, _csrf = auth.create_session(ctx.db, None)
            _set_new_session(ctx, raw)
            session = row
        return auth.session_payload(ctx.db, session)

    @router.add("POST", "/api/auth/register", auth="public")
    def register_user(ctx: RequestContext) -> dict[str, Any]:
        session = ctx.require_session()
        if session["user_id"] is not None:
            raise ApiError(409, "ALREADY_SIGNED_IN", "已登录状态下不能注册新账号，请先退出")
        auth.login_rate_limit(f"register:{ctx.client_ip}", max_attempts=5, window_seconds=600)
        body = ctx.json_object()
        display_name = validate_text(body.get("display_name"), "display_name", min_len=2, max_len=24, label="昵称")
        email = validate_email(body.get("email"))
        password = validate_password(body.get("password"))
        password_hash, salt = auth.hash_password(password)
        handle = auth.unique_handle(ctx.db, email)
        try:
            with dbutil.transaction(ctx.db):
                existing = dbutil.one(ctx.db, "SELECT 1 FROM users WHERE email=?", (email,))
                if existing:
                    raise ApiError(409, "EMAIL_TAKEN", "这个邮箱已注册，请直接登录", {"email": "邮箱已注册"})
                ctx.db.execute(
                    """INSERT INTO users(handle,display_name,email,role,password_hash,password_salt,avatar,member_since,created_at)
                       VALUES(?,?,?,'buyer',?,?,?,?,?)""",
                    (handle, display_name, email, password_hash, salt, "/web/assets/brand/avatar-lin.svg",
                     dbutil.utc_now().date().isoformat(), dbutil.iso()),
                )
                user_id = int(ctx.db.execute("SELECT last_insert_rowid()").fetchone()[0])
                new_token, refreshed = auth.update_session_user(ctx.db, session, user_id)
                csrf = auth.rotate_csrf(ctx.db, session["id"])
            if new_token:
                _set_new_session(ctx, new_token)
            auth.reset_rate_limit(f"register:{ctx.client_ip}")
            payload = auth.session_payload(ctx.db, refreshed, user_id=user_id, csrf=csrf)
            return payload
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "EMAIL_TAKEN", "这个邮箱已注册，请直接登录", {"email": "邮箱已注册"}) from exc

    @router.add("POST", "/api/auth/login", auth="public")
    def login(ctx: RequestContext) -> dict[str, Any]:
        session = ctx.require_session()
        body = ctx.json_object()
        identifier = validate_text(body.get("identifier"), "identifier", min_len=2, max_len=120, label="邮箱或账号")
        password = body.get("password")
        if not isinstance(password, str) or not password:
            raise ApiError(401, "INVALID_CREDENTIALS", "邮箱或密码不正确")
        auth.login_rate_limit(f"login:{ctx.client_ip}:{identifier.lower()[:80]}")
        user = dbutil.one(
            ctx.db,
            "SELECT * FROM users WHERE (LOWER(email)=? OR handle=?) AND password_hash IS NOT NULL",
            (identifier.strip().lower(), identifier.strip().lower()),
        )
        if user is None:
            auth.dummy_verify(password)
            raise ApiError(401, "INVALID_CREDENTIALS", "邮箱或密码不正确")
        if not auth.verify_password(password, user["password_hash"], user["password_salt"]):
            raise ApiError(401, "INVALID_CREDENTIALS", "邮箱或密码不正确")
        if session["user_id"] is not None and int(session["user_id"]) != int(user["id"]):
            with dbutil.transaction(ctx.db):
                auth.revoke_session(ctx.db, session["id"])
                raw, new_session, _ = auth.create_session(ctx.db, int(user["id"]))
                new_csrf = auth.rotate_csrf(ctx.db, new_session["id"])
                new_session = dbutil.one(ctx.db, "SELECT * FROM sessions WHERE id=?", (new_session["id"],))
            _set_new_session(ctx, raw)
            assert new_session is not None
            auth.reset_rate_limit(f"login:{ctx.client_ip}:{identifier.lower()[:80]}")
            return auth.session_payload(ctx.db, new_session, csrf=new_csrf)
        with dbutil.transaction(ctx.db):
            _, refreshed = auth.update_session_user(ctx.db, session, int(user["id"]))
            csrf = auth.rotate_csrf(ctx.db, session["id"])
        auth.reset_rate_limit(f"login:{ctx.client_ip}:{identifier.lower()[:80]}")
        return auth.session_payload(ctx.db, refreshed, user_id=int(user["id"]), csrf=csrf)

    @router.add("POST", "/api/auth/demo", auth="public")
    def demo(ctx: RequestContext) -> dict[str, Any]:
        session = ctx.require_session()
        body = ctx.json_object()
        role = body.get("role")
        if role not in {"buyer", "seller"}:
            raise ApiError(400, "VALIDATION_ERROR", "请选择买家或商家演示账号")
        handle = "demo_seller" if role == "seller" else "demo_buyer"
        user = dbutil.one(ctx.db, "SELECT * FROM users WHERE handle=?", (handle,))
        if user is None:
            raise ApiError(503, "DEMO_UNAVAILABLE", "演示账号尚未初始化")
        auth.login_rate_limit(f"demo:{ctx.client_ip}", max_attempts=40, window_seconds=300)
        if session["user_id"] is not None and int(session["user_id"]) != int(user["id"]):
            with dbutil.transaction(ctx.db):
                auth.revoke_session(ctx.db, session["id"])
                raw, fresh, _ = auth.create_session(ctx.db, int(user["id"]))
                csrf = auth.rotate_csrf(ctx.db, fresh["id"])
                fresh = dbutil.one(ctx.db, "SELECT * FROM sessions WHERE id=?", (fresh["id"],))
            _set_new_session(ctx, raw)
            assert fresh is not None
            return auth.session_payload(ctx.db, fresh, csrf=csrf)
        with dbutil.transaction(ctx.db):
            _, refreshed = auth.update_session_user(ctx.db, session, int(user["id"]))
            csrf = auth.rotate_csrf(ctx.db, session["id"])
        auth.reset_rate_limit(f"demo:{ctx.client_ip}")
        return auth.session_payload(ctx.db, refreshed, user_id=int(user["id"]), csrf=csrf)

    @router.add("POST", "/api/auth/logout", auth="public")
    def logout(ctx: RequestContext) -> dict[str, Any]:
        session = ctx.require_session()
        with dbutil.transaction(ctx.db):
            auth.revoke_session(ctx.db, session["id"])
            raw, fresh, _ = auth.create_session(ctx.db, None)
        _set_new_session(ctx, raw)
        return auth.session_payload(ctx.db, fresh)
