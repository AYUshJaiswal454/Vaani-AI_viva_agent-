"""Small database layer (SQLite by default, PostgreSQL for multi-node deployments).

SQLite (WAL mode) is the default and what the test-suite runs on. PostgreSQL is supported
through psycopg 3 for multi-node deployments: the same SQL is used, with `?` placeholders
translated to `%s` and AUTOINCREMENT translated to identity columns.

Columns listed in JSON_COLUMNS are stored as TEXT and transparently encoded/decoded.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import get_settings

ADDED_COLUMNS: list[tuple[str, str, str]] = []   # (table, column, ddl) for columns added after release
JSON_COLUMNS = {
    "settings", "topics", "roster", "rubric", "key_points", "follow_ups", "hints", "misconceptions", "tags", "ai_meta",
    "state", "consent", "plan", "flags", "latency", "client", "meta", "dims", "feedback", "runs", "review_reasons",
    "payload", "result", "data", "before", "after", "detail", "stats", "validation", "phase_plan", "aliases",
}

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().replace(microsecond=0).isoformat()


def iso_in(**delta) -> str:
    return (utcnow() + timedelta(**delta)).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _encode(key: str, value: Any) -> Any:
    if key in JSON_COLUMNS and not isinstance(value, str) and value is not None:
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, bool):
        return int(value)
    return value


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    for key, value in row.items():
        if key in JSON_COLUMNS and isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except (ValueError, TypeError):
                pass
    return row


class Database:
    def __init__(self, url: str):
        self.url = url
        self.is_postgres = url.startswith(("postgres://", "postgresql://"))
        self._local = threading.local()
        self._lock = threading.Lock()
        if not self.is_postgres:
            path = url.replace("sqlite:///", "", 1)
            self.path = path
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ connections
    def _connect(self):
        if self.is_postgres:
            import psycopg  # type: ignore
            from psycopg.rows import dict_row  # type: ignore

            conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
            return conn
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @property
    def conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------ helpers
    def _sql(self, sql: str) -> str:
        if self.is_postgres:
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        cur = self.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        return _decode_row(dict(row))

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cur = self.execute(sql, params)
        return [_decode_row(dict(r)) for r in cur.fetchall()]

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        cur = self.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        return list(dict(row).values())[0] if not isinstance(row, tuple) else row[0]

    def insert(self, table: str, data: dict[str, Any]) -> int:
        keys = list(data.keys())
        values = [_encode(k, data[k]) for k in keys]
        placeholders = ", ".join("?" for _ in keys)
        sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
        if self.is_postgres:
            cur = self.execute(sql + " RETURNING id", values)
            return int(cur.fetchone()["id"])
        cur = self.execute(sql, values)
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, data: dict[str, Any], where_extra: str = "", extra_params: Iterable[Any] = ()) -> int:
        if not data:
            return 0
        keys = list(data.keys())
        assignments = ", ".join(f"{k} = ?" for k in keys)
        values = [_encode(k, data[k]) for k in keys]
        sql = f"UPDATE {table} SET {assignments} WHERE id = ?{(' AND ' + where_extra) if where_extra else ''}"
        cur = self.execute(sql, [*values, row_id, *extra_params])
        return cur.rowcount

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        if self.is_postgres:
            with self.conn.transaction():
                yield self
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ schema
    def init_schema(self) -> None:
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        if self.is_postgres:
            schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
            schema = schema.replace(" REAL", " DOUBLE PRECISION")
        # Drop comments first (they may contain ';'), then split into statements.
        cleaned = "\n".join(ln.split("--", 1)[0] for ln in schema.splitlines())
        statements = [s.strip() for s in cleaned.split(";") if s.strip()]
        with self._lock:
            # Tables first, then columns added by later versions, then indexes (which may use those columns).
            for stmt in statements:
                if not stmt.upper().startswith("CREATE INDEX"):
                    self.execute(stmt)
            self._add_missing_columns()
            for stmt in statements:
                if stmt.upper().startswith("CREATE INDEX"):
                    self.execute(stmt)
            existing = self.scalar("SELECT MAX(version) FROM schema_version")
            if not existing:
                self.insert_ignore("schema_version", {"version": SCHEMA_VERSION, "applied_at": now_iso()})

    def _add_missing_columns(self) -> None:
        """Forward-only migrations for databases created by an older version (columns added later)."""
        for table, column, ddl in ADDED_COLUMNS:
            if self.is_postgres:
                self.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
            elif column not in {r["name"] for r in self.fetchall(f"PRAGMA table_info({table})")}:
                self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def insert_ignore(self, table: str, data: dict[str, Any]) -> None:
        keys = list(data.keys())
        values = [_encode(k, data[k]) for k in keys]
        placeholders = ", ".join("?" for _ in keys)
        self.execute(f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING", values)


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database(get_settings().database_url)
                _db.init_schema()
    return _db


def reset_db() -> Database:
    """Tests: drop the cached handle so a new DATABASE_URL takes effect."""
    global _db
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
    _db = None
    return get_db()


def audit(actor: dict | None, action: str, entity: str = "", entity_id: Any = "", before: Any = None, after: Any = None,
          data: dict | None = None) -> None:
    """Append-only audit trail (overrides, approvals, audio playback, exports, deletions)."""
    get_db().insert("audit_log", {
        "actor_id": (actor or {}).get("id"), "actor_name": (actor or {}).get("name") or "system", "action": action,
        "entity": entity, "entity_id": str(entity_id), "before": before, "after": after, "data": data or {},
        "created_at": now_iso(),
    })
