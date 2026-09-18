"""Loads JSON fixtures into an in-memory SQLite database.

SQLite (not pandas, not a dict-of-lists) because the queries the deterministic
layer needs are genuinely relational, and because swapping this for a real
warehouse later is then a connection-string change rather than a rewrite.
"""

import json
import os
import sqlite3
from typing import Any, Dict, List

import config

TABLES = ["accounts", "contacts", "deals", "stage_history", "activities", "notes"]


def load_db(fixture_dir: str = config.FIXTURE_DIR) -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for table in TABLES:
        path = os.path.join(fixture_dir, f"{table}.json")
        with open(path) as f:
            rows: List[Dict[str, Any]] = json.load(f)
        if not rows:
            continue
        cols = list(rows[0].keys())
        conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
        conn.executemany(
            f"INSERT INTO {table} VALUES ({', '.join('?' for _ in cols)})",
            [tuple(r.get(c) for c in cols) for r in rows],
        )
    conn.commit()
    return conn


def query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
