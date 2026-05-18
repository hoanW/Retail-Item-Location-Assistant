from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "retail_locations.db"
DatabaseBackend = Literal["sqlite", "postgres"]


def get_database_url() -> str | None:
    """Return production database URL when configured.

    Local development intentionally defaults to SQLite. Production deployments should
    set DATABASE_URL (or POSTGRES_URL) to a managed PostgreSQL database so data
    persists across redeployments.
    """

    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")


def get_backend() -> DatabaseBackend:
    url = get_database_url()
    if url and (url.startswith("postgresql://") or url.startswith("postgres://")):
        return "postgres"
    return "sqlite"


def get_db_path() -> str:
    return os.environ.get("RETAIL_DB_PATH", str(DEFAULT_DB_PATH))


def param_sql(sql: str) -> str:
    """Convert SQLite-style placeholders to the active driver's paramstyle."""

    if get_backend() == "postgres":
        return sql.replace("?", "%s")
    return sql


def true_value() -> Any:
    return True if get_backend() == "postgres" else 1


def false_value() -> Any:
    return False if get_backend() == "postgres" else 0


def get_connection():
    if get_backend() == "postgres":
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - only hit in misconfigured deployment
            raise RuntimeError(
                "PostgreSQL deployment requires the 'psycopg[binary]' package. "
                "Install requirements.txt and set DATABASE_URL."
            ) from exc

        url = get_database_url()
        if not url:  # pragma: no cover - guarded by get_backend
            raise RuntimeError("DATABASE_URL or POSTGRES_URL must be set for PostgreSQL")
        return psycopg.connect(url, row_factory=dict_row)

    db_path = Path(get_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _column_names(conn) -> set[str]:
    if get_backend() == "postgres":
        rows = conn.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_name = 'items'
            """
        ).fetchall()
    else:
        rows = conn.execute("PRAGMA table_info(items)").fetchall()
    return {row["name"] for row in rows}


def init_db() -> None:
    with get_connection() as conn:
        if get_backend() == "postgres":
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    article_number TEXT NOT NULL UNIQUE,
                    current_location TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'valid',
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_updated TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    archived_at TEXT,
                    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TEXT NOT NULL
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_number TEXT NOT NULL UNIQUE,
                    current_location TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'valid',
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_updated TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    archived_at TEXT,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )

        # Lightweight migrations for databases created by earlier MVP versions.
        columns = _column_names(conn)
        if "last_seen_at" not in columns:
            conn.execute("ALTER TABLE items ADD COLUMN last_seen_at TEXT")
            conn.execute("UPDATE items SET last_seen_at = last_updated WHERE last_seen_at IS NULL")
        if "archived_at" not in columns:
            conn.execute("ALTER TABLE items ADD COLUMN archived_at TEXT")
        if "is_archived" not in columns:
            if get_backend() == "postgres":
                conn.execute("ALTER TABLE items ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE")
            else:
                conn.execute("ALTER TABLE items ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_failure_count ON items(failure_count)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_archived ON items(is_archived)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_last_updated ON items(last_updated)")
        conn.commit()
