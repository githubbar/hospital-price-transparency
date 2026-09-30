"""
Script: generate_payers_and_plans.py

Builds prices/static_payers_and_plans.json (the Insurer filter in the search sidebar)
from the distinct payer/plan names in the full SQLite database.

Raw payer names are mapped to canonical payers and product lines using the CSV
rules in reference/payers/ (see prices/payer_normalization.py). Payer/plan pairs
that no rule recognises are written to an unmatched report, sorted by how many
price rows they cover, so the alias list can be extended after each data refresh.

Usage:
    python generate_payers_and_plans.py [DB] [--report PATH]

Arguments:
    DB              SQLite database to read (default: in_full.sqlite3)
    --report PATH   Where to write the unmatched-payer CSV
                    (default: scratch/payer_unmatched_report.csv)
"""
import argparse
import csv
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict

from prices.payer_normalization import (
    CATEGORY_LABELS, CATEGORY_ORDER, UNMATCHED_PAYER, PayerNormalizer, plan_key,
)

OUTPUT_PATH = os.path.join('prices', 'static_payers_and_plans.json')
EXCLUDED_PAYERS = {'gross', 'gross charge', 'negotiated dollar'}
GENERIC_DISPLAYS = {'standard', 'unknown', 'none', 'default', ''}


def clean_name(s):
    if not s:
        return ''
    s = re.sub(r'\[[^\]]*\]', ' ', s)               # bracketed payer IDs, e.g. "[2000]"
    s = re.sub(r'\b(19|20)\d{6}\b', ' ', s)         # contract date stamps, e.g. "20120101"
    s = re.sub(r'^\s*\d{2,5}\s+(?=[A-Za-z])', '', s)  # leading contract numbers, e.g. "9063 MEDICARE ..."
    s = s.replace('_', ' ').replace('-', ' ').replace('/', ' / ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s.title()


def normalize_for_matching(name):
    """Collapse near-duplicate plan labels (filler words, punctuation) into one key."""
    s = re.sub(r'[^\w\s]', ' ', name.lower())
    fillers = {
        'commercial', 'managed', 'network', 'plan', 'plans', 'standard', 'all',
        'healthcare', 'facility', 'provider', 'choice', 'traditional', 'indemnity',
        'contracted', 'negotiated', 'preferred', 'other',
    }
    words = [w for w in s.split() if w not in fillers] or s.split()
    return ' '.join(words)


def build_display(payer_raw, plan_raw, canonical):
    payer_display, plan_display = clean_name(payer_raw), clean_name(plan_raw)

    if not plan_display or plan_display.lower() in GENERIC_DISPLAYS:
        display = payer_display
    elif payer_display.lower() == plan_display.lower() or plan_display.lower() in payer_display.lower():
        display = payer_display
    elif payer_display.lower() in plan_display.lower():
        display = plan_display
    else:
        display = f'{payer_display} - {plan_display}'

    # Drop words that just repeat the parent group name ("Aetna - Aetna PPO" -> "PPO")
    for word in canonical.strip_words:
        display = re.sub(rf'(?<!\w){re.escape(word)}(?!\w)', '', display, flags=re.IGNORECASE)

    display = re.sub(r'\(\s*\)', '', display)
    display = re.sub(r'\s*-\s*', ' - ', display)
    display = re.sub(r'^[\s\-/,]+|[\s\-/,]+$', '', display)
    display = re.sub(r'\s+', ' ', display).strip()

    # Word-level de-duplication ("Ambetter Ambetter" -> "Ambetter")
    seen, words = set(), []
    for w in display.split():
        if w == '-' or w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    display = re.sub(r'^[\s\-]+|[\s\-]+$', '', ' '.join(words)).strip()

    if not display or display.lower() in GENERIC_DISPLAYS:
        display = 'Standard / All Plans'
    return display


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('db', nargs='?', default='in_full.sqlite3')
    parser.add_argument('--report', default=os.path.join('scratch', 'payer_unmatched_report.csv'))
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f'Error: {args.db} not found!')
        return

    normalizer = PayerNormalizer()
    conn = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    print(f'Reading payer/plan pairs and price-row counts from {args.db} (about a minute)...')
    rows = conn.execute("""
        SELECT py.name, pl.name, COALESCE(x.n, 0)
        FROM plans pl
        JOIN payers py ON py.id = pl.payer_id
        LEFT JOIN (SELECT plan_id, COUNT(*) AS n FROM prices GROUP BY plan_id) x ON x.plan_id = pl.id
        WHERE py.name IS NOT NULL AND py.name != ''
    """).fetchall()
    print(f'Found {len(rows)} distinct payer/plan pairs.')

    # canonical name -> (product_line, match_key) -> {'displays': [...], 'raw_pairs': [...]}
    grouped = defaultdict(lambda: defaultdict(lambda: {'displays': [], 'raw_pairs': []}))
    canonicals = {}
    row_totals = Counter()
    unmatched = []

    for payer_raw, plan_raw, n_rows in rows:
        if payer_raw.strip().lower() in EXCLUDED_PAYERS:
            continue
        match = normalizer.match(payer_raw, plan_raw)
        canonical = match.canonical
        canonicals[canonical.name] = canonical
        row_totals[canonical.name] += n_rows
        if canonical.name == UNMATCHED_PAYER:
            unmatched.append((n_rows, payer_raw, plan_raw, match.product_line))

        display = build_display(payer_raw, plan_raw, canonical)
        bucket = grouped[canonical.name][(match.product_line, normalize_for_matching(display))]
        bucket['displays'].append(display)
        bucket['raw_pairs'].append((payer_raw, plan_raw))

    output_list = []
    for name, buckets in grouped.items():
        canonical = canonicals[name]
        plans_list = []
        for (product_line, _), data in buckets.items():
            # Most common display wins; ties go to the longest (most descriptive) label
            display = sorted(Counter(data['displays']).most_common(), key=lambda x: (-x[1], -len(x[0])))[0][0]
            plans_list.append({
                # Raw names can contain ',' and '|', so keep pairs structured
                'key': plan_key(data['raw_pairs']),
                'raw_pairs': [[p, pl] for p, pl in data['raw_pairs']],
                'display': display,
                'product_line': product_line,
            })
        plans_list.sort(key=lambda p: p['display'].lower())
        output_list.append({
            'parent_name': name,
            'category': canonical.category,
            'category_label': CATEGORY_LABELS[canonical.category],
            'plans': plans_list,
        })

    output_list.sort(key=lambda g: (
        CATEGORY_ORDER.index(g['category']),
        canonicals[g['parent_name']].sort_order,
    ))

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output_list, f, indent=2)

    total_rows = sum(row_totals.values()) or 1
    unmatched.sort(reverse=True)
    os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)
    with open(args.report, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['price_rows', 'payer', 'plan', 'product_line'])
        writer.writerows(unmatched)

    print(f'Wrote {OUTPUT_PATH}: {len(output_list)} payer groups, '
          f'{sum(len(g["plans"]) for g in output_list)} plan entries.')
    print(f'Matched {1 - row_totals[UNMATCHED_PAYER] / total_rows:.1%} of price rows to a named payer group.')
    print(f'{len(unmatched)} unmatched payer/plan pairs written to {args.report} '
          f'(add rules to reference/payers/payer_aliases.csv, then re-run).')


if __name__ == '__main__':
    main()
