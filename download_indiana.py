"""
Script: download_indiana.py

Downloads all Indiana hospital standard charges files from the
hospitalpricingfiles.org / visiblecharges API into the /data folder.

Usage:
    python download_indiana.py [--refresh-manifest] [--limit N] [--dry-run]
    python download_indiana.py --fix
    python download_indiana.py --full-update

Arguments:
    --refresh-manifest   Re-fetch hospital list from API (overwrites reference/indiana_hospitals.json)
    --limit N            Only process first N hospitals (for testing)
    --dry-run            Print what would be downloaded without downloading
    --fix                Re-scrape the API for updated URLs on all failed entries, then retry
    --full-update        Re-scrape the API and re-download every hospital (replaces existing files)

The script:
  - Skips files already present in /data (resume-safe)
  - Prefers ZIP when both ZIP and CSV are available (smaller on disk)
  - Streams downloads so large files never fully load into RAM
  - Logs results to data/download_log.json (one entry per hospital, keyed by dest path)
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
    "ptsessionid": "5700bab3-cf5e-4fd0-b763-cef27fd64f42",
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


def load_log() -> dict:
    """
    Load download_log.json as a dict keyed by dest path.
    Automatically migrates old list-format logs on first read.
    """
    if not LOG_PATH.exists():
        return {}
    with open(LOG_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        # Migrate: keep only the latest entry per dest
        migrated: dict = {}
        for entry in raw:
            migrated[entry["dest"]] = entry
        print(f"  Migrated log from list ({len(raw)} entries) to dict ({len(migrated)} unique hospitals).")
        return migrated
    return raw


def save_log(log: dict) -> None:
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def update_log_entry(log: dict, old_dest_str: str, result: dict) -> None:
    """Upsert a log entry. Removes the old key if dest changed (e.g. URL redirected to new filename)."""
    if old_dest_str != result["dest"] and old_dest_str in log:
        del log[old_dest_str]
    log[result["dest"]] = result


def set_url(hospital_name: str, new_url: str):
    """
    Manually set the download URL for a hospital (case-insensitive name match),
    then retry the download and update the log.
    """
    log = load_log()
    if not log:
        print("No download log found.")
        return

    name_lower = hospital_name.lower()
    matches = [(dest, e) for dest, e in log.items() if e.get("hospital", "").lower() == name_lower]

    if not matches:
        # Try partial match
        matches = [(dest, e) for dest, e in log.items() if name_lower in e.get("hospital", "").lower()]

    if not matches:
        print(f"No log entry found matching: {hospital_name!r}")
        print("Tip: run --print-failed to see logged hospital names.")
        return

    if len(matches) > 1:
        print(f"Ambiguous match — {len(matches)} hospitals found:")
        for _, e in matches:
            print(f"  {e.get('hospital')}")
        return

    old_dest_str, entry = matches[0]
    hospital = entry["hospital"]
    # Derive dest from the new URL filename, keeping the same directory
    new_filename = new_url.rstrip("/").split("/")[-1].split("?")[0] or Path(entry["dest"]).name
    fresh_dest = DATA_DIR / new_filename

    print(f"Hospital : {hospital}")
    print(f"Old URL  : {entry['url']}")
    print(f"New URL  : {new_url}")
    print(f"Dest     : {fresh_dest}")

    DATA_DIR.mkdir(exist_ok=True)
    result = download_file(new_url, fresh_dest, hospital)
    if result["status"] == "ok":
        print(f"  OK  {format_size(result['bytes'])}")
    else:
        print(f"  FAILED: {result['error']}")

    update_log_entry(log, old_dest_str, result)
    save_log(log)
    print("Log updated.")


def fix_errors():
    """
    For every failed log entry:
      - Re-scrape the API manifest to find a potentially updated download URL.
      - If the URL has changed, report it; then attempt the download with the new URL.
      - Updates the log in-place.
    """
    log = load_log()
    if not log:
        print("No download log found — nothing to fix.")
        return

    failed = [e for e in log.values() if e.get("status") != "ok"]
    if not failed:
        print("No failed entries in the download log.")
        return

    print(f"Found {len(failed)} failed entries. Re-fetching manifest to look for updated URLs...\n")
    hospitals = fetch_manifest()
    hospital_map = {h["name"]: h for h in hospitals}

    DATA_DIR.mkdir(exist_ok=True)
    for i, entry in enumerate(failed, 1):
        hospital = entry.get("hospital", "")
        old_url = entry["url"]
        dest = Path(entry["dest"])

        # Look up a fresh URL from the re-scraped manifest
        fresh_url = old_url
        fresh_dest = dest
        h = hospital_map.get(hospital)
        if h:
            chosen = pick_best_file(h.get("files", []))
            if chosen:
                fresh_url = chosen["url"]
                fresh_dest = DATA_DIR / chosen["filename"]

        print(f"[{i}/{len(failed)}] {hospital}")
        print(f"  Was: {entry.get('status')} — {entry.get('error')}")
        if fresh_url != old_url:
            print(f"  URL updated: {old_url}")
            print(f"           ->  {fresh_url}")
        else:
            print(f"  URL unchanged, retrying: {fresh_url}")

        result = download_file(fresh_url, fresh_dest, hospital)
        if result["status"] == "ok":
            print(f"  OK  {format_size(result['bytes'])}")
        else:
            print(f"  FAILED again: {result['error']}")

        update_log_entry(log, entry["dest"], result)
        time.sleep(0.5)

    save_log(log)

    ok = sum(1 for e in failed if log.get(e["dest"], {}).get("status") == "ok")
    print(f"\nFix complete: {ok}/{len(failed)} now succeeded. Log updated.")


def full_update():
    """
    Re-fetch the manifest and re-download every hospital, replacing existing files.
    """
    print("Full update: re-fetching manifest and re-downloading all hospitals...\n")
    hospitals = fetch_manifest()
    DATA_DIR.mkdir(exist_ok=True)

    log = load_log()

    seen_filenames: dict = {}
    plan = []
    skipped_no_file = 0
    for h in hospitals:
        chosen = pick_best_file(h.get("files", []))
        if not chosen:
            skipped_no_file += 1
            continue
        dest = dest_path(chosen["filename"], chosen["fileid"], seen_filenames)
        plan.append({
            "hospital": h["name"],
            "city": h["city"],
            "filename": dest.name,
            "url": chosen["url"],
            "suffix": chosen["filesuffix"],
            "size_bytes": int(chosen.get("size", 0)),
            "dest": dest,
        })

    print(f"Plan: {len(plan)} hospitals to re-download, {skipped_no_file} have no file.\n")

    for i, p in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {p['hospital']} ({p['city']})")
        print(f"  {p['suffix'].upper()}  {format_size(p['size_bytes']) if p['size_bytes'] else '?'}  {p['filename']}")
        if p["dest"].exists():
            p["dest"].unlink()
        result = download_file(p["url"], p["dest"], p["hospital"])
        if result["status"] == "ok":
            print(f"  OK  {format_size(result['bytes'])}")
        else:
            print(f"  FAILED: {result['error']}")
        update_log_entry(log, str(p["dest"]), result)
        time.sleep(0.5)

    save_log(log)

    ok = sum(1 for e in log.values() if e.get("status") == "ok")
    failed_count = sum(1 for e in log.values() if e.get("status") != "ok")
    print(f"\n{'=' * 60}")
    print(f"Full update done. Log: {ok} ok, {failed_count} failed. Saved to {LOG_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Download Indiana hospital price files")
    parser.add_argument("--refresh-manifest", action="store_true", help="Re-fetch hospital list from API")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N hospitals")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without downloading")
    parser.add_argument("--fix", action="store_true", help="Re-scrape API for updated URLs on failed entries, then retry")
    parser.add_argument("--full-update", action="store_true", help="Re-scrape API and re-download every hospital (replaces existing files)")
    parser.add_argument("--print-failed", action="store_true", help="Print all failed entries from the download log and exit")
    parser.add_argument("--set-url", nargs=2, metavar=("HOSPITAL_NAME", "URL"), help="Manually set a URL for a hospital (by name) and retry the download")
    args = parser.parse_args()

    if args.set_url:
        set_url(args.set_url[0], args.set_url[1])
        return

    if args.print_failed:
        log = load_log()
        failed = [e for e in log.values() if e.get("status") != "ok"]
        if not failed:
            print("No failed entries in the download log.")
        else:
            print(f"{len(failed)} failed entries:\n")
            for e in sorted(failed, key=lambda x: x.get("hospital", "")):
                print(f"  {e.get('hospital', '?')}")
                print(f"    status : {e.get('status')}")
                print(f"    error  : {e.get('error')}")
                print(f"    url    : {e.get('url')}")
                print(f"    time   : {e.get('timestamp')}")
        return

    if args.fix:
        fix_errors()
        return

    if args.full_update:
        full_update()
        return

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
    log = load_log()
    already_logged = {dest for dest, entry in log.items() if entry.get("status") == "ok"}

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

        update_log_entry(log, str(p["dest"]), result)

        # Small courtesy delay to avoid hammering hospital servers
        time.sleep(0.5)

    save_log(log)

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
