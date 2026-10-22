# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# SQLite store for API keys and the audit log.

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    created REAL NOT NULL,
    expires REAL,
    revoked REAL,
    last_used REAL
);
CREATE TABLE IF NOT EXISTS audit (
    time REAL NOT NULL,
    key_id TEXT,
    ip TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
"""


class Db:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as c, c:
            c.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock, closing(self._connect()) as c, c:
            return c.execute(sql, params).rowcount

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with closing(self._connect()) as c:
            return c.execute(sql, params).fetchall()

    def audit(self, action: str, key_id: str | None, ip: str | None, detail: str = "") -> None:
        self.execute(
            "INSERT INTO audit (time, key_id, ip, action, detail) VALUES (?, ?, ?, ?, ?)",
            (time.time(), key_id, ip, action, detail),
        )
