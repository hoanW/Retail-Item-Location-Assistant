import os
from pathlib import Path


def setup_db(tmp_path: Path):
    os.environ["RETAIL_DB_PATH"] = str(tmp_path / "test.db")
    from retail_location_app.database import get_connection, init_db

    init_db()
    return get_connection()


def test_assign_find_and_update(tmp_path):
    from retail_location_app.main import AssignRequest, assign_item, get_item

    conn = setup_db(tmp_path)
    try:
        created = assign_item(AssignRequest(article_number="123456", location="b2"), conn)
        assert created.article_number == "123456"
        assert created.current_location == "B2"
        assert created.status == "valid"
        assert created.failure_count == 0

        found = get_item("123456", conn)
        assert found.current_location == "B2"

        updated = assign_item(AssignRequest(article_number="123456", location="C1"), conn)
        assert updated.current_location == "C1"
    finally:
        conn.close()


def test_not_there_status_flow_and_unreliable_list(tmp_path):
    from retail_location_app.main import AssignRequest, assign_item, list_items, mark_not_there

    conn = setup_db(tmp_path)
    try:
        assign_item(AssignRequest(article_number="999", location="A"), conn)

        first = mark_not_there("999", conn)
        assert first.failure_count == 1
        assert first.status == "suspect"

        second = mark_not_there("999", conn)
        third = mark_not_there("999", conn)
        assert second.failure_count == 2
        assert third.failure_count == 3
        assert third.status == "stale"

        unreliable = list_items(unreliable=True, conn=conn)
        assert [item.article_number for item in unreliable] == ["999"]

        reset = assign_item(AssignRequest(article_number="999", location="D"), conn)
        assert reset.failure_count == 0
        assert reset.status == "valid"
    finally:
        conn.close()


def test_archive_policy_hides_from_default_lists_but_search_finds(tmp_path):
    from datetime import datetime, timedelta, timezone

    from retail_location_app.main import AssignRequest, apply_archive_policy, assign_item, get_item, list_items

    conn = setup_db(tmp_path)
    try:
        assign_item(AssignRequest(article_number="old", location="E"), conn)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        conn.execute(
            "UPDATE items SET last_updated = ?, last_seen_at = ? WHERE article_number = ?",
            (old_timestamp, old_timestamp, "old"),
        )
        conn.commit()

        archived_count = apply_archive_policy(conn)
        assert archived_count == 1

        default_items = list_items(conn=conn)
        assert [item.article_number for item in default_items] == []

        include_archived = list_items(include_archived=True, conn=conn)
        assert include_archived[0].article_number == "old"
        assert include_archived[0].is_archived is True
        assert include_archived[0].lifecycle_state == "archived"
        assert include_archived[0].archived_at is not None

        found = get_item("old", conn)
        assert found.article_number == "old"
        assert found.lifecycle_state == "archived"

        reactivated = assign_item(AssignRequest(article_number="old", location="A"), conn)
        assert reactivated.is_archived is False
        assert reactivated.lifecycle_state == "active"
        assert reactivated.archived_at is None
    finally:
        conn.close()
