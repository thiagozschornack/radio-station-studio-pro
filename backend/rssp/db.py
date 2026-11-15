"""Camada de acesso a dados.

Um único adaptador fino sobre DB-API com dois dialetos:
  * postgresql://...  → psycopg 3 (produção)
  * sqlite:///caminho → sqlite3 da biblioteca padrão (desenvolvimento e testes)

As consultas são escritas com o marcador `?`; para PostgreSQL ele é convertido para `%s`.
Conexões são mantidas por thread (os handlers síncronos da API rodam em threadpool).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def now_iso(delta_seconds: float = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s.rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, url: str):
        self.url = url
        self._local = threading.local()
        if url.startswith("sqlite:///"):
            self.dialect = "sqlite"
            self.path = url[len("sqlite:///"):]
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        elif url.startswith(("postgresql://", "postgres://")):
            self.dialect = "postgres"
        else:
            raise ValueError("RSSP_DATABASE_URL deve começar com sqlite:/// ou postgresql://")

    # ------------------------------------------------------------------ conexão
    def _connect(self):
        if self.dialect == "sqlite":
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            # LOWER nativo do SQLite só trata ASCII; o do PostgreSQL trata Unicode. Igualamos o comportamento.
            conn.create_function("LOWER", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
            return conn
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)

    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect()
            self._local.conn = c
            self._local.depth = 0
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def _sql(self, sql: str) -> str:
        if self.dialect == "postgres":
            return re.sub(r"\?", "%s", sql)
        return sql

    # ------------------------------------------------------------------ consultas
    def execute(self, sql: str, params: tuple | list = ()) -> int:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return cur.rowcount

    def fetchone(self, sql: str, params: tuple | list = ()) -> dict | None:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self, sql: str, params: tuple | list = ()) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def scalar(self, sql: str, params: tuple | list = ()) -> Any:
        row = self.fetchone(sql, params)
        return next(iter(row.values())) if row else None

    def insert(self, table: str, values: dict) -> dict:
        cols = ", ".join(values.keys())
        marks = ", ".join("?" for _ in values)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
        return values

    def update(self, table: str, values: dict, where: str, params: tuple | list) -> int:
        sets = ", ".join(f"{k} = ?" for k in values)
        return self.execute(f"UPDATE {table} SET {sets} WHERE {where}", list(values.values()) + list(params))

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator["Database"]:
        """Transação aninhável (apenas a mais externa faz BEGIN/COMMIT)."""
        conn = self.conn
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            if self.dialect == "sqlite":
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            else:
                conn.execute("BEGIN")
        self._local.depth = depth + 1
        try:
            yield self
        except BaseException:
            self._local.depth = depth
            if depth == 0:
                conn.execute("ROLLBACK")
            raise
        else:
            self._local.depth = depth
            if depth == 0:
                conn.execute("COMMIT")

    # ------------------------------------------------------------------ migrações
    def migrate(self) -> list[str]:
        if self.dialect == "postgres":
            # API, worker e playout iniciam juntos: só um aplica migrações por vez.
            self.execute("SELECT pg_advisory_lock(7272701)")
            try:
                return self._migrate()
            finally:
                self.execute("SELECT pg_advisory_unlock(7272701)")
        return self._migrate()

    def _migrate(self) -> list[str]:
        self.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r["version"] for r in self.fetchall("SELECT version FROM schema_migrations")}
        done = []
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = f.stem
            if version in applied:
                continue
            script = f.read_text(encoding="utf-8")
            with self.transaction(immediate=True):
                for stmt in _split_sql(script):
                    self.conn.execute(stmt) if self.dialect == "sqlite" else self.conn.cursor().execute(stmt)
                self.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, now_iso()))
            done.append(version)
        return done


def _split_sql(script: str) -> list[str]:
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        from .config import get_settings
        _db = Database(get_settings().database_url)
    return _db


def reset_db() -> None:
    global _db
    _db = None
