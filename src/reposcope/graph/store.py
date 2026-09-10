"""Single-machine transactional metadata, snapshots and durable jobs."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from reposcope.config import RepoScopeError, Settings
from reposcope.models import Snapshot, digest


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.home.mkdir(parents=True, exist_ok=True)
        self.path = settings.home / "reposcope.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS repositories(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ast_cache(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, state TEXT, payload TEXT,
              result TEXT, error TEXT, key TEXT UNIQUE, created REAL, updated REAL,
              owner TEXT, lease REAL, cancel INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS revisions(run_id TEXT, revision INTEGER, data TEXT, PRIMARY KEY(run_id,revision));
            CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY, run_id TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS coverage(id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS recovery(job_id TEXT PRIMARY KEY, state TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS index_publications(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS active_indexes(repo_id TEXT, profile TEXT, publication_id TEXT NOT NULL,
              PRIMARY KEY(repo_id,profile));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, table, key, value):
        assert table in {"repositories", "snapshots", "ast_cache", "evidence", "coverage"}
        data = value.model_dump() if hasattr(value, "model_dump") else value
        with self.connect() as db:
            db.execute(f"INSERT OR REPLACE INTO {table}(id,data) VALUES(?,?)", (key, json.dumps(data)))

    def get(self, table, key):
        assert table in {"repositories", "snapshots", "ast_cache", "evidence", "coverage"}
        with self.connect() as db:
            row = db.execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        if not row:
            raise RepoScopeError("not_found", f"Unknown {table} identifier")
        return json.loads(row[0])

    def list(self, table):
        assert table in {"repositories", "snapshots", "coverage"}
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(f"SELECT data FROM {table} ORDER BY rowid DESC")]

    def snapshot(self, sid):
        return Snapshot.model_validate(self.get("snapshots", sid))

    def active_index(self, repo_id, profile):
        with self.connect() as db:
            row = db.execute(
                "SELECT p.data FROM active_indexes a JOIN index_publications p ON a.publication_id=p.id "
                "WHERE a.repo_id=? AND a.profile=?",
                (repo_id, profile),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def activate_index(self, record, previous_id):
        if record["publication_id"] != digest({k: v for k, v in record.items() if k != "publication_id"}):
            raise RepoScopeError("index_publication_invalid", "Publication content hash mismatch")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT publication_id FROM active_indexes WHERE repo_id=? AND profile=?",
                (record["repo_id"], record["profile"]),
            ).fetchone()
            if (row[0] if row else None) != previous_id:
                raise RepoScopeError("index_publication_conflict", "Published version changed during this build")
            db.execute(
                "INSERT OR IGNORE INTO index_publications VALUES(?,?)", (record["publication_id"], json.dumps(record))
            )
            db.execute(
                "INSERT OR REPLACE INTO active_indexes VALUES(?,?,?)",
                (record["repo_id"], record["profile"], record["publication_id"]),
            )

    def enqueue(self, kind, payload, key=None):
        now, jid = time.time(), uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                row = db.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
                if row:
                    if json.loads(row["payload"]) != payload or row["kind"] != kind:
                        raise RepoScopeError("idempotency_conflict", "Key already used with different input")
                    return row["id"]
            db.execute(
                "INSERT INTO jobs(id,kind,state,payload,key,created,updated) VALUES(?,?,?,?,?,?,?)",
                (jid, kind, "queued", json.dumps(payload), key, now, now),
            )
            self._event(db, jid, "queued", "Task queued")
        return jid

    def _event(self, db, jid, state, message):
        db.execute(
            "INSERT INTO events(job_id,data) VALUES(?,?)", (jid, json.dumps({"state": state, "message": message}))
        )

    def job(self, jid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise RepoScopeError("not_found", "Unknown task")
        d = dict(row)
        d.update(run_id=d["id"], status=d["state"], payload=json.loads(d["payload"]))
        d["report"] = json.loads(d["result"]) if d["result"] else None
        return d

    def jobs(self):
        with self.connect() as db:
            ids = [
                r[0] for r in db.execute("SELECT id FROM jobs WHERE kind='analysis' ORDER BY created DESC LIMIT 100")
            ]
        return [self.job(jid) for jid in ids]

    def claim(self, owner):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            # Test tasks are never automatically rerun after a lost worker lease.
            stale = db.execute(
                "SELECT id,kind,cancel,payload FROM jobs WHERE lease<? AND state NOT IN ('completed','failed','cancelled','interrupted','queued')",
                (now,),
            ).fetchall()
            for row in stale:
                can_execute = row["kind"] == "test" or json.loads(row["payload"]).get("allow_tests", False)
                state = "interrupted" if can_execute else "cancelled" if row["cancel"] else "queued"
                if can_execute:
                    db.execute("INSERT OR IGNORE INTO recovery VALUES(?,?,?)", (row["id"], "pending", "{}"))
                db.execute("UPDATE jobs SET state=?,owner=NULL,lease=NULL WHERE id=?", (state, row["id"]))
                self._event(db, row["id"], state, "Expired worker lease; previous execution is not resubmitted")
            row = db.execute(
                "SELECT id FROM jobs WHERE state='queued' AND cancel=0 ORDER BY created LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE jobs SET state='preparing',owner=?,lease=?,updated=? WHERE id=?",
                (owner, now + self.settings.lease_seconds, now, row[0]),
            )
            self._event(db, row[0], "preparing", "Worker acquired task")
            return row[0]

    def heartbeat(self, jid, owner):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET lease=? WHERE id=? AND owner=?",
                (time.time() + self.settings.lease_seconds, jid, owner),
            )

    def update(self, jid, state, message="", result=None, error=None, owner=None):
        with self.connect() as db:
            if owner is not None:
                db.execute("BEGIN IMMEDIATE")
                owned = db.execute("SELECT owner FROM jobs WHERE id=?", (jid,)).fetchone()
                if not owned or owned[0] != owner:
                    return False
            db.execute(
                "UPDATE jobs SET state=?,updated=?,result=COALESCE(?,result),error=? WHERE id=?",
                (state, time.time(), json.dumps(result) if result is not None else None, error, jid),
            )
            self._event(db, jid, state, message or state)
            if result and result.get("revision"):
                db.execute(
                    "INSERT OR REPLACE INTO revisions VALUES(?,?,?)", (jid, result["revision"], json.dumps(result))
                )

    def cancel(self, jid):
        job = self.job(jid)
        if job["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            return
        with self.connect() as db:
            db.execute("UPDATE jobs SET cancel=1 WHERE id=?", (jid,))
            if job["state"] == "queued":
                db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (jid,))
            self._event(db, jid, "cancel_requested", "Cancellation requested; waiting for resource cleanup")

    def events(self, jid, after=0):
        with self.connect() as db:
            return [
                {"event_id": r[0], **json.loads(r[1])}
                for r in db.execute("SELECT id,data FROM events WHERE job_id=? AND id>? ORDER BY id", (jid, after))
            ]

    def evidence(self, snapshot, symbol):
        lines = snapshot.files[symbol.path].splitlines()
        data = {
            "snapshot_id": snapshot.snapshot_id,
            "path": symbol.path,
            "start": symbol.start,
            "end": symbol.end,
            "source": "\n".join(lines[symbol.start - 1 : symbol.end]),
            "content_hash": symbol.content_hash,
        }
        eid = digest(data)
        self.put("evidence", eid, data)
        return eid
