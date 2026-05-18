from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import false_value, get_backend, get_connection, init_db, param_sql, true_value

SUSPECT_THRESHOLD = 1
STALE_THRESHOLD = 3
AGING_AFTER_DAYS = int(os.environ.get("AGING_AFTER_DAYS", "30"))
ARCHIVE_AFTER_DAYS = int(os.environ.get("ARCHIVE_AFTER_DAYS", "60"))
ARCHIVE_CHECK_SECONDS = int(os.environ.get("ARCHIVE_CHECK_SECONDS", "86400"))


async def archive_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(ARCHIVE_CHECK_SECONDS)
        with get_connection() as conn:
            apply_archive_policy(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with get_connection() as conn:
        apply_archive_policy(conn)
    archive_task = asyncio.create_task(archive_maintenance_loop())
    try:
        yield
    finally:
        archive_task.cancel()
        try:
            await archive_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Retail Item Location Assistant",
    description="Approximate area memory assistant for returning clothing items to the store floor.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

LifecycleState = Literal["active", "aging", "archived"]
ItemStatus = Literal["valid", "suspect", "stale"]


class ItemOut(BaseModel):
    id: int
    article_number: str
    current_location: str
    status: ItemStatus
    failure_count: int
    lifecycle_state: LifecycleState
    last_updated: datetime
    last_seen_at: datetime
    archived_at: datetime | None = None
    is_archived: bool = False
    created_at: datetime


class AssignRequest(BaseModel):
    article_number: str = Field(..., min_length=1, max_length=80, examples=["123456"])
    location: str = Field(..., min_length=1, max_length=20, examples=["B2"])


class NotThereResponse(BaseModel):
    article_number: str
    current_location: str
    status: ItemStatus
    failure_count: int
    lifecycle_state: LifecycleState
    last_updated: datetime
    last_seen_at: datetime
    archived_at: datetime | None = None
    is_archived: bool = False


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_dt().isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def derive_status(failure_count: int) -> ItemStatus:
    if failure_count >= STALE_THRESHOLD:
        return "stale"
    if failure_count >= SUSPECT_THRESHOLD:
        return "suspect"
    return "valid"


def derive_lifecycle(row: dict[str, Any] | Any) -> LifecycleState:
    is_archived = bool(row["is_archived"])
    if is_archived:
        return "archived"
    last_updated = parse_dt(row["last_updated"])
    if last_updated and now_dt() - last_updated > timedelta(days=AGING_AFTER_DAYS):
        return "aging"
    return "active"


def apply_archive_policy(conn) -> int:
    """Soft-archive records that have not been updated within the retention window."""
    cutoff = now_dt() - timedelta(days=ARCHIVE_AFTER_DAYS)
    timestamp = now_iso()
    if get_backend() == "postgres":
        sql = """
        UPDATE items
        SET is_archived = %s, archived_at = COALESCE(archived_at, %s)
        WHERE is_archived = %s AND last_updated::timestamptz < %s::timestamptz
        """
        params = (true_value(), timestamp, false_value(), cutoff.isoformat())
    else:
        sql = """
        UPDATE items
        SET is_archived = ?, archived_at = COALESCE(archived_at, ?)
        WHERE is_archived = ? AND datetime(last_updated) < datetime(?)
        """
        params = (true_value(), timestamp, false_value(), cutoff.isoformat())
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount


def normalize_article(article_number: str) -> str:
    return article_number.strip()


def normalize_location(location: str) -> str:
    return location.strip().upper()


def row_to_item(row) -> ItemOut:
    data = dict(row)
    data["is_archived"] = bool(data.get("is_archived"))
    data["lifecycle_state"] = derive_lifecycle(data)
    return ItemOut(**data)


def db():
    conn = get_connection()
    try:
        apply_archive_policy(conn)
        yield conn
    finally:
        conn.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health(conn=Depends(db)) -> dict[str, int | str]:
    return {
        "status": "ok",
        "database_backend": get_backend(),
        "archive_after_days": ARCHIVE_AFTER_DAYS,
        "aging_after_days": AGING_AFTER_DAYS,
        "archive_check_seconds": ARCHIVE_CHECK_SECONDS,
    }


@app.post("/maintenance/archive")
def run_archive_maintenance(conn=Depends(db)) -> dict[str, int | str]:
    archived_count = apply_archive_policy(conn)
    return {"status": "ok", "archived_count": archived_count, "archive_after_days": ARCHIVE_AFTER_DAYS}


@app.get("/items/{article_number}", response_model=ItemOut)
def get_item(article_number: str, conn=Depends(db)) -> ItemOut:
    article = normalize_article(article_number)
    row = conn.execute(param_sql("SELECT * FROM items WHERE article_number = ?"), (article,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")

    conn.execute(param_sql("UPDATE items SET last_seen_at = ? WHERE article_number = ?"), (now_iso(), article))
    conn.commit()
    row = conn.execute(param_sql("SELECT * FROM items WHERE article_number = ?"), (article,)).fetchone()
    return row_to_item(row)


@app.post("/items/assign", response_model=ItemOut)
def assign_item(payload: AssignRequest, conn=Depends(db)) -> ItemOut:
    article = normalize_article(payload.article_number)
    location = normalize_location(payload.location)
    if not article:
        raise HTTPException(status_code=422, detail="article_number cannot be blank")
    if not location:
        raise HTTPException(status_code=422, detail="location cannot be blank")

    timestamp = now_iso()
    existing = conn.execute(param_sql("SELECT id FROM items WHERE article_number = ?"), (article,)).fetchone()
    if existing:
        conn.execute(
            param_sql(
                """
                UPDATE items
                SET current_location = ?, status = 'valid', failure_count = 0,
                    last_updated = ?, last_seen_at = ?, is_archived = ?, archived_at = NULL
                WHERE article_number = ?
                """
            ),
            (location, timestamp, timestamp, false_value(), article),
        )
    else:
        conn.execute(
            param_sql(
                """
                INSERT INTO items (
                    article_number, current_location, status, failure_count,
                    last_updated, last_seen_at, archived_at, is_archived, created_at
                )
                VALUES (?, ?, 'valid', 0, ?, ?, NULL, ?, ?)
                """
            ),
            (article, location, timestamp, timestamp, false_value(), timestamp),
        )
    conn.commit()
    row = conn.execute(param_sql("SELECT * FROM items WHERE article_number = ?"), (article,)).fetchone()
    return row_to_item(row)


@app.post("/items/{article_number}/not-there", response_model=NotThereResponse)
def mark_not_there(article_number: str, conn=Depends(db)) -> NotThereResponse:
    article = normalize_article(article_number)
    row = conn.execute(param_sql("SELECT * FROM items WHERE article_number = ?"), (article,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")

    failure_count = int(row["failure_count"]) + 1
    status = derive_status(failure_count)
    timestamp = now_iso()
    conn.execute(
        param_sql(
            """
            UPDATE items
            SET failure_count = ?, status = ?, last_updated = ?, last_seen_at = ?,
                is_archived = ?, archived_at = NULL
            WHERE article_number = ?
            """
        ),
        (failure_count, status, timestamp, timestamp, false_value(), article),
    )
    conn.commit()
    updated = conn.execute(param_sql("SELECT * FROM items WHERE article_number = ?"), (article,)).fetchone()
    item = row_to_item(updated)
    return NotThereResponse(**item.dict())


@app.get("/items", response_model=list[ItemOut])
def list_items(
    unreliable: bool = Query(False, description="Return only suspect/stale or over-threshold items."),
    include_archived: bool = Query(False, description="Include soft-archived records in list results."),
    conn=Depends(db),
) -> list[ItemOut]:
    unreliable = unreliable if isinstance(unreliable, bool) else False
    include_archived = include_archived if isinstance(include_archived, bool) else False
    archive_filter = "" if include_archived else f"AND is_archived = {'FALSE' if get_backend() == 'postgres' else 0}"
    if unreliable:
        rows = conn.execute(
            param_sql(
                f"""
                SELECT * FROM items
                WHERE (failure_count >= ? OR status IN ('suspect', 'stale')) {archive_filter}
                ORDER BY failure_count DESC, last_updated ASC
                """
            ),
            (SUSPECT_THRESHOLD,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM items WHERE 1=1 {archive_filter} ORDER BY last_updated DESC"
        ).fetchall()
    return [row_to_item(row) for row in rows]
