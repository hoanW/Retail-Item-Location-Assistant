# Retail Item Location Assistant

Lightweight FastAPI web app for remembering approximate clothing item locations on a store floor.

## Features

- Find item by article number / barcode
- Phone camera barcode scanning for Find and Assign flows
- Single **Not There** button to mark failed searches
- Assign/update location flow for new and existing items
- Outdated/suspect items view
- SQLite persistence for local development
- PostgreSQL support for deployment persistence

## Database configuration

Local development uses SQLite by default. Deployment/production should use a managed PostgreSQL database configured by environment variable so data survives redeployments. Do not rely on a local SQLite file in production hosting environments.

- Local SQLite default: `retail_locations.db` in the project root
- Local SQLite override: `RETAIL_DB_PATH=/path/to/file.db`
- Production PostgreSQL: set `DATABASE_URL=postgresql://...` (or `POSTGRES_URL=postgresql://...`)

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn retail_location_app.main:app --reload
```

Open <http://127.0.0.1:8000>. Camera scanning works on localhost, or on HTTPS when opened from a phone.

By default the database is `retail_locations.db` in the project root. Override with:

```bash
RETAIL_DB_PATH=/path/to/file.db uvicorn retail_location_app.main:app --reload
```

## Deploy

Set `DATABASE_URL` to a persistent PostgreSQL database supplied by your host. The app initializes the required table/indexes on startup.

Example:

```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname uvicorn retail_location_app.main:app --host 0.0.0.0 --port 8000
```

## API

- `GET /items/{article_number}` — find one item
- `POST /items/assign` — create or update an item and reset status
- `POST /items/{article_number}/not-there` — increment failure count and downgrade status
- `GET /items?unreliable=true` — list suspect/stale items

Status rules:

- `valid`: 0 failures
- `suspect`: 1–2 failures
- `stale`: 3+ failures

## Tests

```bash
pytest
```

## Barcode scanning

The web UI has camera scan buttons for both Find Item and Assign / Update Location. It uses the browser `BarcodeDetector` API when available and falls back to ZXing via CDN (`@zxing/library`).

Phone notes:

- Use the rear camera when available.
- Camera permissions require `localhost` during local testing or HTTPS on another device.
- In Assign mode, the scanner stays open after each successful scan so you can batch-register items in the selected area.

## Data retention

Items now use soft lifecycle cleanup instead of hard deletion. New fields are stored in the configured database:

- `last_seen_at`
- `archived_at`
- `is_archived`

Lifecycle behavior:

- Active: recently updated.
- Aging: older than `AGING_AFTER_DAYS` days, default `30`; still searchable and visually marked old.
- Archived: older than `ARCHIVE_AFTER_DAYS` days by `last_updated`, default `60`; hidden from default lists but still searchable directly. Assigning an archived item to a location reactivates it.

Archive maintenance runs automatically on startup, periodically in the background, and when API requests open a DB session. You can also trigger it manually:

```bash
curl -X POST http://127.0.0.1:8000/maintenance/archive
```

Configuration:

```bash
AGING_AFTER_DAYS=30 ARCHIVE_AFTER_DAYS=60 ARCHIVE_CHECK_SECONDS=86400 uvicorn retail_location_app.main:app --reload
```
