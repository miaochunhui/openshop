"""Password, session and one-click demo auth primitives."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from datetime import timedelta
from typing import Any

from packages.commerce import db as dbutil
from packages.commerce import models
from packages.commerce.web import ApiError

PBKDF2_ITERATIONS = 210_000
SESSION_DAYS = 30
SESSION_COOKIE = "openshop_session"
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, list[float]] = {}
_DUMMY_SALT = secrets.token_hex(16)
_DUMMY_HASH = ""


def _password_material(password: str, salt_hex: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return base64.b64encode(digest).decode("ascii")


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    return _password_material(password, salt), salt


def verify_password(password: str, password_hash: str | None, salt: str | None) -> bool:
    if not password_hash or not salt:
        return False
    try:
        expected = _password_material(password, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, password_hash)


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = _password_material("dummy-value-not-a-real-password", _DUMMY_SALT)
    material = _password_material(password, _DUMMY_SALT)
    hmac.compare_digest(material, _DUMMY_HASH)


def token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int | None) -> tuple[str, sqlite3.Row, str]:
    raw_token = secrets.token_urlsafe(32)
    session_id = secrets.token_urlsafe(18)
    csrf = secrets.token_urlsafe(32)
    now = dbutil.utc_now()
    expires = now + timedelta(days=SESSION_DAYS)
    conn.execute(
        """INSERT INTO sessions(id,token_hash,user_id,csrf,created_at,expires_at,last_seen_at)
           VALUES(?,?,?,?,?,?,?)""",
        (session_id, token_hash(raw_token), user_id, csrf, dbutil.iso(now), dbutil.iso(expires), dbutil.iso(now)),
    )
    row = dbutil.one(conn, "SELECT * FROM sessions WHERE id=?", (session_id,))
    assert row is not None
    return raw_token, row, csrf


def load_session(conn: sqlite3.Connection, raw_token: str | None) -> sqlite3.Row | None:
    if not raw_token or len(raw_token) > 200:
        return None
    row = dbutil.one(conn, "SELECT * FROM sessions WHERE token_hash=?", (token_hash(raw_token),))
    if row is None:
        return None
    if row["expires_at"] < dbutil.iso():
        conn.execute("DELETE FROM sessions WHERE id=?", (row["id"],))
        return None
    conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (dbutil.iso(), row["id"]))
    return dbutil.one(conn, "SELECT * FROM sessions WHERE id=?", (row["id"],))


def revoke_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))


def rotate_csrf(conn: sqlite3.Connection, session_id: str) -> str:
    csrf = secrets.token_urlsafe(32)
    conn.execute("UPDATE sessions SET csrf=? WHERE id=?", (csrf, session_id))
    return csrf


def owner_key(session: sqlite3.Row) -> str:
    if session["user_id"] is not None:
        return f"u:{int(session['user_id'])}"
    return f"g:{session['id']}"


def guest_key(session: sqlite3.Row) -> str:
    return f"g:{session['id']}"


def session_payload(conn: sqlite3.Connection, session: sqlite3.Row, *, user_id: int | None = None, csrf: str | None = None) -> dict[str, Any]:
    effective_user_id = int(user_id) if user_id is not None else (int(session["user_id"]) if session["user_id"] is not None else None)
    user = models.user_payload(conn, models.get_user_row(conn, effective_user_id)) if effective_user_id else None
    key = f"u:{effective_user_id}" if effective_user_id else guest_key(session)
    return {
        "user": user,
        "csrf": csrf or session["csrf"],
        "cart_count": models.cart_count(conn, key),
        "favorite_count": models.favorite_count(conn, key),
        "is_guest": effective_user_id is None,
    }


def ensure_auto_coupons(conn: sqlite3.Connection, user_id: int) -> None:
    models.grant_auto_coupons(conn, int(user_id))


def unique_handle(conn: sqlite3.Connection, email: str) -> str:
    base = models.username_from_email(email)
    candidate = base
    for _ in range(30):
        existing = dbutil.one(conn, "SELECT 1 FROM users WHERE handle=?", (candidate,))
        if not existing:
            return candidate
        candidate = (base[:24] + "_" + secrets.token_hex(2))[:30]
    return "user_" + secrets.token_hex(6)


def update_session_user(conn: sqlite3.Connection, session: sqlite3.Row, user_id: int) -> tuple[str | None, sqlite3.Row]:
    """Attach the authenticated user and merge guest cart/favorites into their state.

    Returns (new_guest_token, refreshed_session). If session was already attached
    to a different user, caller must rotate a fresh session instead.
    """
    current = session["user_id"]
    if current is not None and int(current) != int(user_id):
        raise ValueError("cannot switch users on an authenticated session")
    gkey = guest_key(session)
    ukey = f"u:{int(user_id)}"
    models.merge_owner_state(conn, gkey, ukey)
    conn.execute("UPDATE sessions SET user_id=? WHERE id=?", (int(user_id), session["id"]))
    ensure_auto_coupons(conn, int(user_id))
    refreshed = dbutil.one(conn, "SELECT * FROM sessions WHERE id=?", (session["id"],))
    assert refreshed is not None
    return None, refreshed


def login_rate_limit(key: str, *, max_attempts: int = 8, window_seconds: int = 300) -> None:
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window_seconds]
        if len(bucket) >= max_attempts:
            _RATE_BUCKETS[key] = bucket
            raise ApiError(429, "RATE_LIMITED", "尝试次数过多，请稍后再试")
        bucket.append(now)
        _RATE_BUCKETS[key] = bucket


def reset_rate_limit(key: str) -> None:
    with _RATE_LOCK:
        _RATE_BUCKETS.pop(key, None)
