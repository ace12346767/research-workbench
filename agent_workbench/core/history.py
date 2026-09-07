"""Transactional local transcript storage, independent from the model context budget."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, title TEXT NOT NULL, workspace TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                    request_id TEXT NOT NULL, messages TEXT NOT NULL, status TEXT NOT NULL,
                    provider_key TEXT NOT NULL, usage TEXT NOT NULL, context TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                    name TEXT NOT NULL, mime TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL,
                    text TEXT NOT NULL, data BLOB NOT NULL, preview BLOB, attached INTEGER NOT NULL,
                    created_at TEXT NOT NULL, reserved TEXT);
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, workspace TEXT, payload TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    session_id TEXT PRIMARY KEY REFERENCES sessions ON DELETE CASCADE,
                    through_id INTEGER NOT NULL, summary TEXT NOT NULL, provider_key TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS compaction_calls (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
                    status TEXT NOT NULL, usage TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')
        self.session_id = self.active() or self.create()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def active(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key='active'").fetchone()
            return row[0] if row and db.execute('SELECT 1 FROM sessions WHERE session_id=?', (row[0],)).fetchone() else None

    def create(self, workspace=None):
        session_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (session_id, '新会话', workspace, now(), now()))
            db.execute("INSERT OR REPLACE INTO state VALUES ('active',?)", (session_id,))
        self.session_id = session_id
        return session_id

    def select(self, session_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE session_id=?', (session_id,)).fetchone()
            if row is None:
                raise ValueError('Session does not exist')
            db.execute("INSERT OR REPLACE INTO state VALUES ('active',?)", (session_id,))
        self.session_id = session_id
        return dict(row)

    def sessions(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM sessions ORDER BY updated_at DESC')]

    def records(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM turns WHERE session_id=? ORDER BY id', (self.session_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ('messages', 'usage', 'context'):
                item[key] = json.loads(item[key])
            result.append(item)
        return result

    def append(self, request_id, messages, status, provider_key='', usage=None, context=None, record_id=None):
        with self.connect() as db:
            values = (json.dumps(messages, ensure_ascii=False), status, provider_key, json.dumps(usage or {}), json.dumps(context or {}))
            if record_id is None:
                record_id = db.execute('INSERT INTO turns(session_id,request_id,messages,status,provider_key,usage,context,created_at) VALUES (?,?,?,?,?,?,?,?)',
                                       (self.session_id, request_id, *values, now())).lastrowid
            else:
                db.execute('UPDATE turns SET messages=?,status=?,provider_key=?,usage=?,context=? WHERE id=? AND session_id=?',
                           (*values, record_id, self.session_id))
            title = messages[0].get('content', '')[:60].strip() or '新会话'
            db.execute("UPDATE sessions SET title=CASE WHEN title='新会话' THEN ? ELSE title END, updated_at=? WHERE session_id=?",
                       (title, now(), self.session_id))
        return record_id

    def clear(self):
        with self.connect() as db:
            db.execute('DELETE FROM attachments WHERE session_id=?', (self.session_id,))
            db.execute('DELETE FROM checkpoints WHERE session_id=?', (self.session_id,))
            db.execute('DELETE FROM compaction_calls WHERE session_id=?', (self.session_id,))
            db.execute('DELETE FROM turns WHERE session_id=?', (self.session_id,))

    def checkpoint(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM checkpoints WHERE session_id=?', (self.session_id,)).fetchone()
        return dict(row) if row else None

    def save_checkpoint(self, through_id, summary, provider_key, *, expected_through, session_id):
        with self.connect() as db:
            row = db.execute('SELECT through_id, summary FROM checkpoints WHERE session_id=?', (session_id,)).fetchone()
            old = row[0] if row else 0
            valid = db.execute("SELECT 1 FROM turns WHERE session_id=? AND id=? AND status IN ('stop','length')",
                               (session_id, through_id)).fetchone()
            same_boundary_shrinks = row and through_id == old and len(summary.encode('utf-8')) < len(row[1].encode('utf-8'))
            if session_id != self.session_id or old != expected_through or (through_id <= old and not same_boundary_shrinks) or not valid:
                raise ValueError('Conversation changed during compaction; original history retained')
            db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?)',
                       (session_id, through_id, summary, provider_key, now()))

    def record_compaction_call(self, status, usage, session_id):
        with self.connect() as db:
            db.execute('INSERT INTO compaction_calls(session_id,status,usage,created_at) VALUES (?,?,?,?)',
                       (session_id, status, json.dumps(usage), now()))

    def compaction_usage(self):
        with self.connect() as db:
            rows = db.execute('SELECT usage FROM compaction_calls WHERE session_id=?', (self.session_id,)).fetchall()
        result = {'requests': len(rows), 'complete': bool(rows)}
        for row in rows:
            usage = json.loads(row[0])
            result['complete'] &= all(isinstance(usage.get(k), (int, float)) for k in ('prompt_tokens','completion_tokens'))
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                if isinstance(usage.get(key), (int, float)) and usage[key] >= 0:
                    result[key] = result.get(key, 0) + usage[key]
        return result

    def delete(self, session_id):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE session_id=?', (session_id,))
        if session_id == self.session_id:
            remaining = self.sessions()
            self.select(remaining[0]['session_id']) if remaining else self.create()

    def record_audit(self, workspace, payload):
        with self.connect() as db:
            db.execute('INSERT INTO audit(workspace,payload,created_at) VALUES (?,?,?)',
                       (str(workspace), json.dumps(payload, ensure_ascii=False), now()))

    def audit(self, workspace):
        with self.connect() as db:
            return [{'created_at': row['created_at'], **json.loads(row['payload'])} for row in
                    db.execute('SELECT * FROM audit WHERE workspace=? ORDER BY id DESC LIMIT 100', (str(workspace),))]
