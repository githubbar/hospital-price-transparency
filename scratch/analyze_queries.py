import re
from datetime import datetime

def parse_time(ts_str):
    # E.g. 2026-05-28T11:50:26.291104Z
    # Strip any trailing 'Z' and parse
    ts_str = ts_str.rstrip('Z')
    if '.' in ts_str:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f")
    else:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")

def analyze_logs(log_path):
    print(f"\n==========================================")
    print(f"Analyzing: {log_path}")
    print(f"==========================================\n")
    
    with open(log_path, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()
        
    startups = []
    attaches = []
    spellchecks = []
    warnings = []
    errors = []
    all_events = []
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line: continue
        parts = line.split(None, 2)
        if len(parts) < 3: continue
        ts_str = parts[0]
        payload = parts[2]
        
        # We try to parse timestamp
        try:
            ts = parse_time(ts_str)
        except Exception:
            continue
            
        event = {'ts': ts, 'ts_str': ts_str, 'payload': payload, 'line': i+1}
        all_events.append(event)
        
        if "Starting new instance" in payload:
            startups.append(event)
        elif "[SQLite Attach]" in payload:
            attaches.append(event)
        elif "[spellcheck]" in payload:
            spellchecks.append(event)
        elif "warning" in payload.lower():
            warnings.append(event)
        elif "error" in payload.lower():
            errors.append(event)
            
    print(f"Total valid log lines with timestamps: {len(all_events)}")
    print(f"Startup events: {len(startups)}")
    print(f"SQLite attach events: {len(attaches)}")
    print(f"Spellcheck events: {len(spellchecks)}")
    print(f"Warning events: {len(warnings)}")
    print(f"Error events: {len(errors)}")
    
    print("\n--- Startup Events ---")
    for s in startups:
        print(f"[{s['ts_str']}] {s['payload']}")
        
    print("\n--- SQLite Attach Events ---")
    for a in attaches:
        print(f"[{a['ts_str']}] {a['payload']}")
        
    print("\n--- Spellcheck Events ---")
    for sp in spellchecks:
        print(f"[{sp['ts_str']}] {sp['payload']}")
        
    # Let's see if there are any actual search queries or words
    print("\n--- Checking for spellchecks around SQLite Attach to find Cold Start searches ---")
    # For each SQLite attach, find the preceding Startup and succeeding Spellcheck
    for a in attaches:
        # Find the startup just before this attach
        prev_startup = None
        for s in startups:
            if s['ts'] < a['ts']:
                prev_startup = s
            else:
                break
                
        # Find the spellcheck just after this attach (within 2 minutes)
        next_spellcheck = None
        for sp in spellchecks:
            if sp['ts'] > a['ts'] and (sp['ts'] - a['ts']).total_seconds() < 120:
                next_spellcheck = sp
                break
                
        print(f"\nAttach: [{a['ts_str']}] {a['payload']}")
        if prev_startup:
            diff_start = (a['ts'] - prev_startup['ts']).total_seconds()
            print(f"  Preceding Startup: [{prev_startup['ts_str']}] (diff: {diff_start:.2f}s)")
        if next_spellcheck:
            diff_sp = (next_spellcheck['ts'] - a['ts']).total_seconds()
            print(f"  Succeeding Spellcheck: [{next_spellcheck['ts_str']}] {next_spellcheck['payload']} (diff: {diff_sp:.2f}s)")
            if prev_startup:
                total_cold = (next_spellcheck['ts'] - prev_startup['ts']).total_seconds()
                print(f"  Total Cold Start Search Time (Startup -> Spellcheck): {total_cold:.2f}s")
                
    # Search for other queries or words like "mri" in all log messages
    mri_related = []
    for event in all_events:
        if any(w in event['payload'].lower() for w in ['mri', 'magnetic', 'resonance', 'imaging', 'procedure']):
            # Filter out GCSFuse setup lines
            if "gcsfuse" in event['payload'].lower() or "cache" in event['payload'].lower():
                continue
            mri_related.append(event)
            
    print(f"\nMRI/imaging related logs count: {len(mri_related)}")
    for mr in mri_related[:30]:
        print(f"  [{mr['ts_str']}] {mr['payload']}")

analyze_logs(r"x:\Hospital Price Transparency\scratch\server_logs.txt")
analyze_logs(r"x:\Hospital Price Transparency\scratch\server_logs_full.txt")
