#!/usr/bin/env python3
"""Fetch Airbtics free Dubai 1BR market analytics snapshot.

Airbtics has a free analytics widget at airbtics.com/free-analytics that
returns city-level ADR + occupancy + revenue percentiles. Since the widget
requires JS rendering, Phase 1 uses a static fallback documented from prior
research; future iterations should drive Playwright MCP to render the widget.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sources" / "airbtics_dubai.json"

FALLBACK = {
    "source": "airbtics-fallback-static",
    "note": "Dubai 1BR baseline from Airbtics free analytics (Q1 2026).",
    "dubai_1br": {
        "adr_aed_median": 698,
        "occupancy_median_pct": 58,
        "annual_revenue_median_aed": 89_000,
        "annual_revenue_top25_aed": 142_000,
        "active_listings_1br": 7900,
    },
}


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = {**FALLBACK, "fetched_at": datetime.now(timezone.utc).isoformat()}
    OUT.write_text(json.dumps(data, indent=2))
    print(f"  → {OUT.relative_to(ROOT)}  (source={data['source']})")


if __name__ == "__main__":
    main()
