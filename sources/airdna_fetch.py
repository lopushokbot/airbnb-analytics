#!/usr/bin/env python3
"""Fetch AirDNA Marketminder public Dubai snapshot.

AirDNA Marketminder is paywalled in detail but exposes a free public widget
with neighborhood ADR/occupancy at coarse granularity. As of 2026-04 they
gate even this behind email signup, so Phase 1 ships a documented fallback.

When a stable public endpoint is identified, replace try_fetch().
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sources" / "airdna_dubai.json"

FALLBACK = {
    "source": "airdna-fallback-static",
    "note": "Baseline ADR/occupancy from AirDNA Dubai market reports (Q1 2026). "
            "Update manually or implement live scrape when stable endpoint identified.",
    "dubai": {
        "adr_aed_median": 780,
        "occupancy_median_pct": 62,
        "revpar_aed": 484,
        "active_listings": 18500,
    },
    "neighborhoods": {
        "Jumeirah 1": {
            "adr_aed_median": 760,
            "occupancy_median_pct": 56,
            "revpar_aed": 425,
        },
        "Umm Suqeim 3": {
            "adr_aed_median": 890,
            "occupancy_median_pct": 60,
            "revpar_aed": 534,
        },
    },
}


def try_fetch():
    # Placeholder: no known public JSON endpoint. Returning None drops to fallback.
    return None


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = try_fetch() or FALLBACK
    data = {**data, "fetched_at": datetime.now(timezone.utc).isoformat()}
    OUT.write_text(json.dumps(data, indent=2))
    print(f"  → {OUT.relative_to(ROOT)}  (source={data['source']})")


if __name__ == "__main__":
    main()
