from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "retail_locations.db"


def get_db_path() -> str:
    return os.environ.get("RETAIL_DB_PATH", str(DEFAULT_DB_PATH))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _column_names(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()}


def init_db() -> None:
    db_path = Path(get_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
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
            conn.execute("ALTER TABLE items ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_failure_count ON items(failure_count)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_archived ON items(is_archived)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_last_updated ON items(last_updated)")
        conn.commit()
