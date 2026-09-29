"""Family-scoped sleep bridge with opt-in, audited reverse synchronization."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeSettings:
    db_path: str
    room_code: str
    child_id: int
    source_id: str
    supabase_url: str
    public_key: str
    signing_key: bytes
    reverse_enabled: bool = False

    @classmethod
    def from_env(cls) -> BridgeSettings | None:
        if os.getenv("APP_SYNC_ENABLED", "").lower() != "true":
            return None
        url = os.environ["APP_SYNC_SUPABASE_URL"].rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9]+\.supabase\.co", parsed.hostname or "") or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("Invalid sync destination")
        key = bytes.fromhex(os.environ["APP_SYNC_SIGNING_KEY"])
        source = os.environ["APP_SYNC_SOURCE_ID"]
        if len(key) != 32 or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", source):
            raise ValueError("Invalid sync identity")
        public_key = os.environ["APP_SYNC_PUBLIC_KEY"]
        if not public_key.startswith("sb_publishable_"):
            raise ValueError("Only a public Supabase key is accepted")
        path = Path(os.environ["APP_SYNC_DB_PATH"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError("Existing absolute SQLite path required")
        child = int(os.environ["APP_SYNC_CHILD_ID"])
        room = os.environ["APP_SYNC_ROOM_CODE"]
        if child <= 0 or not re.fullmatch(r"[A-Z0-9]{6,12}", room):
            raise ValueError("Invalid source family")
        return cls(str(path), room, child, source, url, public_key, key, os.getenv('APP_SYNC_REVERSE_ENABLED', '').lower() == 'true')


def read_snapshot(settings: BridgeSettings) -> list[dict]:
    path = Path(settings.db_path)
    if not path.is_file():
        raise FileNotFoundError("Source SQLite is unavailable")
    # mode=ro prevents accidental creation, PRAGMA query_only prevents mutations.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3.0)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        children = db.execute("SELECT id FROM children WHERE invite_code=?", (settings.room_code,)).fetchall()
        if children != [(settings.child_id,)]:
            raise ValueError("Source family does not match configuration")
        rows = db.execute("SELECT id,start_time,end_time,sleep_type FROM sleep_logs WHERE child_id=? ORDER BY start_time,id", (settings.child_id,)).fetchall()
        precise_ids = set()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_sync_row_links'").fetchone():
            precise_ids = {r[0] for r in db.execute('SELECT bot_id FROM app_sync_row_links WHERE source_id=? AND child_id=?', (settings.source_id, settings.child_id))}
        if len(rows) > 10000:
            raise ValueError("Source history exceeds configured bound")
    def milliseconds(value: str | None) -> int | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # Matches the already imported source's second precision.
        return int(parsed.timestamp()) * 1000
    from services.app_sync_reverse import _milliseconds
    return [{"id":r[0],"start":_milliseconds(r[1]) if r[0] in precise_ids else milliseconds(r[1]),"end":_milliseconds(r[2]) if r[0] in precise_ids else milliseconds(r[2]),"kind":r[3]} for r in rows]


def signed_request(settings: BridgeSettings, sleeps: list[dict], sequence: int) -> dict:
    body = json.dumps({"version":1,"child_id":settings.child_id,"sequence":sequence,"sleeps":sleeps}, ensure_ascii=False, separators=(",", ":"))
    signature = hmac.new(settings.signing_key, body.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"p_source":settings.source_id,"p_body":body,"p_signature":signature}


def send_snapshot(settings: BridgeSettings, sleeps: list[dict], sequence: int) -> dict:
    payload = json.dumps(signed_request(settings, sleeps, sequence), ensure_ascii=False).encode("utf-8")
    if len(payload) > 1500000:
        raise ValueError("Snapshot is too large")
    request = urllib.request.Request(settings.supabase_url + "/rest/v1/rpc/br_ingest_bot", data=payload, method="POST", headers={"apikey":settings.public_key,"Content-Type":"application/json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        result = json.loads(response.read(4096))
    if not result.get("ok"):
        raise RuntimeError("Sync not acknowledged")
    return result


async def sync_loop(settings: BridgeSettings, bot=None) -> None:
    last_hash = ""
    last_sent = 0.0
    failures = 0
    last_conflicts = None
    while True:
        try:
            if settings.reverse_enabled:
                from services.app_sync_reverse import pull_and_apply, pending_notifications
                from database.session import db_lock
                batch_full = await pull_and_apply(settings)
                async with db_lock:
                    notify = await asyncio.to_thread(pending_notifications, settings)
                if notify and bot is not None:
                    from services.live_status import resync_child_runtime
                    await resync_child_runtime(bot, settings.child_id)
                    async with db_lock:
                        await asyncio.to_thread(pending_notifications, settings, True)
                if batch_full:
                    # Drain the ordered outbox before echoing a potentially older snapshot.
                    await asyncio.sleep(1)
                    continue
            sleeps = await asyncio.to_thread(read_snapshot, settings)
            fingerprint = hashlib.sha256(json.dumps(sleeps, sort_keys=True).encode()).hexdigest()
            if fingerprint != last_hash or time.monotonic() - last_sent >= 60:
                result = await asyncio.to_thread(send_snapshot, settings, sleeps, time.time_ns() // 1000000)
                if not result.get("stale"):
                    last_hash, last_sent = fingerprint, time.monotonic()
                    conflicts = result.get("conflicts", 0)
                    if result.get("inserted") or result.get("updated") or conflicts != last_conflicts:
                        logger.info("App sleep sync acknowledged: inserted=%s updated=%s conflicts=%s", result.get("inserted", 0), result.get("updated", 0), conflicts)
                    last_conflicts = conflicts
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failures += 1
            # Never log bodies, secrets, SQL, URLs with credentials or child history.
            logger.warning("App sync temporarily unavailable (%s); bot continues normally", type(error).__name__)
        await asyncio.sleep(min(60, 10 * 2 ** min(failures, 3)))
