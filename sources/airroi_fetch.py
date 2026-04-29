#!/usr/bin/env python3
"""Fetch AirROI public Dubai market metrics.

AirROI publishes a public city report at https://www.airroi.com/data/cities/.
The exact JSON endpoint is undocumented and changes; this fetcher tries the
known patterns in order, parses what it can, and writes a snapshot to
data/sources/airroi_dubai.json. Failures are logged but never fail the whole
refresh — macro data is supplementary, not load-bearing.

Output schema:
  {
    fetched_at: ISO,
    source: "airroi" | "airroi-fallback-static",
    dubai: {adr_aed_median, adr_aed_top25, occupancy_median, occupancy_top25, ...},
    neighborhoods: {
      "Jumeirah 1":   {adr_aed_median, occupancy_median, listing_count},
      "Umm Suqeim 3": {adr_aed_median, occupancy_median, listing_count},
    }
  }

Phase 1 fallback: if live fetch fails, write a snapshot from the values we
already documented in workspace/airbnb-port-la-mer/CLAUDE.md (AirROI deep-dive,
2026-04-29). This keeps the macro panel populated even when AirROI changes its
API or geo-blocks.
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sources" / "airroi_dubai.json"

# Documented baseline from AirROI 2026-04-29 deep dive (per port-la-mer/RUNBOOK).
# USD → AED at 3.6725.
USD_TO_AED = 3.6725
FALLBACK = {
    "fetched_at": None,  # filled at write time
    "source": "airroi-fallback-static",
    "note": "Baseline from AirROI 2026-04-29 deep dive (port-la-mer RUNBOOK). "
            "Replace by implementing live scrape when AirROI exposes a stable endpoint.",
    "dubai": {
        "adr_aed_median": round(217 * USD_TO_AED, 0),  # ~AED 797
        "adr_aed_top25": round(290 * USD_TO_AED, 0),
        "adr_aed_peak_dec": round(387 * USD_TO_AED, 0),  # ~AED 1,420
        "adr_aed_low_aug": round(215 * USD_TO_AED, 0),   # ~AED 789
        "occupancy_median_pct": 43,
        "occupancy_top25_pct": 69,
        "occupancy_top10_pct": 84,
        "annual_revenue_median_aed": 95_000,
    },
    "neighborhoods": {
        "Jumeirah 1": {
            "adr_aed_median": 720,
            "occupancy_median_pct": 45,
            "listing_count_estimate": 280,
            "note": "Port de la Mer comp area",
        },
        "Umm Suqeim 3": {
            "adr_aed_median": 850,
            "occupancy_median_pct": 48,
            "listing_count_estimate": 95,
            "note": "MJL comp area, smaller pool, family-skew premium",
        },
    },
}

ENDPOINTS = [
    # If AirROI ever exposes a public JSON, slot it in here. For now both 404.
    "https://www.airroi.com/api/v2/markets/dubai/summary",
    "https://www.airroi.com/data/cities/dubai/api/summary",
]


def try_fetch():
    for url in ENDPOINTS:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 airbnb-analytics-fetch",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                # Return as-is in a "raw" envelope; mapping into our schema would
                # require knowing the actual keys. Mark for manual mapping later.
                return {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "source": "airroi-live",
                    "endpoint": url,
                    "raw": data,
                    "_note": "Live AirROI response. Map fields into dubai/neighborhoods on next refresh.",
                }
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            continue
    return None


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = try_fetch()
    if not data:
        data = {**FALLBACK, "fetched_at": datetime.now(timezone.utc).isoformat()}
        print("  ! AirROI live fetch failed; using documented fallback snapshot")
    OUT.write_text(json.dumps(data, indent=2))
    print(f"  → {OUT.relative_to(ROOT)}  (source={data['source']})")


if __name__ == "__main__":
    main()
