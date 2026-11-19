"""SQLite storage: live meter readings, dataset metadata, trained model registry."""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "smartgrid.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id TEXT NOT NULL,
    energy_consumption REAL NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ONLINE',
    source TEXT NOT NULL DEFAULT 'api'
);
CREATE INDEX IF NOT EXISTS idx_live_meter ON live_readings(meter_id, timestamp);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    rows INTEGER,
    columns INTEGER,
    status TEXT NOT NULL DEFAULT 'uploaded'
);

CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    trained_at TEXT NOT NULL,
    rows INTEGER,
    features INTEGER,
    best_model TEXT,
    payload TEXT NOT NULL
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def now():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ---------- live readings ----------
def add_reading(meter_id: str, energy: float, status: str = "ONLINE", source: str = "api"):
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO live_readings (meter_id, energy_consumption, timestamp, status, source) "
            "VALUES (?,?,?,?,?)", (meter_id, energy, ts, status, source))
        rid = cur.lastrowid
    return {"id": rid, "meter_id": meter_id, "energy_consumption": energy,
            "timestamp": ts, "status": status, "source": source}


def list_readings(meter_id=None, limit=200, start=None, end=None):
    q = "SELECT * FROM live_readings WHERE 1=1"
    args = []
    if meter_id:
        q += " AND meter_id = ?"
        args.append(meter_id)
    if start:
        q += " AND timestamp >= ?"
        args.append(start)
    if end:
        q += " AND timestamp <= ?"
        args.append(end)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with connect() as conn:
        return [dict(r) for r in conn.execute(q, args)]


def meters():
    with connect() as conn:
        rows = conn.execute(
            "SELECT meter_id, COUNT(*) n, MAX(timestamp) last_seen, "
            "MAX(id) last_id FROM live_readings GROUP BY meter_id ORDER BY meter_id").fetchall()
        out = []
        for r in rows:
            last = conn.execute("SELECT * FROM live_readings WHERE id=?", (r["last_id"],)).fetchone()
            out.append({"meter_id": r["meter_id"], "readings": r["n"],
                        "last_seen": r["last_seen"],
                        "latest": dict(last) if last else None})
        return out


def reading_stats():
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT meter_id) m, "
            "MIN(timestamp) first, MAX(timestamp) last FROM live_readings").fetchone()
        return dict(row)


def clear_readings(meter_id=None):
    with connect() as conn:
        if meter_id:
            conn.execute("DELETE FROM live_readings WHERE meter_id=?", (meter_id,))
        else:
            conn.execute("DELETE FROM live_readings")


# ---------- datasets ----------
def upsert_dataset(ds):
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO datasets (id, filename, path, uploaded_at, rows, columns, status)"
            " VALUES (?,?,?,?,?,?,?)",
            (ds["id"], ds["filename"], ds["path"], ds.get("uploaded_at", now()),
             ds.get("rows"), ds.get("columns"), ds.get("status", "uploaded")))


def set_dataset_status(dataset_id, status, rows=None, columns=None):
    with connect() as conn:
        conn.execute("UPDATE datasets SET status=?, rows=COALESCE(?,rows), "
                     "columns=COALESCE(?,columns) WHERE id=?", (status, rows, columns, dataset_id))


def list_datasets():
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM datasets ORDER BY uploaded_at DESC")]


# ---------- model runs ----------
def save_run(dataset_id, rows, features, best_model, payload_json):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO model_runs (dataset_id, trained_at, rows, features, best_model, payload)"
            " VALUES (?,?,?,?,?,?)",
            (dataset_id, now(), rows, features, best_model, payload_json))
        return cur.lastrowid


def latest_run(dataset_id):
    with connect() as conn:
        r = conn.execute("SELECT * FROM model_runs WHERE dataset_id=? ORDER BY id DESC LIMIT 1",
                         (dataset_id,)).fetchone()
        return dict(r) if r else None
