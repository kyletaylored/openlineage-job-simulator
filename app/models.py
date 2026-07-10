"""SQLite persistence for job runs so demo history survives an app restart."""
import sqlite3
import threading

from app import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    parent_run_id TEXT,
    root_run_id TEXT,
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    ended_at TEXT,
    duration_seconds REAL,
    error_message TEXT,
    ol_service TEXT,
    request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_request ON runs(request_id);
CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id);
"""


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


def create_run(*, run_id, parent_run_id, root_run_id, namespace, name, job_type,
                ol_service, request_id):
    conn = get_conn()
    conn.execute(
        """INSERT INTO runs (run_id, parent_run_id, root_run_id, namespace, name,
                              job_type, status, ol_service, request_id)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (run_id, parent_run_id, root_run_id, namespace, name, job_type, ol_service, request_id),
    )
    conn.commit()


def mark_started(run_id, started_at):
    conn = get_conn()
    conn.execute(
        "UPDATE runs SET status='running', started_at=? WHERE run_id=?",
        (started_at, run_id),
    )
    conn.commit()


def mark_terminal(run_id, status, ended_at, duration_seconds, error_message=None):
    conn = get_conn()
    conn.execute(
        """UPDATE runs SET status=?, ended_at=?, duration_seconds=?, error_message=?
           WHERE run_id=?""",
        (status, ended_at, duration_seconds, error_message, run_id),
    )
    conn.commit()


def get_run(run_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs_for_request(request_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM runs WHERE request_id=? ORDER BY started_at", (request_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _children(conn, run_id):
    rows = conn.execute(
        "SELECT * FROM runs WHERE parent_run_id=? ORDER BY started_at", (run_id,)
    ).fetchall()
    children = [dict(r) for r in rows]
    for c in children:
        c["workers"] = _children(conn, c["run_id"])
    return children


def list_history(limit=200):
    """Controller runs (no parent) newest-first, each with its descendants nested
    arbitrarily deep under a "workers" key (controller -> workers -> sub-tasks, etc.)."""
    conn = get_conn()
    controllers = conn.execute(
        """SELECT * FROM runs WHERE parent_run_id IS NULL
           ORDER BY COALESCE(started_at, '') DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    result = []
    for c in controllers:
        c = dict(c)
        c["workers"] = _children(conn, c["run_id"])
        result.append(c)
    return result
