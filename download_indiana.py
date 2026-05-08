"""
Script: download_indiana.py

Downloads all Indiana hospital standard charges files from the
hospitalpricingfiles.org / visiblecharges API into the /data folder.

Usage:
    python download_indiana.py [--refresh-manifest] [--limit N] [--dry-run]

Arguments:
    --refresh-manifest   Re-fetch hospital list from API (overwrites reference/indiana_hospitals.json)
    --limit N            Only download first N hospitals (for testing)
    --dry-run            Print what would be downloaded without downloading

The script:
  - Skips files already present in /data (resume-safe)
  - Prefers ZIP when both ZIP and CSV are available (smaller on disk)
  - Streams downloads so large files never fully load into RAM
  - Logs results to data/download_log.json
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
REFERENCE_DIR = BASE_DIR / "reference"
MANIFEST_PATH = REFERENCE_DIR / "indiana_hospitals.json"
LOG_PATH = DATA_DIR / "download_log.json"

API_URL = "https://staging01.visiblecharges.com/facility/search?search=&searchstate=IN"
API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://hospitalpricingfiles.org",
    "Referer": "https://hospitalpricingfiles.org/",
    # These session headers are required but not secret — any valid browser visit generates them.
    # Re-generate by visiting hospitalpricingfiles.org and opening DevTools > Network.
    "ptsessionid": "c25b65fe-73ef-4044-9756-9c351b07c1d0",
    "sessionid": "5087494111016062868872034",
}

# Prefer these formats in order when a hospital has multiple files
FORMAT_PRIORITY = ["zip", "csv", "json", "xlsx"]

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
}

CHUNK_SIZE = 1024 * 512  # 512 KB chunks


def fetch_manifest() -> list:
    """Fetch the list of all Indiana hospitals from the API."""
    print(f"Fetching hospital manifest from API...")
    req = urllib.request.Request(API_URL, headers=API_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    print(f"  Found {len(data)} hospitals.")
    DATA_DIR.mkdir(exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved to {MANIFEST_PATH}")
    return data


def load_manifest() -> list:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            return json.load(f)
    return fetch_manifest()


def pick_best_file(files: list) -> dict | None:
    """Pick the best file from a hospital's file list based on FORMAT_PRIORITY."""
    if not files:
        return None
    by_suffix = {f["filesuffix"].lower(): f for f in files}
    for fmt in FORMAT_PRIORITY:
        if fmt in by_suffix:
            return by_suffix[fmt]
    return files[0]  # fallback to first


def dest_path(filename: str, fileid: str, seen_filenames: dict) -> Path:
    """
    Return destination path for a file. If a different fileid already claimed
    this filename, disambiguate by prepending the fileid prefix.
    """
    if filename not in seen_filenames:
        seen_filenames[filename] = fileid
        return DATA_DIR / filename
    if seen_filenames[filename] == fileid:
        # Exact same file (same fileid) — reuse the same destination
        return DATA_DIR / filename
    # Different fileid, same filename — disambiguate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    new_name = f"{stem}__{fileid[:8]}{suffix}"
    return DATA_DIR / new_name


def format_size(n_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def download_file(url: str, dest: Path, hospital_name: str) -> dict:
    """
    Stream-download url to dest. Returns a result dict.
    Never extracts ZIP — stores as-is for later pandas reading.
    """
    result = {
        "hospital": hospital_name,
        "url": url,
        "dest": str(dest),
        "status": None,
        "bytes": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    try:
        req = urllib.request.Request(url, headers=DOWNLOAD_HEADERS)
        with urllib.request.urlopen(req, timeout=120) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = r.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(f"\r    {pct:5.1f}%  {format_size(downloaded)} / {format_size(total)}   ", end="", flush=True)
                    else:
                        print(f"\r    {format_size(downloaded)} downloaded...   ", end="", flush=True)
            print()
            result["status"] = "ok"
            result["bytes"] = downloaded
    except urllib.error.HTTPError as e:
        result["status"] = "http_error"
        result["error"] = f"HTTP {e.code}: {e.reason}"
        print(f"    ERROR: {result['error']}")
        if dest.exists():
            dest.unlink()
    except urllib.error.URLError as e:
        result["status"] = "url_error"
        result["error"] = str(e.reason)
        print(f"    ERROR: {result['error']}")
        if dest.exists():
            dest.unlink()
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"    ERROR: {result['error']}")
        if dest.exists():
            dest.unlink()

    return result


def main():
    parser = argparse.ArgumentParser(description="Download Indiana hospital price files")
    parser.add_argument("--refresh-manifest", action="store_true", help="Re-fetch hospital list from API")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N hospitals")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without downloading")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)

    if args.refresh_manifest or not MANIFEST_PATH.exists():
        hospitals = fetch_manifest()
    else:
        hospitals = load_manifest()
        print(f"Loaded {len(hospitals)} hospitals from {MANIFEST_PATH}")

    if args.limit:
        hospitals = hospitals[: args.limit]
        print(f"Limiting to first {args.limit} hospitals.")

    # Load existing log
    log: list = []
    if LOG_PATH.exists():
        with open(LOG_PATH, encoding="utf-8") as f:
            log = json.load(f)
    already_logged = {entry["dest"] for entry in log if entry.get("status") == "ok"}

    # Plan downloads
    plan = []
    skipped_exists = 0
    skipped_no_file = 0
    seen_filenames: dict = {}  # filename -> fileid, for collision detection

    for h in hospitals:
        files = h.get("files", [])
        chosen = pick_best_file(files)
        if not chosen:
            skipped_no_file += 1
            continue
        dest = dest_path(chosen["filename"], chosen["fileid"], seen_filenames)
        if str(dest) in already_logged or dest.exists():
            skipped_exists += 1
            continue
        plan.append({
            "hospital": h["name"],
            "city": h["city"],
            "filename": dest.name,
            "url": chosen["url"],
            "suffix": chosen["filesuffix"],
            "size_bytes": int(chosen.get("size", 0)),
            "dest": dest,
        })

    print(f"\n{'=' * 60}")
    print(f"Plan: {len(plan)} to download, {skipped_exists} already present, {skipped_no_file} have no file")
    total_known = sum(p["size_bytes"] for p in plan if p["size_bytes"])
    if total_known:
        print(f"Known download size: ~{format_size(total_known)} (some sizes unknown)")
    print(f"{'=' * 60}\n")

    if args.dry_run:
        for i, p in enumerate(plan, 1):
            size_str = format_size(p["size_bytes"]) if p["size_bytes"] else "size unknown"
            print(f"  [{i:3d}] {p['hospital']} ({p['city']})  [{p['suffix'].upper()}  {size_str}]")
            print(f"         -> {p['filename']}")
        return

    # Download
    results = []
    for i, p in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {p['hospital']} ({p['city']})")
        print(f"  {p['suffix'].upper()}  {format_size(p['size_bytes']) if p['size_bytes'] else '?'}  {p['filename']}")

        result = download_file(p["url"], p["dest"], p["hospital"])
        results.append(result)

        if result["status"] == "ok":
            print(f"  OK  {format_size(result['bytes'])}")
        else:
            print(f"  FAILED: {result['error']}")

        # Small courtesy delay to avoid hammering hospital servers
        time.sleep(0.5)

    # Update log
    log.extend(results)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)

    # Summary
    ok = sum(1 for r in results if r["status"] == "ok")
    failed = [r for r in results if r["status"] != "ok"]
    total_bytes = sum(r["bytes"] for r in results)

    print(f"\n{'=' * 60}")
    print(f"Done: {ok} downloaded ({format_size(total_bytes)}), {len(failed)} failed")
    if failed:
        print(f"\nFailed hospitals:")
        for r in failed:
            print(f"  {r['hospital']}: {r['error']}")
    print(f"Log saved to {LOG_PATH}")


if __name__ == "__main__":
    main()
