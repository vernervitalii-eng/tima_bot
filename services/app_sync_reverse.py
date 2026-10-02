"""Durable app-to-bot sleep delivery. Scoped writes; no deletion or replacement."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)


def _uuid(value) -> str:
    return str(UUID(str(value)))


def _milliseconds(value: str | None, precise: bool = True) -> int | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    seconds = delta.days * 86400 + delta.seconds
    return seconds * 1000 + (delta.microseconds // 1000 if precise else 0)


def _datetime(value: int | None) -> str | None:
    return None if value is None else (datetime(1970, 1, 1) + timedelta(milliseconds=value)).isoformat(' ', timespec='microseconds')


def _integer(value, low=1) -> int:
    if type(value) is not int or value < low:
        raise ValueError('Invalid integer')
    return value


def reverse_rpc(settings, action: str, **fields) -> dict:
    body = json.dumps({'version': 1, 'child_id': settings.child_id, 'sequence': time.time_ns() // 1000000, 'action': action, **fields}, separators=(',', ':'), ensure_ascii=False)
    signature = hmac.new(settings.signing_key, body.encode(), hashlib.sha256).hexdigest()
    data = json.dumps({'p_source': settings.source_id, 'p_body': body, 'p_signature': signature}).encode()
    request = urllib.request.Request(settings.supabase_url + '/rest/v1/rpc/br_' + ('pull_bot' if action == 'pull' else 'ack_bot'), data=data, method='POST', headers={'apikey': settings.public_key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read(1_000_001)
    except urllib.error.HTTPError as error:
        category = 'unclassified'
        try:
            reply = json.loads(error.read(2048))
            message = reply.get('message')
            if isinstance(message, str) and re.fullmatch(r'[A-Za-z /:._-]{1,100}', message):
                category = message
        except (OSError, ValueError, TypeError):
            pass
        logger.warning('Reverse sync rejected (action=%s, HTTP=%s, reason=%s)', action, error.code, category)
        raise
    if len(raw) > 1_000_000:
        raise ValueError('Response exceeds bound')
    result = json.loads(raw)
    if action == 'pull':
        text, mac = result.get('body'), result.get('signature')
        if not isinstance(text, str) or not isinstance(mac, str) or not hmac.compare_digest(hmac.new(settings.signing_key, text.encode(), hashlib.sha256).hexdigest(), mac):
            raise ValueError('Unverified reverse response')
        result = json.loads(text)
        if result.get('version') != 1 or result.get('child_id') != settings.child_id or result.get('source_id') != settings.source_id or not isinstance(result.get('jobs'), list) or len(result['jobs']) > 100:
            raise ValueError('Wrong reverse scope')
    elif not result.get('ok'):
        raise ValueError('Acknowledgement failed')
    return result


def _connect(settings):
    path = Path(settings.db_path)
    if not path.is_absolute() or not path.is_file():
        raise FileNotFoundError('Existing SQLite required')
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    return db


def apply_job(settings, job: dict, now: int | None = None) -> dict:
    """Atomically persist both a sleep and its receipt. Retries cannot duplicate it."""
    job_id, app_id = _uuid(job['id']), _uuid(job['app_id'])
    revision = _integer(job['revision'])
    payload = job['payload']
    if payload.get('app_id') != app_id or payload.get('revision') != revision or type(payload.get('deleted')) is not bool:
        raise ValueError('Wrong job identity')
    start, end, kind = _integer(payload['start'], 946684800000), payload.get('end'), payload.get('kind')
    current = time.time_ns() // 1000000 if now is None else now
    if end is not None:
        _integer(end)
    if start > current + 60000 or end is not None and (end <= start or end - start > 86400000 or end > current + 60000) or kind not in ('day', 'night'):
        raise ValueError('Invalid sleep interval')
    job_hash = hashlib.sha256(json.dumps(job, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with closing(_connect(settings)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        from services.app_sync import verify_family_scope
        verify_family_scope(db, settings)
        # These separate tables only add receipts/mappings/audit; existing tables stay intact.
        db.execute('CREATE TABLE IF NOT EXISTS app_sync_receipts (job_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,child_id INTEGER NOT NULL,app_id TEXT NOT NULL,revision INTEGER NOT NULL,job_hash TEXT NOT NULL,result_json TEXT NOT NULL,notified INTEGER NOT NULL DEFAULT 0,applied_at INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS app_sync_row_links (source_id TEXT NOT NULL,app_id TEXT NOT NULL,child_id INTEGER NOT NULL,bot_id INTEGER NOT NULL,revision INTEGER NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(source_id,app_id),UNIQUE(source_id,bot_id))')
        db.execute('CREATE TABLE IF NOT EXISTS app_sync_audit (job_id TEXT PRIMARY KEY,child_id INTEGER NOT NULL,before_json TEXT,after_json TEXT NOT NULL,actor TEXT NOT NULL,at INTEGER NOT NULL)')
        receipt = db.execute('SELECT * FROM app_sync_receipts WHERE job_id=?', (job_id,)).fetchone()
        if receipt:
            if receipt['source_id'] != settings.source_id or receipt['child_id'] != settings.child_id or receipt['job_hash'] != job_hash:
                raise ValueError('Receipt identity changed')
            return json.loads(receipt['result_json'])
        local_link = db.execute('SELECT * FROM app_sync_row_links WHERE source_id=? AND app_id=?', (settings.source_id, app_id)).fetchone()
        bot_id = job.get('bot_id')
        if bot_id is not None:
            _integer(bot_id)
        expected = job.get('expected')
        if local_link:
            if local_link['child_id'] != settings.child_id or bot_id is not None and local_link['bot_id'] != bot_id:
                raise ValueError('Link scope changed')
            bot_id = local_link['bot_id']
            expected = json.loads(local_link['payload_json'])
        old = None
        problem = None
        if payload['deleted']:
            problem = 'Удаление из приложения не удаляет историю Telegram.'
        elif local_link and revision <= local_link['revision']:
            problem = 'Поздняя версия приложения не заменяет более новую.'
        elif bot_id is not None:
            old = db.execute('SELECT * FROM sleep_logs WHERE id=? AND child_id=?', (bot_id, settings.child_id)).fetchone()
            if old is None:
                problem = 'Запись Telegram не найдена; история приложения сохранена.'
            else:
                precise = bool(local_link) or settings.precise_timestamps
                actual = {'id': bot_id, 'start': _milliseconds(old['start_time'], precise), 'end': _milliseconds(old['end_time'], precise), 'kind': old['sleep_type']}
                if actual != expected:
                    problem = 'Время изменено в Telegram; обе версии сохранены.'
        elif expected is not None:
            raise ValueError('Missing expected target identity')
        if problem is None:
            rows = db.execute('SELECT id,start_time,end_time FROM sleep_logs WHERE child_id=?', (settings.child_id,)).fetchall()
            if any(r['id'] != bot_id and start < (_milliseconds(r['end_time']) or 9223372036854775807) and (end or 9223372036854775807) > _milliseconds(r['start_time']) for r in rows):
                problem = 'Сон пересекается с записью Telegram; обе версии сохранены.'
        if problem:
            result = {'status': 'conflict', 'reason': problem}
        else:
            owner = db.execute("SELECT id FROM users WHERE child_id=? ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END,id LIMIT 1", (settings.child_id,)).fetchone()
            if owner is None:
                raise ValueError('Family author is missing')
            if old is None:
                cursor = db.execute('INSERT INTO sleep_logs(child_id,start_time,end_time,sleep_type,created_by_user_id,ended_by_user_id) VALUES(?,?,?,?,?,?)', (settings.child_id, _datetime(start), _datetime(end), kind, owner['id'], owner['id'] if end is not None else None))
                bot_id = cursor.lastrowid
            else:
                db.execute('UPDATE sleep_logs SET start_time=?,end_time=?,sleep_type=?,ended_by_user_id=? WHERE id=? AND child_id=?', (_datetime(start), _datetime(end), kind, owner['id'] if end is not None else None, bot_id, settings.child_id))
            canonical = {'id': bot_id, 'start': start, 'end': end, 'kind': kind}
            db.execute('INSERT INTO app_sync_row_links VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,app_id) DO UPDATE SET revision=excluded.revision,payload_json=excluded.payload_json', (settings.source_id, app_id, settings.child_id, bot_id, revision, json.dumps(canonical)))
            changed = dict(db.execute('SELECT * FROM sleep_logs WHERE id=?', (bot_id,)).fetchone())
            db.execute('INSERT INTO app_sync_audit VALUES(?,?,?,?,?,?)', (job_id, settings.child_id, json.dumps(dict(old)) if old else None, json.dumps(changed), str(payload.get('actor', 'Приложение'))[:80], current))
            result = {'status': 'applied', 'bot_id': bot_id, 'canonical': canonical}
        db.execute('INSERT INTO app_sync_receipts VALUES(?,?,?,?,?,?,?,?,?)', (job_id, settings.source_id, settings.child_id, app_id, revision, job_hash, json.dumps(result), 0 if result['status'] == 'applied' else 1, current))
        return result


def pending_notifications(settings, mark=False) -> bool:
    with closing(_connect(settings)) as db, db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_sync_receipts'").fetchone():
            return False
        where = 'source_id=? AND child_id=? AND notified=0'
        args = (settings.source_id, settings.child_id)
        pending = bool(db.execute('SELECT 1 FROM app_sync_receipts WHERE ' + where + ' LIMIT 1', args).fetchone())
        if mark:
            db.execute('UPDATE app_sync_receipts SET notified=1 WHERE ' + where, args)
        return pending


async def pull_and_apply(settings) -> bool:
    import asyncio
    from database.session import db_lock
    response = await asyncio.to_thread(reverse_rpc, settings, 'pull')
    for job in response['jobs']:
        async with db_lock:
            result = await asyncio.to_thread(apply_job, settings, job)
        # The receipt committed before ACK. A lost reply only retries the same job.
        try:
            await asyncio.to_thread(reverse_rpc, settings, 'ack', job_id=job['id'], result=result)
        except urllib.error.HTTPError:
            logger.warning('Reverse ACK pending (sequence=%s, bot_id=%s, status=%s)',
                           job.get('sequence'), result.get('bot_id'), result.get('status'))
            raise
    return len(response['jobs']) == 100
