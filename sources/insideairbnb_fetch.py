#!/usr/bin/env python3
"""Fetch Dubai 1BR comp distributions from Inside Airbnb public datasets.

Inside Airbnb (insideairbnb.com) publishes monthly CSV snapshots of every
active Airbnb listing. We download the Dubai listings CSV, filter to
1BR entire-home listings in each target neighborhood, and extract a price
distribution — giving real, independent comp data that can cross-check
Playwright MCP scrapes.

Output: data/sources/insideairbnb_dubai.json
Schema:
  {
    fetched_at, source, snapshot_date,
    neighborhoods: {
      "Jumeirah 1": {
        listing_count, price_aed_median, price_aed_p25, price_aed_p75,
        price_aed_min, price_aed_max, sample_listings: [{id,name,price,url}]
      },
      "Umm Suqeim 3": { ... }
    }
  }

Note: Inside Airbnb prices are per-night nightly rates (host AED equivalent),
NOT guest totals — so they map directly to comp_median_host_aed.

Run this on Sema's Mac; the domain is geo-accessible from UAE/EU residential
IPs. Will return 403 from cloud/datacenter IPs.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import statistics
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sources" / "insideairbnb_dubai.json"
CFG = json.loads((ROOT / "config" / "listings.json").read_text())

# Inside Airbnb Dubai snapshot dates — newest first. Update when they publish
# a new snapshot (check insideairbnb.com/get-the-data/).
SNAPSHOT_DATES = [
    "2025-03-17",
    "2024-12-16",
    "2024-09-11",
    "2024-06-10",
]

BASE_URL = "http://data.insideairbnb.com/united-arab-emirates/dubai/dubai"

# Neighborhood aliases Inside Airbnb uses for our target areas
NEIGHBORHOOD_ALIASES = {
    "Jumeirah 1": [
        "Jumeirah 1", "Jumeirah", "Port de la Mer", "La Mer",
        "Jumeirah 1 - La Mer", "Jumeirah 1 - Bvlgari",
    ],
    "Umm Suqeim 3": [
        "Umm Suqeim 3", "Umm Suqeim", "Madinat Jumeirah Living",
        "Madinat Jumeirah", "Umm Al Sheif", "Al Barsha South 1",
    ],
}

AED_PER_USD = 3.6725


def percentile(data: list[float], p: float) -> float | None:
    if not data:
        return None
    s = sorted(data)
    if len(s) == 1:
        return s[0]
    idx = (len(s) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def parse_price(price_str: str) -> float | None:
    """Parse '$150' / 'AED 550' / '550' → host AED nightly rate."""
    if not price_str:
        return None
    s = price_str.strip().replace(",", "")
    # Remove currency symbols
    for prefix in ("$", "AED ", "USD ", "£", "€"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    try:
        val = float(s)
    except ValueError:
        return None
    if val < 50 or val > 50_000:
        return None
    # Inside Airbnb Dubai prices are in USD when the site is in USD mode
    # but often in the listing's local currency. We treat values > 500 as
    # already AED and < 500 as USD (heuristic — Dubai 1BR rarely < 500 AED).
    if val < 500:
        val = round(val * AED_PER_USD, 0)
    return val


def fetch_listings_csv(snapshot_date: str) -> bytes | None:
    url = f"{BASE_URL}/{snapshot_date}/data/listings.csv.gz"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36",
            "Accept-Encoding": "gzip, deflate",
            "Accept": "*/*",
            "Referer": "https://insideairbnb.com/",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"  ! {snapshot_date}: {e}")
        return None


def parse_csv_gz(raw: bytes) -> list[dict]:
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        return list(reader)


def filter_comps(rows: list[dict]) -> dict[str, list[dict]]:
    """Filter to 1BR entire-home listings per target neighborhood."""
    results: dict[str, list[dict]] = {nb: [] for nb in NEIGHBORHOOD_ALIASES}
    for row in rows:
        room_type = row.get("room_type", "").strip()
        if room_type not in ("Entire home/apt", "Entire rental unit", "Entire condo"):
            continue
        bedrooms_raw = row.get("bedrooms", "").strip()
        try:
            bedrooms = int(float(bedrooms_raw)) if bedrooms_raw else 1
        except ValueError:
            bedrooms = 1
        if bedrooms != 1:
            continue

        price = parse_price(row.get("price", ""))
        if not price:
            continue

        nb_raw = row.get("neighbourhood_cleansed", row.get("neighbourhood", "")).strip()
        listing_id = row.get("id", "").strip()
        name = row.get("name", "")[:60]
        url = f"https://www.airbnb.com/rooms/{listing_id}" if listing_id else ""

        for nb, aliases in NEIGHBORHOOD_ALIASES.items():
            if any(alias.lower() in nb_raw.lower() or nb_raw.lower() in alias.lower()
                   for alias in aliases):
                results[nb].append({"id": listing_id, "name": name, "price": price, "url": url})
                break

    return results


def summarise(comps: list[dict]) -> dict | None:
    if not comps:
        return None
    prices = [c["price"] for c in comps]
    return {
        "listing_count": len(prices),
        "price_aed_median": round(percentile(prices, 0.5) or 0, 0),
        "price_aed_p25": round(percentile(prices, 0.25) or 0, 0),
        "price_aed_p75": round(percentile(prices, 0.75) or 0, 0),
        "price_aed_min": round(min(prices), 0),
        "price_aed_max": round(max(prices), 0),
        "sample_listings": sorted(comps, key=lambda c: c["price"])[:8],
    }


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    snapshot_date = None
    comps_by_nb: dict[str, list[dict]] = {}

    for date in SNAPSHOT_DATES:
        print(f"  Trying Inside Airbnb snapshot {date}…")
        raw = fetch_listings_csv(date)
        if not raw:
            continue
        print(f"  ✓ downloaded {len(raw) // 1024}KB")
        rows = parse_csv_gz(raw)
        print(f"  parsed {len(rows)} rows")
        comps_by_nb = filter_comps(rows)
        total = sum(len(v) for v in comps_by_nb.values())
        if total > 0:
            snapshot_date = date
            print(f"  found {total} 1BR entire-home comps across target neighborhoods")
            break
        print(f"  ! no matching listings found in snapshot {date}")

    if not snapshot_date:
        print("  ! all Inside Airbnb fetches failed — writing stub (will retry on next refresh)")
        out = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "insideairbnb-unavailable",
            "note": "Inside Airbnb fetch failed (network/IP block). Run from a residential IP.",
            "neighborhoods": {},
        }
        OUT.write_text(json.dumps(out, indent=2))
        print(f"  → {OUT.relative_to(ROOT)}")
        return

    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "insideairbnb",
        "snapshot_date": snapshot_date,
        "neighborhoods": {},
    }
    for nb, comps in comps_by_nb.items():
        summary = summarise(comps)
        if summary:
            out["neighborhoods"][nb] = summary
            print(f"  {nb}: {summary['listing_count']} comps, "
                  f"median AED {summary['price_aed_median']}, "
                  f"p25–p75 {summary['price_aed_p25']}–{summary['price_aed_p75']}")

    OUT.write_text(json.dumps(out, indent=2))
    print(f"  → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
