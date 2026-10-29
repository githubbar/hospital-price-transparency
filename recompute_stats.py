"""
Recompute each procedure's min/max/avg in existing databases so they leave out unusual prices
(see prices/outliers.py), without rebuilding them from the hospital files.

Prices are read from the full database; the stats are written to it and to any other databases given,
such as the aggregate one the search page reads (it has no prices of its own).

    python recompute_stats.py in_full.sqlite3 in_aggregate.sqlite3
"""

import argparse
import sqlite3
from collections import defaultdict

from prices.outliers import robust_stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('full_db', help='Database with the prices table')
    parser.add_argument('other_dbs', nargs='*', help='More databases whose procedures get the same stats')
    args = parser.parse_args()

    print(f"Reading prices from {args.full_db} ...")
    values = defaultdict(list)
    with sqlite3.connect(args.full_db) as conn:
        for proc_id, price in conn.execute("SELECT procedure_id, price FROM prices WHERE price IS NOT NULL"):
            values[proc_id].append(price)

    stats = {proc_id: robust_stats(v) for proc_id, v in values.items()}
    rows = [(s['min'], s['max'], s['avg'], s['count'], proc_id) for proc_id, s in stats.items() if s]
    changed = sum(1 for s in stats.values() if s and s['unusual'])
    print(f"{len(rows)} procedures; {changed} had unusual prices left out of min/max/avg.")

    for db in [args.full_db] + args.other_dbs:
        with sqlite3.connect(db) as conn:
            conn.executemany(
                "UPDATE procedures SET stats_min = ?, stats_max = ?, stats_avg = ?, stats_count = ? WHERE id = ?", rows)
            print(f"  {db}: updated {conn.total_changes} procedures")


if __name__ == '__main__':
    main()
