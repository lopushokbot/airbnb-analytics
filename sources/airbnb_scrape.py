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
seed snapshot using AirROI neighborhood ADRs as the market baseline, with:
  - Seasonal multipliers calibrated against AirROI Dubai data
  - Weekend premium (+18% Fri/Sat, UAE calendar)
  - Event premium (+10/+20/+35%) from events.json tier
  - Spread (p25 −20%, p75 +25%) based on Dubai market distribution
  - Inside Airbnb data used as additional source when available

Data sources used (in order of preference):
  1. Playwright MCP scrapes (data/sources/airbnb_<slug>_<date>.json)
  2. Inside Airbnb bulk listings (data/sources/insideairbnb_dubai.json)
  3. AirROI-grounded synthetic seed (always available as last resort)

Cross-check: after any merge, comp medians are validated against AirROI + AirDNA
neighborhood ADR benchmarks. Results stored in listing.cross_check.
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
EVENTS_CFG = json.loads((ROOT / "config" / "events.json").read_text()).get("events", [])
SOURCES_DIR = ROOT / "data" / "sources"
LATEST = ROOT / "data" / "latest.json"
HISTORY = ROOT / "data" / "history.jsonl"
ARCHIVE = ROOT / "data" / "archive"
YOUR_RATES = ROOT / "data" / "your_rates.json"

# ── Seasonal multipliers (Dubai STR) ──────────────────────────────────────────
# Calibrated against AirROI 2026: median AED 797, peak Dec AED 1421, low Aug AED 790.
# Weighted 12-month average ≈ 1.0.
SEASONAL_MULT: dict[int, float] = {
    1: 1.28, 2: 1.18, 3: 1.12, 4: 1.03,
    5: 0.83, 6: 0.78, 7: 0.76, 8: 0.78,
    9: 0.88, 10: 1.03, 11: 1.22, 12: 1.55,
}

# AirROI neighborhood baseline ADR (host AED nightly, 1BR)
NEIGHBORHOOD_ADR: dict[str, float] = {
    "port-la-mer":        720.0,   # Jumeirah 1 (AirROI 2026)
    "mjl-iconic-terrace": 850.0,   # Umm Suqeim 3 (AirROI 2026)
}

# Market spread: Dubai 1BR comps, calibrated from AirROI
SPREAD_P25 = 0.80   # p25 = 80% of median
SPREAD_P75 = 1.25   # p75 = 125% of median

# Weekend premium (Fri + Sat nights in UAE)
WEEKEND_PREMIUM = 1.18

# Event tier premiums
EVENT_PREMIUM: dict[str, float] = {
    "very_high": 1.35,
    "high":      1.20,
    "medium":    1.10,
    "low":       1.05,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

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
        per_night_aed_guest = total_pln / nights
    return round(per_night_aed_guest / fee_factor, 0)


def is_uae_weekend(d: date) -> bool:
    """Fri (weekday=4) and Sat (weekday=5) are the UAE weekend."""
    return d.weekday() in (4, 5)


def event_multiplier_for_date(iso: str) -> float:
    """Return the highest event tier premium for this date."""
    mult = 1.0
    for ev in EVENTS_CFG:
        if ev.get("start", "") <= iso <= ev.get("end", ""):
            m = EVENT_PREMIUM.get(ev.get("tier", ""), 1.0)
            mult = max(mult, m)
    return mult


# ── --plan ────────────────────────────────────────────────────────────────────

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


# ── Synthetic seed ────────────────────────────────────────────────────────────

def synthetic_seed() -> dict | None:
    """Generate a realistic placeholder snapshot grounded in AirROI neighborhood ADRs.

    Unlike the old approach (your_rate / fixed_multiplier → flat curve), this:
      1. Uses AirROI neighborhood ADR as the market baseline for each listing
      2. Applies calibrated monthly seasonal multipliers
      3. Adds UAE weekend premium (+18% Fri/Sat)
      4. Adds event-tier premium from events.json
      5. Sets spread at p25 −20% / p75 +25% (Dubai market distribution)
      6. Cross-checks against Inside Airbnb data if available (adjusts base)
    """
    if not YOUR_RATES.exists():
        return None
    your_rates = json.loads(YOUR_RATES.read_text())
    horizon = CFG["scan_config"]["horizon_days"]
    today = date.today()

    # Load Inside Airbnb if available — use its median to refine the base ADR
    iab_path = SOURCES_DIR / "insideairbnb_dubai.json"
    iab = json.loads(iab_path.read_text()) if iab_path.exists() else {}
    iab_nbs = iab.get("neighborhoods", {})
    nb_map = {
        "port-la-mer":        "Jumeirah 1",
        "mjl-iconic-terrace": "Umm Suqeim 3",
    }

    snapshot = {}
    for slug, data in your_rates["listings"].items():
        nb = nb_map.get(slug)
        base_adr = NEIGHBORHOOD_ADR.get(slug, 720.0)

        # If Inside Airbnb has real data, blend its median with AirROI ADR
        iab_nb = iab_nbs.get(nb, {}) if nb else {}
        if iab_nb.get("price_aed_median"):
            # Weight: 60% Inside Airbnb (current market), 40% AirROI (structural benchmark)
            base_adr = round(iab_nb["price_aed_median"] * 0.6 + base_adr * 0.4, 0)
            print(f"  {slug}: blending InsideAirbnb AED {iab_nb['price_aed_median']} "
                  f"+ AirROI AED {NEIGHBORHOOD_ADR.get(slug)} → base AED {base_adr}")

        per_date = {}
        for i in range(horizon):
            d_obj = today + timedelta(days=i)
            d = d_obj.isoformat()
            if d not in data["by_date"]:
                continue
            you = data["by_date"][d]

            # Seasonal multiplier
            month = d_obj.month
            s_mult = SEASONAL_MULT.get(month, 1.0)

            # Weekend premium
            w_mult = WEEKEND_PREMIUM if is_uae_weekend(d_obj) else 1.0

            # Event premium
            e_mult = event_multiplier_for_date(d)

            comp_median = round(base_adr * s_mult * w_mult * e_mult, 0)

            per_date[d] = {
                "comp_median_host_aed": comp_median,
                "comp_p25_host_aed": round(comp_median * SPREAD_P25, 0),
                "comp_p75_host_aed": round(comp_median * SPREAD_P75, 0),
                "comp_count": 0,
                "your_rate_now": you["host_now"],
                "your_rate_after_reviews": you["host_after_reviews"],
                "guest_sees_now": you["guest_now"],
                "synthetic": True,
                "synthetic_base": "airroi+insideairbnb" if iab_nb.get("price_aed_median") else "airroi",
            }
        snapshot[slug] = {"name": data["name"], "subtitle": data["subtitle"], "by_date": per_date}
    return snapshot


# ── Inside Airbnb fallback ────────────────────────────────────────────────────

def insideairbnb_as_comps() -> dict[str, dict]:
    """Use Inside Airbnb neighborhood medians to fill comp data where no scrape exists.

    Returns a by_listing_date dict in the same shape that the Playwright scrape
    files produce, but covering only the 'current snapshot' (same price for all
    dates in each month, with seasonal adjustment).
    """
    iab_path = SOURCES_DIR / "insideairbnb_dubai.json"
    if not iab_path.exists():
        return {}
    iab = json.loads(iab_path.read_text())
    if iab.get("source") in ("insideairbnb-unavailable",):
        return {}

    nb_map = {
        "port-la-mer":        "Jumeirah 1",
        "mjl-iconic-terrace": "Umm Suqeim 3",
    }
    horizon = CFG["scan_config"]["horizon_days"]
    today = date.today()
    result: dict[str, dict] = {}

    for slug, nb in nb_map.items():
        nb_data = iab.get("neighborhoods", {}).get(nb)
        if not nb_data:
            continue
        iab_median = nb_data.get("price_aed_median")
        iab_p25    = nb_data.get("price_aed_p25")
        iab_p75    = nb_data.get("price_aed_p75")
        if not iab_median:
            continue

        # Inside Airbnb snapshot is taken in a specific month — we know its season.
        snapshot_date = iab.get("snapshot_date", "")
        snapshot_month = int(snapshot_date[5:7]) if len(snapshot_date) >= 7 else 10
        snapshot_mult  = SEASONAL_MULT.get(snapshot_month, 1.0)

        per_date: dict[str, dict] = {}
        for i in range(horizon):
            d_obj = today + timedelta(days=i)
            d = d_obj.isoformat()
            month = d_obj.month
            # Re-seasonalise from snapshot month to target month
            target_mult = SEASONAL_MULT.get(month, 1.0)
            w_mult = WEEKEND_PREMIUM if is_uae_weekend(d_obj) else 1.0
            e_mult = event_multiplier_for_date(d)
            factor = (target_mult / snapshot_mult) * w_mult * e_mult

            comp_median = round(iab_median * factor, 0)
            comp_p25    = round(iab_p25    * factor, 0) if iab_p25 else round(comp_median * SPREAD_P25, 0)
            comp_p75    = round(iab_p75    * factor, 0) if iab_p75 else round(comp_median * SPREAD_P75, 0)

            per_date[d] = {
                "comp_median_host_aed": comp_median,
                "comp_p25_host_aed": comp_p25,
                "comp_p75_host_aed": comp_p75,
                "comp_count": nb_data.get("listing_count", 0),
                "scraped_at": iab.get("fetched_at"),
                "currency_seen": "AED",
                "synthetic": False,
                "source": "insideairbnb",
            }
        result[slug] = per_date

    return result


# ── --merge ───────────────────────────────────────────────────────────────────

def cmd_merge():
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    nights = CFG["scan_config"]["window_nights"]
    your_rates = json.loads(YOUR_RATES.read_text()) if YOUR_RATES.exists() else {"listings": {}}

    raw_files = sorted(SOURCES_DIR.glob("airbnb_*.json"))
    by_listing_date: dict[str, dict] = {}

    for f in raw_files:
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            print(f"  ! {f.name}: {e}")
            continue
        parts = f.stem.split("_")
        if len(parts) < 3:
            continue
        slug = "_".join(parts[1:-1]).replace("_", "-")
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
            "source": "playwright",
        }

    # Determine data source tier
    if by_listing_date:
        data_source = "playwright"
        print(f"  Using Playwright MCP scrapes ({sum(len(v) for v in by_listing_date.values())} scan windows)")
    else:
        # Try Inside Airbnb as real-data fallback
        iab_comps = insideairbnb_as_comps()
        if iab_comps:
            data_source = "insideairbnb"
            by_listing_date = iab_comps
            print(f"  No Playwright scrapes found — using Inside Airbnb data for "
                  f"{len(iab_comps)} listings")
        else:
            data_source = "synthetic"

    # Build final snapshot
    snapshot: dict[str, dict] = {}

    if data_source in ("playwright", "insideairbnb"):
        for slug, dates in by_listing_date.items():
            listing_meta = your_rates["listings"].get(slug, {})
            per_date: dict[str, dict] = {}

            for d, comp in dates.items():
                you = listing_meta.get("by_date", {}).get(d, {})
                per_date[d] = {
                    **comp,
                    "your_rate_now": you.get("host_now"),
                    "your_rate_after_reviews": you.get("host_after_reviews"),
                    "guest_sees_now": you.get("guest_now"),
                }

            # Cross-check this listing
            try:
                from crosscheck import cross_check_listing
                cc = cross_check_listing(slug, per_date, is_synthetic=False)
            except Exception as e:
                cc = {"confidence": "unknown", "error": str(e)}
            print(f"  cross-check {slug}: confidence={cc.get('confidence')} "
                  f"deviation={cc.get('deviation_pct', '—')}%"
                  + (f"  ⚠ {cc['flags']}" if cc.get("flags") else ""))

            snapshot[slug] = {
                "name": listing_meta.get("name", slug),
                "subtitle": listing_meta.get("subtitle", ""),
                "data_source": data_source,
                "cross_check": cc,
                "by_date": per_date,
            }
    else:
        seed = synthetic_seed()
        if seed:
            snapshot = seed
            # Add cross_check stubs for synthetic
            for slug, listing in snapshot.items():
                try:
                    from crosscheck import cross_check_listing
                    cc = cross_check_listing(slug, listing["by_date"], is_synthetic=True)
                except Exception as e:
                    cc = {"confidence": "synthetic", "error": str(e)}
                listing["data_source"] = "synthetic"
                listing["cross_check"] = cc
            print("  ! No scrape files or Inside Airbnb data. Emitting AirROI-grounded synthetic seed.")
            print("  !  Run ./scripts/refresh.sh to fetch InsideAirbnb, then drive Playwright MCP.")

    now_iso = datetime.now(timezone.utc).isoformat()
    out = {
        "generated_at": now_iso,
        "horizon_days": CFG["scan_config"]["horizon_days"],
        "data_source": data_source,
        "listings": snapshot,
    }
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(out, indent=2))
    print(f"  → {LATEST.relative_to(ROOT)}  "
          f"({sum(len(v.get('by_date', {})) for v in snapshot.values())} listing-day rows)")

    with HISTORY.open("a") as h:
        h.write(json.dumps({"ts": now_iso, "data_source": data_source, "listings": snapshot}) + "\n")

    today = date.today()
    year, week, _ = today.isocalendar()
    arc_dir = ARCHIVE / str(year)
    arc_dir.mkdir(parents=True, exist_ok=True)
    arc_path = arc_dir / f"{year}-W{week:02d}.json.gz"
    with gzip.open(arc_path, "wt") as g:
        json.dump(out, g, indent=2)
    print(f"  → {arc_path.relative_to(ROOT)}")


# ── Entry ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan",  action="store_true")
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
