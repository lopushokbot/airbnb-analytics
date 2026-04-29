#!/usr/bin/env bash
# Build monthly rollup — read all weekly archive files for given month,
# compute median-per-date-per-listing, write data/archive/monthly/<YYYY-MM>.json.
# Run on the 1st of each month for the previous month.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
MONTH="${1:-$(date -v-1m +%Y-%m)}"

$PY <<PYEOF
import gzip
import json
import statistics
from pathlib import Path

ROOT = Path("$ROOT")
MONTH = "$MONTH"
year = MONTH[:4]
arc = ROOT / "data" / "archive" / year
if not arc.exists():
    print(f"  ! no archive folder for {year}, nothing to rollup")
    raise SystemExit(0)

snapshots = []
for p in sorted(arc.glob("*.json.gz")):
    with gzip.open(p, "rt") as g:
        d = json.load(g)
        if d.get("generated_at", "").startswith(MONTH):
            snapshots.append(d)
print(f"  found {len(snapshots)} weekly snapshots for {MONTH}")
if not snapshots:
    raise SystemExit(0)

# By listing → date → list of comp medians
agg = {}
for snap in snapshots:
    for slug, listing in snap.get("listings", {}).items():
        for d, info in listing.get("by_date", {}).items():
            agg.setdefault(slug, {}).setdefault(d, []).append(info.get("comp_median_host_aed") or 0)

rollup = {"month": MONTH, "snapshot_count": len(snapshots), "listings": {}}
for slug, dates in agg.items():
    per_date = {}
    for d, vals in dates.items():
        clean = [v for v in vals if v]
        if not clean:
            continue
        per_date[d] = {
            "comp_median_host_aed": round(statistics.median(clean), 0),
            "comp_min": min(clean),
            "comp_max": max(clean),
            "samples": len(clean),
        }
    rollup["listings"][slug] = {"by_date": per_date}

out_dir = ROOT / "data" / "archive" / "monthly"
out_dir.mkdir(parents=True, exist_ok=True)
out = out_dir / f"{MONTH}.json"
out.write_text(json.dumps(rollup, indent=2))
print(f"  → {out.relative_to(ROOT)}")
PYEOF
