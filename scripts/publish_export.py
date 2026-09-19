"""Writes a synthetic Salesforce-shaped export to the GCS bucket.

This stands in for the scheduled export job a real deployment would run against
a Salesforce org. It is honest about what it is: the CSVs carry Salesforce API
field names and a real export manifest, but the rows are generated, and no
Salesforce org is involved anywhere in this repository.

The manifest is the part that matters architecturally. It records when the
export was produced, which is what lets the pipeline know how stale its own view
of the world is.
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.normalize import EXPORT_FILES, denormalize

MANIFEST = "export_manifest.json"


def _csv_bytes(rows, fieldnames) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def build_export(fixture_dir: str, exported_at: str) -> dict:
    files, counts = {}, {}
    for table, (filename, mapping) in EXPORT_FILES.items():
        with open(os.path.join(fixture_dir, f"{table}.json")) as f:
            rows = json.load(f)
        export_rows = denormalize(rows, mapping)
        files[filename] = _csv_bytes(export_rows, list(mapping.keys()))
        counts[filename] = len(export_rows)

    manifest = {
        "source_system": "salesforce",
        "export_type": "scheduled_full_extract",
        "exported_at": exported_at,
        "objects": sorted(counts),
        "row_counts": counts,
        "note": ("Synthetic data on the Salesforce export schema. No Salesforce org "
                 "is connected to this repository."),
    }
    files[MANIFEST] = json.dumps(manifest, indent=2).encode()
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default=config.EXPORT_BUCKET)
    ap.add_argument("--prefix", default=config.EXPORT_PREFIX)
    ap.add_argument("--fixtures", default=config.FIXTURE_DIR)
    ap.add_argument("--stale-days", type=float, default=0.0,
                    help="Backdate the export, to exercise staleness handling.")
    ap.add_argument("--local-only", metavar="DIR", help="Write to a directory instead of GCS")
    args = ap.parse_args()

    as_of = datetime.fromisoformat(config.AS_OF_DATE).replace(tzinfo=timezone.utc)
    exported_at = (as_of - timedelta(days=args.stale_days)).isoformat().replace("+00:00", "Z")
    files = build_export(args.fixtures, exported_at)

    if args.local_only:
        os.makedirs(args.local_only, exist_ok=True)
        for name, blob in files.items():
            with open(os.path.join(args.local_only, name), "wb") as f:
                f.write(blob)
        print(f"Wrote {len(files)} files to {args.local_only}/ (exported_at={exported_at})")
        return 0

    from google.cloud import storage
    bucket = storage.Client(project=config.PROJECT_ID or None).bucket(args.bucket)
    for name, blob in files.items():
        bucket.blob(f"{args.prefix}/{name}").upload_from_string(blob)
        print(f"  gs://{args.bucket}/{args.prefix}/{name}  ({len(blob):,} bytes)")
    print(f"\nExport published. exported_at={exported_at}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
