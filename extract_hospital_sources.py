"""
Build reference/hospital_sources.json: where each hospital's prices came from and how current they are.

For every file downloaded by download_indiana.py (data/download_log.json), reads the CMS metadata rows
at the top of the hospital's CSV (hospital_name, last_updated_on) and records the file's URL and our
download time. Entries are keyed by the same hospital hash load_to_sqlite.py stores in hospitals.hospital_hash,
so the site can look them up without touching the price database.

Run after downloading and before building the database:
    python extract_hospital_sources.py
"""

import csv
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from load_to_sqlite import clean_hospital_name, generate_id

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "data" / "download_log.json"
MANIFEST_PATH = BASE_DIR / "reference" / "indiana_hospitals.json"
OUTPUT_PATH = BASE_DIR / "reference" / "hospital_sources.json"

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y")


def parse_date(raw):
    """Normalize a last_updated_on value to YYYY-MM-DD, or None if it can't be read."""
    s = (raw or "").strip()
    if not s:
        return None
    s = re.sub(r"[T ]\d{1,2}:\d{2}.*$", "", s)  # drop any time part
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def read_meta(stream):
    """Return (hospital_name, last_updated_on) from a CMS CSV's first two rows, the way load_to_sqlite reads them."""
    lines = [stream.readline() for _ in range(2)]
    if len(lines) < 2 or not lines[1]:
        return None, None
    row1 = next(csv.reader([lines[0].lstrip("﻿")]), [])
    row2 = next(csv.reader([lines[1]]), [])
    meta = {h.strip().lower(): i for i, h in enumerate(row1) if len(h) < 200}
    if "hospital_name" not in meta:
        return None, None  # no metadata rows: the loader can't name this hospital either
    get = lambda key: row2[meta[key]].strip() if key in meta and meta[key] < len(row2) else None
    return get("hospital_name"), get("last_updated_on")


def read_file_meta(path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in (n for n in zf.namelist() if n.lower().endswith(".csv")):
                with zf.open(name) as raw:
                    return read_meta(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""))
        return None, None
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return read_meta(f)


def main():
    with open(LOG_PATH, encoding="utf-8") as f:
        log = json.load(f)
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = {h["name"]: h for h in json.load(f)}

    sources = {}
    skipped = []
    for entry in log.values():
        path = Path(entry["dest"])
        if entry.get("status") != "ok" or not path.exists():
            skipped.append((entry["hospital"], entry.get("status")))
            continue
        name, updated_raw = read_file_meta(path)
        if not name:
            skipped.append((entry["hospital"], "no metadata rows"))
            continue
        ref = manifest.get(entry["hospital"], {})
        sources[generate_id([name])] = {
            "name": clean_hospital_name(name),
            "last_updated_on": parse_date(updated_raw),
            "last_updated_raw": updated_raw,
            "file_url": entry["url"],
            "file_bytes": entry.get("bytes"),
            "page_url": next((fl.get("pageurl") for fl in ref.get("files", []) if fl.get("pageurl")), None),
            "website": ref.get("url"),
            "downloaded": entry["timestamp"][:10],
        }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=1, sort_keys=True)

    undated = [s["name"] for s in sources.values() if not s["last_updated_on"]]
    print(f"Wrote {len(sources)} hospitals to {OUTPUT_PATH}")
    if undated:
        print(f"{len(undated)} without a readable last_updated_on: {', '.join(sorted(undated))}")
    for hosp, why in skipped:
        print(f"  skipped {hosp}: {why}")


if __name__ == "__main__":
    main()
