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
    --refresh            Unattended update: find each hospital's current file via its cms-hpt.txt
                         (falling back to the last known URL), re-download only what changed, and
                         report new hospitals from the CMS hospital registry. No browser session needed.
    --session-id ID      ptsessionid for the hospitalpricingfiles.org API (or set HPT_SESSION_ID).
                         Only needed for API calls; with --refresh it also re-fetches the hospital list first.

Getting a session ID: the API only returns data for a session that passed the site's
Cloudflare check. Open hospitalpricingfiles.org in Chrome, pass the check, open DevTools >
Network, click Indiana, and copy the `ptsessionid` request header from the facility/search call.

The script:
  - Skips files already present in /data (resume-safe)
  - Prefers ZIP when both ZIP and CSV are available (smaller on disk)
  - Streams downloads so large files never fully load into RAM
  - Logs results to data/download_log.json (one entry per hospital, keyed by dest path)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from json_to_csv import convert_json_to_csv
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
REFERENCE_DIR = BASE_DIR / "reference"
MANIFEST_PATH = REFERENCE_DIR / "indiana_hospitals.json"
LOG_PATH = DATA_DIR / "download_log.json"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
# Some sites (e.g. Oaklawn, Porter-Starke) 403 any browser UA sent from urllib (it doesn't look
# like a real browser) but accept an honest client name; open_url() retries 403s with this.
FALLBACK_USER_AGENT = "HospitalPriceRefresh/1.0"

API_URL = "https://pts.patientrightsadvocatefiles.org/facility/search?search=&searchstate=IN"
API_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Origin": "https://hospitalpricingfiles.org",
    "Referer": "https://hospitalpricingfiles.org/",
    # These session headers are required but not secret — any valid browser visit generates them.
    # ptsessionid is only honored after the site's Cloudflare check; override it with
    # --session-id or the HPT_SESSION_ID env var (see module docstring).
    "ptsessionid": "01bc9e83-5df8-482e-bb77-d7caff019f8b",
    "sessionid": "5087494111016062868872034",
}
SESSION_ENV = "HPT_SESSION_ID"

# CMS Provider Data Catalog: "Hospital General Information" (all Medicare-certified hospitals)
CMS_HOSPITALS_URL = "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0"
STATE = "IN"
REFRESH_REPORT_PATH = DATA_DIR / "refresh_report.json"

# Prefer these formats in order when a hospital has multiple files
FORMAT_PRIORITY = ["zip", "csv", "json", "xlsx"]

DOWNLOAD_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
}

# Hand-checked price-file URLs (manifest hospital name -> URL) for hospitals whose cms-hpt.txt
# is missing, stale or unreadable. --refresh tries these first and falls back to the last known
# URL. Remove an entry once the hospital's cms-hpt.txt points at a working file again.
URL_OVERRIDES = {
    # cms-hpt.txt redirects to a JS app; file URL comes from its machine-readable-files API (2026-10)
    "REID HOSPITAL": "https://rhblobmobileapp.blob.core.windows.net/hospital-price-index/"
                     "350892672_reid-health_standardcharges-864a2273e04b4eb7818c40dfab066123.csv",
    # cms-hpt.txt entry is named "Physicians Medical Center LLC", so it isn't matched automatically
    "PMC REGIONAL HOSPITAL": "https://clariti-health.com/csp/clariti/machinereadable/v2/"
                             "205071967_Physicians-Medical-Center-LLC_standardcharges.csv",
    # Old link was an .xlsx; price page now has a CSV
    "MISSION BEHAVIORAL HEALTH": "https://www.mbhcares.com/getmedia/d7051a21-546f-4305-8234-02e82ef59667/"
                                 "MissionBehavioralHealth_MRF.csv",
    # Rebranded as Brentwood Behavioral Health (Deaconess); old domain redirects to the home page
    "BRENTWOOD SPRINGS": "https://www.brentwoodbehavioralhealth.com/getContentAsset/"
                         "6c9ab142-90e0-4c3a-9234-dfdcacf43c05/141d77fc-2e06-49eb-b14c-2ff58f5ce730/"
                         "brentwood_standardcharges.csv",
    # cms-hpt.txt URL is missing the /2026/08/ upload folder and 404s
    "NW INDIANA ER & HOSPITAL": "https://nwindianaer.com/wp-content/uploads/2026/08/"
                                "831287043_northwest-indiana-hospital-llc_standardcharges.csv",
    # Last known URL was missing the /2026/06/ upload folder
    "MARGARET MARY HEALTH": "https://www.mmhealth.org/wp-content/uploads/2026/06/"
                            "356067049_Margaret-Mary-Health_standardcharges-2.csv",
}

CHUNK_SIZE = 1024 * 512  # 512 KB chunks


def open_url(url: str, timeout: float, method: str | None = None):
    """urlopen with DOWNLOAD_HEADERS, retrying once with FALLBACK_USER_AGENT on HTTP 403."""
    try:
        return urllib.request.urlopen(
            urllib.request.Request(url, headers=DOWNLOAD_HEADERS, method=method), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code != 403:
            raise
        headers = dict(DOWNLOAD_HEADERS, **{"User-Agent": FALLBACK_USER_AGENT})
        return urllib.request.urlopen(
            urllib.request.Request(url, headers=headers, method=method), timeout=timeout)


def fetch_manifest() -> list:
    """Fetch the list of all Indiana hospitals from the API."""
    print(f"Fetching hospital manifest from API...")
    headers = dict(API_HEADERS)
    if os.environ.get(SESSION_ENV):
        headers["ptsessionid"] = os.environ[SESSION_ENV]
    req = urllib.request.Request(API_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if not data:
        # The API answers 200 with [] for an unverified or expired session. Never treat that as
        # "every hospital was removed" — keep the existing manifest untouched.
        sys.exit(
            "ERROR: The API returned 0 hospitals, which means the session ID is expired or invalid.\n"
            "Get a fresh ptsessionid (see the docstring at the top of this script) and pass it with\n"
            f"--session-id or the {SESSION_ENV} environment variable. The manifest was not changed."
        )
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
        with open_url(url, timeout=120) as r:
            total = int(r.headers.get("Content-Length", 0))
            # Kept so --refresh can skip files the server says haven't changed
            result["etag"] = r.headers.get("ETag")
            result["last_modified"] = r.headers.get("Last-Modified")
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
    result = make_readable(download_file(new_url, fresh_dest, hospital))
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

        result = make_readable(download_file(fresh_url, fresh_dest, hospital))
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

    done_this_run = set()  # several hospitals can share one file (same fileid -> same dest)
    for i, p in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {p['hospital']} ({p['city']})")
        print(f"  {p['suffix'].upper()}  {format_size(p['size_bytes']) if p['size_bytes'] else '?'}  {p['filename']}")
        if p["dest"] in done_this_run:
            print("  shared with a hospital already downloaded this run, skipped")
            continue
        if p["dest"].exists():
            p["dest"].unlink()
        result = make_readable(download_file(p["url"], p["dest"], p["hospital"]))
        done_this_run.add(p["dest"])
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


# ---------------------------------------------------------------------------
# --refresh: unattended update via cms-hpt.txt + the CMS hospital registry
# ---------------------------------------------------------------------------
#
# CMS requires every hospital to publish /cms-hpt.txt at its website root, listing
# location-name / mrf-url pairs. For each hospital in the manifest we look up its
# entry there; if it points somewhere new we switch to it, otherwise we keep using
# the last URL that worked. Only changed files are re-downloaded.

HPT_TIMEOUT = 20
NAME_STOPWORDS = {"HOSPITAL", "INC", "LLC", "THE", "OF", "AND", "AT"}


def site_host(url: str) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    return urllib.parse.urlparse(url).netloc.lower() or None


def hpt_hosts(site_url: str) -> list:
    """Hosts to try for cms-hpt.txt: the site itself, its www/non-www twin, and its root domain
    (hospital pages often live on subdomains like directory.franciscanhealth.org)."""
    host = site_host(site_url)
    if not host:
        return []
    bare = host[4:] if host.startswith("www.") else host
    root = ".".join(bare.split(".")[-2:])
    hosts = [host, bare, "www." + bare, root, "www." + root]
    return list(dict.fromkeys(hosts))


def fetch_hpt(host: str) -> str | None:
    try:
        with open_url(f"https://{host}/cms-hpt.txt", timeout=HPT_TIMEOUT) as r:
            text = r.read(1024 * 1024).decode("utf-8", "replace")
    except Exception:
        return None
    return text if "mrf-url" in text.lower() else None


def parse_hpt(text: str) -> list:
    """Parse cms-hpt.txt into [{'location-name': ..., 'mrf-url': ..., ...}, ...]."""
    entries, cur = [], {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z-]+)\s*:\s*(.*\S)", line)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if key == "location-name" and cur:
            entries.append(cur)
            cur = {}
        cur[key] = val
    if cur:
        entries.append(cur)
    return [e for e in entries if e.get("mrf-url")]


def name_tokens(name: str) -> set:
    name = name.upper().replace("SAINT ", "ST ").replace("ST. ", "ST ")
    return {t for t in re.findall(r"[A-Z0-9]+", name) if t not in NAME_STOPWORDS}


def url_basename(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1])


def url_stem(url_or_name: str) -> str:
    return Path(url_basename(url_or_name) if "://" in url_or_name else url_or_name).stem.lower()


def ein_prefix(filename: str) -> str | None:
    """CMS file naming is <EIN>_<name>_standardcharges.<ext>; return the EIN digits if present."""
    m = re.match(r"(\d[\d-]{7,10})_", filename)
    return m.group(1).replace("-", "") if m else None


def match_hpt_entry(hospital_name: str, known_url: str | None, entries: list):
    """Pick this hospital's entry from a cms-hpt.txt. Returns (entry, reason) or (None, None)."""
    if known_url:
        for e in entries:
            if e["mrf-url"] == known_url:
                return e, "same url"
        for e in entries:
            if url_stem(e["mrf-url"]) == url_stem(known_url):
                return e, "same filename"
    if len(entries) == 1:
        # A lone entry is only trusted if its name resembles ours (a system site may list a sibling facility)
        loc = name_tokens(entries[0].get("location-name", ""))
        mine = name_tokens(hospital_name)
        if loc and mine and len(loc & mine) / len(loc | mine) >= 0.34:
            return entries[0], "only entry"
        return entries[0], "only entry, name differs"
    # Name match. Tokens shared by every entry (the system name, e.g. PARKVIEW) carry no signal.
    per_entry = []
    for e in entries:
        parts = re.split(r"\s*(?:&|;|/)\s*", e.get("location-name", ""))
        per_entry.append([name_tokens(p) for p in parts if p])
    common = set.intersection(*[set().union(*parts) for parts in per_entry if parts]) if per_entry else set()
    target = name_tokens(hospital_name) - common
    if not target:
        return None, None
    scored = []
    for e, parts in zip(entries, per_entry):
        best = max((len(target & (p - common)) / len(target | (p - common)) for p in parts if p - common), default=0)
        scored.append((best, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1], "name"
    return None, None


def sniff_format(path: Path) -> str:
    """'zip', 'xlsx', 'json', 'html' or 'csv' based on file content — neither URLs (.ashx/.aspx)
    nor the site's filenames (some '.csv' links serve JSON or Excel) can be trusted."""
    with open(path, "rb") as f:
        head = f.read(4096).lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"PK"):
        return "xlsx" if b"[Content_Types].xml" in head or b"xl/" in head else "zip"
    if head[:1] in (b"{", b"["):
        return "json"
    if head[:1] == b"<":
        return "html"
    return "csv"


def make_readable(result: dict) -> dict:
    """After a successful download, make the file something extract/load can read:
    convert JSON to CSV, fix a .csv/.zip name that doesn't match the content, and
    reject HTML pages and Excel files (marks the result as an error and deletes the file)."""
    if result.get("status") != "ok":
        return result
    dest = Path(result["dest"])
    fmt = sniff_format(dest)
    if fmt == "json":
        raw = dest.with_name(dest.name + ".json.part")
        dest.replace(raw)
        csv_dest = dest.with_suffix(".csv")
        try:
            stats = convert_json_to_csv(raw, csv_dest)
            print(f"    converted JSON: {stats['items']} items -> {stats['rows']} CSV rows")
            raw.unlink()
            result["dest"], result["converted_from_json"] = str(csv_dest), True
        except Exception as e:
            raw.unlink()
            result["status"], result["error"] = "error", f"JSON conversion failed: {e}"
        return result
    if fmt in ("html", "xlsx"):
        dest.unlink()
        result["status"], result["error"] = "error", f"server returned {fmt}, not a CSV/ZIP/JSON price file"
        print(f"    ERROR: {result['error']}")
        return result
    if dest.suffix.lower() != f".{fmt}":
        fixed = dest.with_suffix(f".{fmt}")
        dest.replace(fixed)
        result["dest"] = str(fixed)
    return result


def server_unchanged(url: str, entry: dict) -> bool:
    """True if a HEAD request shows the file matches what we downloaded last time."""
    if not entry or entry.get("url") != url or not (entry.get("etag") or entry.get("last_modified")):
        return False
    try:
        with open_url(url, timeout=30, method="HEAD") as r:
            etag, lm = r.headers.get("ETag"), r.headers.get("Last-Modified")
            length = int(r.headers.get("Content-Length") or 0)
    except Exception:
        # Some servers (e.g. Ascension) 404 a HEAD but serve the GET; read just the headers.
        try:
            req = urllib.request.Request(url, headers=dict(DOWNLOAD_HEADERS, Range="bytes=0-0"))
            with urllib.request.urlopen(req, timeout=30) as r:
                etag, lm = r.headers.get("ETag"), r.headers.get("Last-Modified")
                total = (r.headers.get("Content-Range") or "").rpartition("/")[2]
                length = int(total) if r.status == 206 and total.isdigit() else \
                    int(r.headers.get("Content-Length") or 0) if r.status == 200 else 0
        except Exception:
            return False
    if entry.get("etag") and etag:
        return etag == entry["etag"]
    return bool(lm) and lm == entry.get("last_modified") and (not length or length == entry.get("bytes"))


def check_cms_registry(hospitals: list) -> list:
    """Hospitals in the CMS registry for STATE whose CCN isn't in our manifest (VA/DoD are exempt)."""
    body = json.dumps({
        "conditions": [{"property": "state", "value": STATE, "operator": "="}],
        "limit": 1500,
    }).encode()
    req = urllib.request.Request(CMS_HOSPITALS_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        results = json.loads(r.read())["results"]
    known = {h.get("ccn") for h in hospitals if h.get("ccn")}
    return [
        {"ccn": x["facility_id"], "name": x["facility_name"], "city": x.get("citytown"), "type": x.get("hospital_type")}
        for x in results
        if x["facility_id"] not in known
        and not re.search(r"veterans|department of defense", x.get("hospital_type") or "", re.I)
    ]


def refresh(dry_run: bool = False, limit: int | None = None, use_api: bool = False):
    hospitals = fetch_manifest() if use_api else load_manifest()
    if limit:
        hospitals = hospitals[:limit]
    log = load_log()
    last_ok = {e["hospital"]: (dest, e) for dest, e in log.items() if e.get("status") == "ok"}

    # 1. Fetch every cms-hpt.txt once
    host_lists = {h["id"]: hpt_hosts(h.get("url")) for h in hospitals}
    all_hosts = sorted({x for hosts in host_lists.values() for x in hosts})
    print(f"Checking cms-hpt.txt on {len(all_hosts)} candidate hosts...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        hpt_text = dict(zip(all_hosts, ex.map(fetch_hpt, all_hosts)))
    hpt_entries = {host: parse_hpt(t) for host, t in hpt_text.items() if t}
    print(f"  Found cms-hpt.txt on {len(hpt_entries)} hosts.\n")

    # 2. Decide a URL for each hospital
    plan, claimed_urls = [], set()
    for h in hospitals:
        name = h["name"]
        chosen = pick_best_file(h.get("files", []))
        prev_dest, prev = last_ok.get(name, (None, None))
        known_url = (chosen["url"] if chosen else None) if (use_api or not prev) else prev["url"]
        if known_url and chosen and known_url == chosen["url"]:
            known_dest = Path(prev_dest) if prev and prev["url"] == known_url else DATA_DIR / chosen["filename"]
        else:
            known_dest = Path(prev_dest) if prev_dest else None

        host = next((x for x in host_lists[h["id"]] if x in hpt_entries), None)
        entry, reason = match_hpt_entry(name, known_url, hpt_entries[host]) if host else (None, None)
        if entry:
            claimed_urls.add(entry["mrf-url"])
        hpt_url = entry["mrf-url"] if entry else None

        item = {"hospital": name, "city": h.get("city"), "hpt_host": host, "hpt_match": reason,
                "hpt_url": hpt_url, "known_url": known_url,
                "known_dest": str(known_dest) if known_dest else None, "prev_log_dest": prev_dest,
                "note": None}
        if hpt_url and hpt_url != known_url:
            new_ein, old_ein = ein_prefix(url_basename(hpt_url)), ein_prefix(known_dest.name if known_dest else "")
            if url_basename(hpt_url).lower().endswith((".xlsx", ".xls")):
                item["note"] = "cms-hpt.txt lists a newer file in a format the pipeline can't read; kept last known URL"
            elif reason == "only entry, name differs":
                item["note"] = f"cms-hpt.txt's only entry is '{entry.get('location-name')}', not this hospital; kept last known URL"
            elif new_ein and old_ein and new_ein != old_ein and reason != "same filename":
                item["note"] = f"cms-hpt.txt file has a different EIN ({new_ein} vs {old_ein}); not switching automatically"
            else:
                item["url"], item["source"] = hpt_url, "cms-hpt.txt (new url)"
        if name in URL_OVERRIDES:
            item["url"], item["source"] = URL_OVERRIDES[name], "manual override"
            item["note"] = None
        if "url" not in item:
            item["url"] = known_url
            item["source"] = "cms-hpt.txt" if entry and hpt_url == known_url else "last known url"
        if not item["url"]:
            item["source"], item["note"] = "none", item["note"] or "no known file and no cms-hpt.txt entry"
        plan.append(item)

    # 3. New hospitals / locations we don't track yet
    try:
        new_registry = check_cms_registry(hospitals if not limit else load_manifest())
    except Exception as e:
        new_registry = [{"error": f"CMS registry check failed: {e}"}]
    used_hosts = {p["hpt_host"] for p in plan if p["hpt_host"]}
    known_urls = {p["known_url"] for p in plan} | claimed_urls
    unlisted = [
        {"host": host, "location-name": e.get("location-name"), "mrf-url": e["mrf-url"]}
        for host in sorted(used_hosts) for e in hpt_entries[host] if e["mrf-url"] not in known_urls
    ]

    print(f"Sources: {dict(Counter(p['source'] for p in plan))}")
    print(f"New in CMS registry (not tracked): {len(new_registry)}")
    for x in new_registry:
        print(f"  {x}")
    # System-wide files list out-of-state sites too; only surface ones that look like Indiana
    # (city names are too ambiguous: Indiana has its own Madison, Richmond, Columbus...)
    in_state_words = {"INDIANA", "INDIANAPOLIS"}
    for x in unlisted:
        text = f"{x['location-name']} {url_basename(x['mrf-url'])}".upper().replace("-", " ").replace("_", " ")
        x["looks_in_state"] = any(re.search(rf"\b{re.escape(w)}\b", text) for w in in_state_words)
    likely = [x for x in unlisted if x["looks_in_state"]]
    print(f"cms-hpt.txt locations not matched to a tracked hospital: {len(unlisted)} "
          f"({len(likely)} look like {STATE}; all listed in the report)")
    for x in likely:
        print(f"  {x['location-name']}  ({x['host']})  {x['mrf-url']}")
    for p in plan:
        if p["note"]:
            print(f"  NOTE {p['hospital']}: {p['note']}")

    report = {"generated": datetime.now(timezone.utc).isoformat(), "dry_run": dry_run,
              "hospitals": plan, "new_in_cms_registry": new_registry, "unlisted_hpt_locations": unlisted}

    def write_report():
        with open(REFRESH_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

    if dry_run:
        write_report()
        print(f"\nDry run: nothing downloaded. Report: {REFRESH_REPORT_PATH}")
        return

    # 4. Download what changed
    DATA_DIR.mkdir(exist_ok=True)
    archive_dir = DATA_DIR / f"archive_{datetime.now():%Y-%m-%d}"
    dest_by_url: dict = {}
    todo = [p for p in plan if p["url"]]
    for i, p in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {p['hospital']}  ({p['source']})")
        if p["url"] in dest_by_url:  # e.g. two Parkview campuses share one file
            p["status"], p["dest"] = "shared file", dest_by_url[p["url"]]
            print("  shared with another hospital, skipped")
            continue
        prev_entry = log.get(p["prev_log_dest"]) if p["prev_log_dest"] else None
        if p["url"] == p["known_url"] and p["known_dest"] and Path(p["known_dest"]).exists() \
                and sniff_format(Path(p["known_dest"])) in ("csv", "zip") \
                and server_unchanged(p["url"], prev_entry):
            p["status"], p["dest"] = "unchanged", p["known_dest"]
            dest_by_url[p["url"]] = p["known_dest"]
            print("  unchanged")
            continue

        candidates = [p["url"]] + ([p["known_url"]] if p["known_url"] and p["known_url"] != p["url"] else [])
        for url in candidates:
            tmp = DATA_DIR / f".refresh_{i}.part"
            result = download_file(url, tmp, p["hospital"])
            if result["status"] != "ok":
                p["note"] = f"{url}: {result['error']}"
                continue
            fmt = sniff_format(tmp)
            if fmt == "json":
                # CMS JSON -> CMS tall CSV, so extract/load read it like any other hospital
                converted = tmp.with_suffix(".csv.part")
                try:
                    stats = convert_json_to_csv(tmp, converted)
                    print(f"    converted JSON: {stats['items']} items -> {stats['rows']} CSV rows")
                    tmp.unlink()
                    tmp, fmt = converted, "csv"
                    result["converted_from_json"] = True
                except Exception as e:
                    tmp.unlink()
                    converted.unlink(missing_ok=True)
                    result["status"], p["note"] = "error", f"{url}: JSON conversion failed: {e}"
                    print(f"    {p['note']}")
                    continue
            if fmt not in ("csv", "zip"):
                tmp.unlink()
                result["status"], p["note"] = "error", f"{url} returned {fmt}, which the pipeline can't read"
                print(f"    {p['note']}")
                continue
            if url == p["known_url"] and p["known_dest"]:
                dest = Path(p["known_dest"]).with_suffix(f".{fmt}")
            else:
                dest = DATA_DIR / f"{Path(url_basename(url)).stem}.{fmt}"
                if dest.exists() and str(dest) != p["known_dest"]:
                    dest = DATA_DIR / f"{dest.stem}__{re.sub(r'[^a-z0-9]+', '-', p['hospital'].lower()).strip('-')}.{fmt}"
            os.replace(tmp, dest)
            result["dest"] = str(dest)
            old = p["prev_log_dest"]
            if old and old != str(dest) and Path(old).exists():
                archive_dir.mkdir(exist_ok=True)
                os.replace(old, archive_dir / Path(old).name)  # keep one file per hospital in data/
            update_log_entry(log, old or str(dest), result)
            p["status"], p["dest"], p["url"] = "updated", str(dest), url
            dest_by_url[url] = str(dest)
            print(f"  OK  {format_size(result['bytes'])}  -> {dest.name}")
            break
        else:
            p["status"] = "failed (kept existing file)" if p["known_dest"] and Path(p["known_dest"]).exists() else "failed"
            print(f"  FAILED: {p['note']}")
        if i % 10 == 0:
            save_log(log)
            write_report()
        time.sleep(0.5)

    save_log(log)
    write_report()
    print(f"\n{'=' * 60}")
    print(f"Refresh done: {dict(Counter(p.get('status', 'skipped') for p in plan))}")
    print(f"Report: {REFRESH_REPORT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Download Indiana hospital price files")
    parser.add_argument("--refresh-manifest", action="store_true", help="Re-fetch hospital list from API")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N hospitals")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without downloading")
    parser.add_argument("--fix", action="store_true", help="Re-scrape API for updated URLs on failed entries, then retry")
    parser.add_argument("--full-update", action="store_true", help="Re-scrape API and re-download every hospital (replaces existing files)")
    parser.add_argument("--print-failed", action="store_true", help="Print all failed entries from the download log and exit")
    parser.add_argument("--set-url", nargs=2, metavar=("HOSPITAL_NAME", "URL"), help="Manually set a URL for a hospital (by name) and retry the download")
    parser.add_argument("--refresh", action="store_true", help="Unattended update via cms-hpt.txt + last known URLs; downloads only changed files")
    parser.add_argument("--session-id", help=f"ptsessionid for the API (or set {SESSION_ENV}); with --refresh, re-fetches the hospital list first")
    args = parser.parse_args()

    if args.session_id:
        os.environ[SESSION_ENV] = args.session_id

    if args.refresh:
        refresh(dry_run=args.dry_run, limit=args.limit, use_api=bool(args.session_id))
        return

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

        result = make_readable(download_file(p["url"], p["dest"], p["hospital"]))
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
