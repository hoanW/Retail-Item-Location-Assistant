# Retail Item Location Assistant Web App

## 1. Project Overview

This is a lightweight internal web application for retail work (clothing store).

Purpose:
Help a single user quickly locate where clothing items belong on the store floor and reduce time spent returning items from fitting/changing rooms.

The system is NOT an official inventory system.
It is an approximate “memory assistant” for item locations.

Each item is identified by an article number / barcode and mapped to an approximate store location (area-based).

---

## 2. Core Concept

The store floor is divided into 5 areas:

- A
- B
- C
- D
- E

Optionally later expanded to sub-areas:

- B1, B2, C1, etc.

Each item has:
- article_number (barcode or manual input)
- current_location (area)
- status (valid / suspect / stale)
- failure_count
- last_updated timestamp

---

## 3. Core User Workflows

### 3.1 Find Item (Primary Use Case)

Goal: quickly find where an item is located.

Flow:
1. User scans barcode or enters article number
2. System returns stored location
3. UI shows:
   - location (area)
   - status
   - last updated timestamp

If item is found in correct location → nothing else is required.

If item is NOT found:
- user presses ONLY button: "Not There"
- system increments failure_count
- system updates status to suspect or stale

No "found there" button exists.

---

### 3.2 Assign / Update Location (Unified Flow)

This flow is used for:
- first-time registration
- correcting outdated items
- updating relocated items

Flow:
1. User selects current store area (e.g. B2)
2. User enters scan mode
3. Each scanned item:
   - if item does not exist → create new record
   - if item exists → overwrite current_location
   - reset failure_count to 0
   - update last_updated timestamp

This is a single unified operation (no separate update UI).

---

### 3.3 Outdated Items View

Displays items where location is unreliable.

Criteria:
- failure_count >= threshold OR status = suspect/stale

UI shows:
- article_number
- last known location
- failure_count
- last_updated

User can later fix these items using Assign Location flow.

---

## 4. Item Status Logic

Status is derived from usage:

- valid: recently confirmed / no issues
- suspect: 1–2 failed searches ("Not There")
- stale: repeated failures (likely outdated location)

Rules:
- Each "Not There" increases failure_count
- failure_count triggers status downgrade
- Any successful re-assignment resets status to valid

---

## 5. Backend Requirements

Use Python backend with FastAPI.

### 5.1 Database

- Local development: use SQLite.
- Deployment/production: use PostgreSQL configured via environment variables (for example, a `DATABASE_URL`).
- Production database persistence must survive redeployments.
- Never rely on local SQLite file persistence in production hosting environments.


Table: items

Fields:
- id (primary key)
- article_number (unique)
- current_location (string)
- status (string)
- failure_count (int)
- last_updated (datetime)
- created_at (datetime)

---

### 5.2 API Endpoints

#### GET /items/{article_number}
Returns item location + status.

---

#### POST /items/assign

Used for both:
- new item creation
- updating existing location

Request:
```json
{
  "article_number": "123456",
  "location": "B2"
}
```
