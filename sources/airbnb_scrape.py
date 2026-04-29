#!/usr/bin/env python3
"""Airbnb scrape orchestrator.

Two modes:
  --plan   Generate a list of (slug, checkin, checkout, url, save_path) tuples
           the operator should drive through Playwright MCP. Prints both human-
           readable lines and a JSON block at the end.
  --merge  Read all data/sources/airbnb_*.json files and merge them into
           data/latest.json (per-listing per-date comp distribution) and
           append a snapshot to data/history.jsonl.

Why split: Airbnb blocks headless Python. Use real Playwright MCP (browser
session) for the scrape itself; let Python handle planning + parsing.

Synthetic-fallback: if --merge runs and finds zero scrape files, it generates a
seed snapshot from each listing's pricing-rates.csv (using the host_now rate as
a proxy for comp median ÷ multiplier). This lets the dashboard render on day
one before the first real MCP scrape.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config" / "listings.json").read_text())
SOURCES_DIR = ROOT / "data" / "sources"
LATEST = ROOT / "data" / "latest.json"
HISTORY = ROOT / "data" / "history.jsonl"
ARCHIVE = ROOT / "data" / "archive"
YOUR_RATES = ROOT / "data" / "your_rates.json"


def fridays_in_horizon(horizon_days: int) -> list[date]:
    today = date.today()
    out = []
    d = today
    while d.weekday() != 4:
        d += timedelta(days=1)
    end = today + timedelta(days=horizon_days)
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def build_url(template: str, checkin: date, checkout: date, keywords: list[str], exclude: list[str]) -> str:
    url = template.replace("{CHECKIN}", checkin.isoformat()).replace("{CHECKOUT}", checkout.isoformat())
    hashparts = []
    if keywords:
        hashparts.append("keywords=" + ",".join(keywords).replace(" ", "%20"))
    if exclude:
        hashparts.append("exclude=" + ",".join(exclude))
    if hashparts:
        url += "#" + "&".join(hashparts)
    return url


def cmd_plan():
    horizon = CFG["scan_config"]["horizon_days"]
    nights = CFG["scan_config"]["window_nights"]
    fridays = fridays_in_horizon(horizon)
    plan = []
    for listing in CFG["listings"]:
        for fri in fridays:
            checkout = fri + timedelta(days=nights)
            url = build_url(
                listing["comp_search"]["primary_url_template"],
                fri,
                checkout,
                listing["comp_search"]["geo_keywords"],
                listing["comp_search"]["exclude_listing_ids"],
            )
            plan.append({
                "listing_slug": listing["slug"],
                "listing_name": listing["name"],
                "checkin": fri.isoformat(),
                "checkout": checkout.isoformat(),
                "nights": nights,
                "url": url,
                "save_path": f"data/sources/airbnb_{listing['slug']}_{fri.isoformat()}.json",
                "fallback_url": build_url(
                    listing["comp_search"]["fallback_url_template"],
                    fri,
                    checkout,
                    listing["comp_search"]["geo_keywords"],
                    listing["comp_search"]["exclude_listing_ids"],
                ),
            })
    print(f"# Airbnb scrape plan — {len(plan)} URLs ({len(CFG['listings'])} listings × {len(fridays)} weekends)")
    for item in plan:
        print(f"  {item['listing_slug']}  {item['checkin']}  →  {item['url'][:90]}…")
    print()
    print("# JSON for MCP driver:")
    print(json.dumps(plan, indent=2))


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


def to_aed_per_night(total_pln: float, nights: int, currency: str) -> float:
    """Convert raw scrape total → AED guest-total per night → host AED nightly."""
    pln_to_aed = CFG["scan_config"]["currency_pln_to_aed"]
    fee_factor = CFG["scan_config"]["airbnb_guest_fee_factor"]
    if currency.upper() == "PLN":
        per_night_aed_guest = (total_pln / nights) * pln_to_aed
    elif currency.upper() == "AED":
        per_night_aed_guest = total_pln / nights
    else:
        per_night_aed_guest = total_pln / nights  # treat unknown as AED
    return round(per_night_aed_guest / fee_factor, 0)


def synthetic_seed():
    """Generate a placeholder snapshot from your_rates.json so the dashboard
    has something to render before the first real Airbnb scrape.

    Heuristic: comp median host AED ≈ your_rate ÷ multiplier (0.71 for PdLM,
    0.65 for MJL — based on the documented pricing strategy). Spread (p25/p75)
    is ±15% around the median. Marked clearly as `synthetic: true`.
    """
    if not YOUR_RATES.exists():
        return None
    your_rates = json.loads(YOUR_RATES.read_text())
    multipliers = {"port-la-mer": 0.71, "mjl-iconic-terrace": 0.65}
    horizon = CFG["scan_config"]["horizon_days"]
    today = date.today()
    snapshot = {}
    for slug, data in your_rates["listings"].items():
        m = multipliers.get(slug, 0.7)
        per_date = {}
        for i in range(horizon):
            d = (today + timedelta(days=i)).isoformat()
            if d not in data["by_date"]:
                continue
            you = data["by_date"][d]
            comp_median = round(you["host_now"] / m, 0)
            per_date[d] = {
                "comp_median_host_aed": comp_median,
                "comp_p25_host_aed": round(comp_median * 0.85, 0),
                "comp_p75_host_aed": round(comp_median * 1.15, 0),
                "comp_count": 0,
                "your_rate_now": you["host_now"],
                "your_rate_after_reviews": you["host_after_reviews"],
                "guest_sees_now": you["guest_now"],
                "synthetic": True,
            }
        snapshot[slug] = {"name": data["name"], "subtitle": data["subtitle"], "by_date": per_date}
    return snapshot


def cmd_merge():
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    nights = CFG["scan_config"]["window_nights"]
    your_rates = json.loads(YOUR_RATES.read_text()) if YOUR_RATES.exists() else {"listings": {}}

    raw_files = sorted(SOURCES_DIR.glob("airbnb_*.json"))
    by_listing_date = {}

    for f in raw_files:
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            print(f"  ! {f.name}: {e}")
            continue
        # filename pattern: airbnb_<slug>_<checkin>.json
        parts = f.stem.split("_")
        if len(parts) < 3:
            continue
        slug = "_".join(parts[1:-1]).replace("_", "-")
        # repair: slug uses hyphens already, splitter mangled it; recover from data file
        slug = data.get("listing_slug") or slug
        checkin = data.get("checkin") or parts[-1]
        prices_total = data.get("prices_total", [])
        currency = data.get("currency", "PLN")
        per_night_host = [to_aed_per_night(p, nights, currency) for p in prices_total]
        if not per_night_host:
            continue
        by_listing_date.setdefault(slug, {})[checkin] = {
            "comp_median_host_aed": round(percentile(per_night_host, 0.5), 0),
            "comp_p25_host_aed": round(percentile(per_night_host, 0.25), 0),
            "comp_p75_host_aed": round(percentile(per_night_host, 0.75), 0),
            "comp_count": data.get("comp_count", len(per_night_host)),
            "scraped_at": data.get("scraped_at"),
            "currency_seen": currency,
            "synthetic": False,
        }

    # Build final snapshot
    snapshot = {}
    if by_listing_date:
        # Merge real scrapes with own-rate overlay
        for slug, dates in by_listing_date.items():
            listing_meta = your_rates["listings"].get(slug, {})
            per_date = {}
            for d, comp in dates.items():
                you = listing_meta.get("by_date", {}).get(d, {})
                per_date[d] = {
                    **comp,
                    "your_rate_now": you.get("host_now"),
                    "your_rate_after_reviews": you.get("host_after_reviews"),
                    "guest_sees_now": you.get("guest_now"),
                }
            snapshot[slug] = {
                "name": listing_meta.get("name", slug),
                "subtitle": listing_meta.get("subtitle", ""),
                "by_date": per_date,
            }
    else:
        # No real scrapes yet — emit synthetic seed
        seed = synthetic_seed()
        if seed:
            snapshot = seed
            print("  ! No scrape files in data/sources/. Emitting synthetic seed from your_rates.json.")
            print("  !  Run sources/airbnb_scrape.py --plan and drive Playwright MCP to get real data.")

    now_iso = datetime.now(timezone.utc).isoformat()
    out = {
        "generated_at": now_iso,
        "horizon_days": CFG["scan_config"]["horizon_days"],
        "listings": snapshot,
    }
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(out, indent=2))
    print(f"  → {LATEST.relative_to(ROOT)}  ({sum(len(v.get('by_date', {})) for v in snapshot.values())} listing-day rows)")

    # Append to history (one line per scan)
    with HISTORY.open("a") as h:
        h.write(json.dumps({"ts": now_iso, "listings": snapshot}) + "\n")

    # Archive (gzipped weekly snapshot, keyed by ISO week)
    today = date.today()
    year, week, _ = today.isocalendar()
    arc_dir = ARCHIVE / str(year)
    arc_dir.mkdir(parents=True, exist_ok=True)
    arc_path = arc_dir / f"{year}-W{week:02d}.json.gz"
    with gzip.open(arc_path, "wt") as g:
        json.dump(out, g, indent=2)
    print(f"  → {arc_path.relative_to(ROOT)}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan", action="store_true")
    p.add_argument("--merge", action="store_true")
    args = p.parse_args()
    if args.plan:
        cmd_plan()
    elif args.merge:
        cmd_merge()
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
