"""The reverse bridge must never recycle an ID held by Supabase history."""

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.app_sync import BridgeSettings
from services.app_sync_reverse import apply_job


def test_new_app_sleep_skips_reserved_historical_bot_id(tmp_path):
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE children(id INTEGER PRIMARY KEY, invite_code TEXT NOT NULL);
            CREATE TABLE users(id INTEGER PRIMARY KEY, child_id INTEGER NOT NULL, role TEXT NOT NULL);
            CREATE TABLE sleep_logs(
                id INTEGER PRIMARY KEY, child_id INTEGER NOT NULL, start_time TEXT NOT NULL,
                end_time TEXT, sleep_type TEXT NOT NULL, created_by_user_id INTEGER,
                ended_by_user_id INTEGER
            );
            INSERT INTO children VALUES(1,'ROOM123');
            INSERT INTO users VALUES(1,1,'admin');
            INSERT INTO sleep_logs VALUES(290,1,'2026-10-01 10:00:00','2026-10-01 11:00:00','day',1,1);
        """)

    settings = BridgeSettings(
        db_path=str(path), room_code="ROOM123", child_id=1,
        source_id=str(uuid4()), supabase_url="https://example.supabase.co",
        public_key="sb_publishable_test", signing_key=b"x" * 32,
    )
    start = int(datetime(2026, 10, 5, 10, tzinfo=timezone.utc).timestamp() * 1000)
    app_id = str(uuid4())
    job = {
        "id": str(uuid4()), "app_id": app_id, "revision": 1,
        "bot_id": None, "expected": None,
        "payload": {"app_id": app_id, "revision": 1, "start": start,
                    "end": start + 30 * 60_000, "kind": "day", "deleted": False},
    }

    result = apply_job(settings, job, now=start + 60 * 60_000, reserved_max_bot_id=291)
    assert result["status"] == "applied"
    assert result["bot_id"] == 292
    assert apply_job(settings, job, now=start + 60 * 60_000, reserved_max_bot_id=291) == result
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id FROM sleep_logs ORDER BY id").fetchall() == [(290,), (292,)]
        assert json.loads(db.execute("SELECT result_json FROM app_sync_receipts").fetchone()[0]) == result

    with pytest.raises(ValueError):
        apply_job(settings, {**job, "id": str(uuid4())}, now=start + 60 * 60_000,
                  reserved_max_bot_id=True)
