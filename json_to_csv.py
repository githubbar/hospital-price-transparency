"""
Script: json_to_csv.py

Converts a CMS hospital price transparency JSON file (schema v2.x / v3.x) into the CMS
"tall" CSV layout, which extract_shoppable.py and load_to_sqlite.py already read.
Streams the JSON with ijson, so multi-GB files never fully load into RAM.

Usage:
    python json_to_csv.py INPUT.json [OUTPUT.csv]

download_indiana.py --refresh calls convert_json_to_csv() automatically when a hospital's
file turns out to be JSON.

CSV layout (CMS tall template):
    row 1: hospital_name, last_updated_on, version, location_name, hospital_address, license_number|<state>
    row 2: their values
    row 3: description, code|1, code|1|type ... code|6|type, setting, standard_charge|gross,
           standard_charge|discounted_cash, standard_charge|min, standard_charge|max,
           payer_name, plan_name, standard_charge|negotiated_dollar, ... additional_generic_notes
    then one row per (item, charge setting, payer); items with no payers get one row with blank payer.
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path

import ijson

MAX_CODES = 6  # parse_csv_into_map reads code|1 .. code|6

META_KEYS = ["hospital_name", "last_updated_on", "version", "location_name", "hospital_address"]

HEADER = (
    ["description"]
    + [col for i in range(1, MAX_CODES + 1) for col in (f"code|{i}", f"code|{i}|type")]
    + [
        "modifiers",
        "setting",
        "drug_unit_of_measurement",
        "drug_type_of_measurement",
        "standard_charge|gross",
        "standard_charge|discounted_cash",
        "standard_charge|min",
        "standard_charge|max",
        "payer_name",
        "plan_name",
        "standard_charge|negotiated_dollar",
        "standard_charge|negotiated_percentage",
        "standard_charge|negotiated_algorithm",
        "estimated_amount",
        "standard_charge|methodology",
        "additional_payer_notes",
        "additional_generic_notes",
    ]
)


def _s(value) -> str:
    """Render a JSON scalar for CSV (ijson yields Decimal for numbers)."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else str(value)
    if isinstance(value, list):
        return " | ".join(_s(v) for v in value)
    return str(value)


def _open_json(path: Path):
    """Open for ijson, skipping a UTF-8 byte-order mark (some hospitals' files start with one)."""
    f = open(path, "rb")
    if f.read(3) != b"\xef\xbb\xbf":
        f.seek(0)
    return f


def read_metadata(path: Path) -> dict:
    """Top-level scalar fields. Usually they precede standard_charge_information, so we stop there;
    if hospital_name comes later we keep scanning (slower, but still streaming)."""
    meta = {"location_name": [], "hospital_address": []}
    license_state = license_number = None
    with _open_json(path) as f:
        for prefix, event, value in ijson.parse(f):
            if prefix == "" and event == "map_key" and value == "standard_charge_information" and meta.get("hospital_name"):
                break
            if prefix.startswith("standard_charge_information") or prefix.startswith("modifier_information"):
                continue
            if event in ("string", "number"):
                if prefix in ("hospital_name", "last_updated_on", "version"):
                    meta[prefix] = _s(value)
                elif prefix in ("location_name.item", "hospital_location.item"):  # v3 / v2 name
                    meta["location_name"].append(_s(value))
                elif prefix == "hospital_address.item":
                    meta["hospital_address"].append(_s(value))
                elif prefix == "license_information.license_number":
                    license_number = _s(value)
                elif prefix == "license_information.state":
                    license_state = _s(value)
    meta["location_name"] = " | ".join(meta["location_name"])
    meta["hospital_address"] = " | ".join(meta["hospital_address"])
    meta["license_col"] = f"license_number|{license_state or 'NA'}"
    meta["license_number"] = license_number or ""
    return meta


def item_rows(item: dict):
    codes = [(_s(c.get("code")), _s(c.get("type")).upper()) for c in item.get("code_information") or [] if c.get("code")]
    code_cells = []
    for i in range(MAX_CODES):
        code_cells += list(codes[i]) if i < len(codes) else ["", ""]
    drug = item.get("drug_information") or {}
    base = [_s(item.get("description"))] + code_cells

    for charge in item.get("standard_charges") or []:
        common = base + [
            _s(charge.get("modifiers") or charge.get("modifier_code")),
            _s(charge.get("setting")),
            _s(drug.get("unit")),
            _s(drug.get("type")),
            _s(charge.get("gross_charge")),
            _s(charge.get("discounted_cash")),
            _s(charge.get("minimum")),
            _s(charge.get("maximum")),
        ]
        notes = _s(charge.get("additional_generic_notes"))
        payers = charge.get("payers_information") or []
        if not payers:
            yield common + [""] * 8 + [notes]
            continue
        for p in payers:
            yield common + [
                _s(p.get("payer_name")),
                _s(p.get("plan_name")),
                _s(p.get("standard_charge_dollar")),
                _s(p.get("standard_charge_percentage")),
                _s(p.get("standard_charge_algorithm")),
                # v2 calls it estimated_amount; v3 replaced it with median_amount
                _s(p.get("estimated_amount") if p.get("estimated_amount") is not None else p.get("median_amount")),
                _s(p.get("methodology")),
                _s(p.get("additional_payer_notes")),
                notes,
            ]


def convert_json_to_csv(json_path, csv_path) -> dict:
    """Convert json_path to a CMS tall CSV at csv_path. Returns {'items': n, 'rows': n, 'hospital_name': ...}."""
    json_path, csv_path = Path(json_path), Path(csv_path)
    meta = read_metadata(json_path)
    if not meta.get("hospital_name"):
        raise ValueError(f"{json_path.name}: no hospital_name — not a CMS price transparency JSON file")

    items = rows = 0
    tmp = csv_path.with_name(csv_path.name + ".tmp")
    with _open_json(json_path) as src, open(tmp, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(META_KEYS + [meta["license_col"]])
        w.writerow([meta.get(k, "") for k in META_KEYS] + [meta["license_number"]])
        w.writerow(HEADER)
        for item in ijson.items(src, "standard_charge_information.item"):
            items += 1
            for row in item_rows(item):
                w.writerow(row)
                rows += 1
    if items == 0:
        tmp.unlink()
        raise ValueError(f"{json_path.name}: no standard_charge_information items found")
    tmp.replace(csv_path)
    return {"hospital_name": meta["hospital_name"], "items": items, "rows": rows}


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) == 3 else src.with_suffix(".csv")
    stats = convert_json_to_csv(src, dst)
    print(f"{stats['hospital_name']}: {stats['items']} items -> {stats['rows']} rows in {dst}")


if __name__ == "__main__":
    main()
