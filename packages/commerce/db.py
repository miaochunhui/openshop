"""SQLite foundation for OpenShop.

All persistent state is local SQLite. Connections use explicit transactions so
stock and coupon updates can be reasoned about without depending on Python's
legacy implicit transaction mode.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "1"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    value = dt or utc_now()
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_days_ago(days: float, base: datetime | None = None) -> str:
    return iso((base or utc_now()) - timedelta(days=days))


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def new_id_factory(prefix: str) -> str:
    raw = os.urandom(8).hex()
    return f"{prefix}{raw}"


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit BEGIN IMMEDIATE transaction with loud rollback."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def all_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params).fetchall())


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def catalog_fingerprint(catalog_bytes: bytes) -> str:
    return hashlib.sha256(SCHEMA_VERSION.encode() + b"\0" + catalog_bytes).hexdigest()


def default_catalog_path() -> Path:
    return ROOT / "data" / "catalog.json"


def init_database(data_dir: str | Path, catalog_path: str | Path | None = None) -> Path:
    """Create directories + database, apply schema and seed when needed.

    Raises SeedMismatchError if the data directory already contains a different
    seed. Never destroys user data implicitly; use a new data directory or the
    explicit reset tool for a demo database.
    """
    from packages.commerce.seed import SeedMismatchError, seed_catalog

    target = Path(data_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    db_path = target / "openshop.sqlite3"
    catalog = Path(catalog_path).expanduser().resolve() if catalog_path else default_catalog_path()
    if not catalog.is_file():
        raise FileNotFoundError(f"catalog not found: {catalog}")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    import fcntl

    lock_path = target / ".seed.lock"
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        conn = connect(db_path)
        try:
            conn.executescript(schema)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                (SCHEMA_VERSION,),
            )
            seed_catalog(conn, catalog)
        finally:
            conn.close()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return db_path


class SeedMismatchError(RuntimeError):
    pass


def _now() -> str:
    return iso()
