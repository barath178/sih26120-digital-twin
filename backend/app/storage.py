"""Persistence (SQLite). Schema mirrors a TimescaleDB layout (telemetry hypertable keyed on
time + well) so it can be pointed at TimescaleDB/PostgreSQL for a field deployment."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from . import config

DB_PATH = config.DATA_DIR / "twin.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    time TEXT NOT NULL, sim_day REAL NOT NULL, well_id TEXT NOT NULL, phase TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tel_well_time ON telemetry (well_id, sim_day);
CREATE TABLE IF NOT EXISTS audit (
    time TEXT NOT NULL, sim_time TEXT, actor TEXT, action TEXT, well_id TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS scenario (
    scenario_id TEXT PRIMARY KEY, created_at TEXT, base_well_id TEXT, kind TEXT, inputs_json TEXT, outputs_json TEXT,
    model_version TEXT, data_mode TEXT
);
CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY, well_id TEXT, type TEXT, status TEXT, payload TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, well_id TEXT, type TEXT, severity TEXT, status TEXT, payload TEXT, updated TEXT
);
"""


class Storage:
    def __init__(self, path=DB_PATH, fresh: bool = True):
        if fresh and path.exists():
            try:
                path.unlink()
            except OSError:  # another twin process holds the file (Windows): use a private one
                import os

                path = path.with_name(f"twin_{os.getpid()}.db")
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def write_telemetry(self, rows: list[tuple]):
        with self._lock:
            self._conn.executemany("INSERT INTO telemetry VALUES (?,?,?,?,?)", rows)
            self._conn.commit()

    def audit(self, sim_time: str, actor: str, action: str, well_id: str | None, detail: dict | str):
        with self._lock:
            self._conn.execute("INSERT INTO audit VALUES (?,?,?,?,?,?)",
                               (datetime.now().isoformat(timespec="seconds"), sim_time, actor, action, well_id,
                                detail if isinstance(detail, str) else json.dumps(detail)))
            self._conn.commit()

    def audit_log(self, limit: int = 200) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT time, sim_time, actor, action, well_id, detail FROM audit ORDER BY rowid DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        return [dict(time=r[0], sim_time=r[1], actor=r[2], action=r[3], well_id=r[4], detail=r[5]) for r in rows]

    def upsert(self, table: str, obj: dict):
        with self._lock:
            if table == "recommendations":
                self._conn.execute("INSERT OR REPLACE INTO recommendations VALUES (?,?,?,?,?,?)",
                                   (obj["id"], obj["well_id"], obj["type"], obj["status"], json.dumps(obj, default=str),
                                    datetime.now().isoformat(timespec="seconds")))
            else:
                self._conn.execute("INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?)",
                                   (obj["id"], obj["well_id"], obj["type"], obj["severity"], obj["status"],
                                    json.dumps(obj, default=str), datetime.now().isoformat(timespec="seconds")))
            self._conn.commit()

    def log_scenario(self, well_id: str, kind: str, inputs: dict, outputs: dict, model_version: str, data_mode: str) -> str:
        import uuid

        sid = "SC-" + uuid.uuid4().hex[:8].upper()
        with self._lock:
            self._conn.execute("INSERT INTO scenario VALUES (?,?,?,?,?,?,?,?)",
                               (sid, datetime.now().isoformat(timespec="seconds"), well_id, kind, json.dumps(inputs, default=str),
                                json.dumps(outputs, default=str), model_version, data_mode))
            self._conn.commit()
        return sid

    def scenarios(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT scenario_id, created_at, base_well_id, kind, inputs_json, outputs_json, model_version, data_mode "
                                      "FROM scenario ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(scenario_id=r[0], created_at=r[1], base_well_id=r[2], kind=r[3], inputs=json.loads(r[4]), outputs=json.loads(r[5]),
                     model_version=r[6], data_mode=r[7]) for r in rows]

    def telemetry_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0])

    def close(self):
        with self._lock:
            self._conn.close()
