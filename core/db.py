"""Loads CRM data into an in-memory SQLite database.

Two sources, one internal schema:

  local  - data/fixtures JSON. The default, so tests stay offline and hermetic.
  gcs    - the Salesforce-shaped CSV export in the export bucket, normalized on
           the way in by core/normalize.py.

Either way the export watermark lands in an `export_manifest` table alongside
the data. The watermark travels with the rows it describes, because a snapshot
whose age you cannot look up is a snapshot you cannot reason about: six of the
eight risk rules count days, and all of them are counting against this data.
"""

import csv
import io
import json
import os
import sqlite3
from typing import Any, Dict, List, Tuple

import config
from core.normalize import EXPORT_FILES, normalize

TABLES = ["accounts", "contacts", "deals", "stage_history", "activities", "notes"]
MANIFEST = "export_manifest.json"


def _read_local(fixture_dir: str) -> Tuple[Dict[str, List[dict]], dict]:
    data = {}
    for table in TABLES:
        with open(os.path.join(fixture_dir, f"{table}.json")) as f:
            data[table] = json.load(f)
    # Local fixtures are the live snapshot by definition: nothing has gone stale
    # between generating them and reading them.
    manifest = {"source_system": "local_fixtures", "exported_at": f"{config.AS_OF_DATE}T00:00:00Z"}
    return data, manifest


def _read_gcs(bucket_name: str, prefix: str) -> Tuple[Dict[str, List[dict]], dict]:
    from google.cloud import storage

    bucket = storage.Client(project=config.PROJECT_ID or None).bucket(bucket_name)
    manifest = json.loads(bucket.blob(f"{prefix}/{MANIFEST}").download_as_text())
    data = {}
    for table, (filename, mapping) in EXPORT_FILES.items():
        text = bucket.blob(f"{prefix}/{filename}").download_as_text()
        data[table] = normalize(list(csv.DictReader(io.StringIO(text))), mapping)
    return data, manifest


def load_db(
    fixture_dir: str = None, source: str = None,
    bucket: str = None, prefix: str = None,
) -> sqlite3.Connection:
    source = source or config.DATA_SOURCE
    if source == "gcs":
        data, manifest = _read_gcs(bucket or config.EXPORT_BUCKET, prefix or config.EXPORT_PREFIX)
    else:
        data, manifest = _read_local(fixture_dir or config.FIXTURE_DIR)

    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for table, rows in data.items():
        if not rows:
            continue
        cols = list(rows[0].keys())
        conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
        conn.executemany(
            f"INSERT INTO {table} VALUES ({', '.join('?' for _ in cols)})",
            [tuple(r.get(c) for c in cols) for r in rows],
        )
    conn.execute("CREATE TABLE export_manifest (source_system, exported_at, detail)")
    conn.execute("INSERT INTO export_manifest VALUES (?, ?, ?)",
                 (manifest.get("source_system"), manifest.get("exported_at"), json.dumps(manifest)))
    conn.commit()
    return conn


def watermark(conn: sqlite3.Connection) -> dict:
    """When this data was extracted, and how old that makes it."""
    from datetime import date, datetime

    row = dict(conn.execute("SELECT * FROM export_manifest").fetchone())
    exported = datetime.fromisoformat(row["exported_at"].replace("Z", "+00:00")).date()
    as_of = date.fromisoformat(config.AS_OF_DATE)
    staleness = max(0.0, (as_of - exported).days)
    return {
        "source_system": row["source_system"],
        "exported_at": row["exported_at"],
        "staleness_days": staleness,
        "is_stale": staleness > config.STALENESS_WARN_DAYS,
    }


def query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
