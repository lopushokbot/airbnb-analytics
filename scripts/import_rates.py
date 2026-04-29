#!/usr/bin/env python3
"""Read each listing's pricing-rates.csv and expand the 16 seasonal periods
into per-day rates. Writes data/your_rates.json.

CSV schema (from existing listings):
    Period, Start, End, Days,
    YOU SET (host AED nightly) Now,
    YOU SET (host AED nightly) After 15+ reviews,
    GUEST SEES per night Now,
    GUEST SEES per night After 15+ reviews,
    Why this price
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LISTINGS_CFG = ROOT / "config" / "listings.json"
OUT_PATH = ROOT / "data" / "your_rates.json"


def parse_csv(csv_path: Path) -> list[dict]:
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "period": r["Period"],
                "start": r["Start"],
                "end": r["End"],
                "host_now": int(r["YOU SET (host AED nightly) Now"]),
                "host_after_reviews": int(r["YOU SET (host AED nightly) After 15+ reviews"]),
                "guest_now": int(r["GUEST SEES per night Now"]),
                "guest_after_reviews": int(r["GUEST SEES per night After 15+ reviews"]),
                "why": r["Why this price"],
            })
    return rows


def expand_to_days(periods: list[dict]) -> dict[str, dict]:
    by_date = {}
    for p in periods:
        d = datetime.strptime(p["start"], "%Y-%m-%d").date()
        end = datetime.strptime(p["end"], "%Y-%m-%d").date()
        while d <= end:
            by_date[d.isoformat()] = {
                "host_now": p["host_now"],
                "host_after_reviews": p["host_after_reviews"],
                "guest_now": p["guest_now"],
                "guest_after_reviews": p["guest_after_reviews"],
                "period": p["period"],
            }
            d += timedelta(days=1)
    return by_date


def main():
    cfg = json.loads(LISTINGS_CFG.read_text())
    out = {"generated_at": datetime.utcnow().isoformat() + "Z", "listings": {}}
    for listing in cfg["listings"]:
        slug = listing["slug"]
        csv_path = (ROOT / listing["pricing_csv_path"]).resolve()
        if not csv_path.exists():
            print(f"  ! pricing-rates.csv not found for {slug} at {csv_path}")
            continue
        periods = parse_csv(csv_path)
        by_date = expand_to_days(periods)
        out["listings"][slug] = {
            "name": listing["name"],
            "subtitle": listing["subtitle"],
            "periods": periods,
            "by_date": by_date,
        }
        print(f"  ✓ {slug}: {len(periods)} periods → {len(by_date)} days")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"  → {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
