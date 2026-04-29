#!/usr/bin/env python3
"""Cross-check comp medians against AirROI + AirDNA neighborhood ADR benchmarks.

Called by airbnb_scrape.py --merge after building the snapshot. Adds a
`cross_check` field to each listing's summary:

  {
    "confidence":     "high" | "medium" | "low" | "synthetic",
    "sources_agreed": bool,     # True if Airbnb scrape + InsideAirbnb within 15%
    "airroi_adr":     720,      # neighborhood benchmark
    "airdna_adr":     760,
    "benchmark_adr":  740,      # average of available benchmarks
    "seasonal_mult":  0.82,     # month-of-year factor applied to benchmark
    "expected_range": [534, 705],  # benchmark × seasonal_mult ± 30%
    "sample_median":  535,      # actual comp median (averaged over horizon)
    "deviation_pct":  -1.2,     # how far off the benchmark
    "flags":          [],       # list of warning strings
  }

Seasonal multipliers are derived from Dubai tourism seasonality data:
AirROI reports AED 797 yearly median, 1421 peak Dec, 790 low Aug.
The curve below is calibrated so the weighted-year-average = 1.0.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Monthly seasonal multiplier for Dubai STR market.
# Calibrated against AirROI 2026 deep-dive: median 797, peak_dec 1421, low_aug 790.
# Weighted 12-month average ≈ 1.0 (normalised to yearly median).
SEASONAL_MULT: dict[int, float] = {
    1: 1.28,   # Jan: post-NYE European winter escape
    2: 1.18,   # Feb: pleasant weather, Valentine's
    3: 1.12,   # Mar: spring, Ramadan variable
    4: 1.03,   # Apr: shoulder
    5: 0.83,   # May: heat building, tourist demand falling
    6: 0.78,   # Jun: low season
    7: 0.76,   # Jul: hottest
    8: 0.78,   # Aug: still hot; AirROI shows 790 ≈ yearly median (long-stay props up ADR)
    9: 0.88,   # Sep: cooling begins, expats return
    10: 1.03,  # Oct: solid demand
    11: 1.22,  # Nov: strong season
    12: 1.55,  # Dec: peak (avg incl Christmas spike & NYE)
}

# Tolerance bands for cross-check
BAND_HIGH   = 0.30   # ±30% → "high" confidence
BAND_MEDIUM = 0.55   # ±55% → "medium"; beyond this → "low"

# Agreement threshold between two independent sources
AGREE_PCT = 0.15


def load_airroi() -> dict:
    p = ROOT / "data" / "sources" / "airroi_dubai.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def load_airdna() -> dict:
    p = ROOT / "data" / "sources" / "airdna_dubai.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def load_insideairbnb() -> dict:
    p = ROOT / "data" / "sources" / "insideairbnb_dubai.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


NEIGHBORHOOD_MAP = {
    "port-la-mer":        "Jumeirah 1",
    "mjl-iconic-terrace": "Umm Suqeim 3",
}


def benchmark_adr(slug: str, airroi: dict, airdna: dict) -> tuple[float | None, float | None, float | None]:
    """Return (airroi_adr, airdna_adr, blended_benchmark) for a listing slug."""
    nb = NEIGHBORHOOD_MAP.get(slug)
    if not nb:
        return None, None, None

    airroi_adr = airroi.get("neighborhoods", {}).get(nb, {}).get("adr_aed_median")
    airdna_adr = airdna.get("neighborhoods", {}).get(nb, {}).get("adr_aed_median")

    sources = [v for v in (airroi_adr, airdna_adr) if v]
    blended = round(statistics.mean(sources), 0) if sources else None
    return airroi_adr, airdna_adr, blended


def cross_check_listing(
    slug: str,
    by_date: dict,  # {date_iso: {comp_median_host_aed, synthetic, ...}}
    is_synthetic: bool,
) -> dict:
    """Produce a cross_check summary for one listing."""
    airroi  = load_airroi()
    airdna  = load_airdna()
    iab     = load_insideairbnb()

    airroi_adr, airdna_adr, benchmark = benchmark_adr(slug, airroi, airdna)
    nb = NEIGHBORHOOD_MAP.get(slug)

    flags: list[str] = []

    if is_synthetic:
        return {
            "confidence": "synthetic",
            "sources_agreed": None,
            "airroi_adr": airroi_adr,
            "airdna_adr": airdna_adr,
            "benchmark_adr": benchmark,
            "note": "No real Airbnb scrape yet. Run refresh.sh → Playwright MCP step.",
        }

    # Compute average comp median across the horizon, grouped by month
    medians_by_month: dict[int, list[float]] = {}
    for d, info in by_date.items():
        med = info.get("comp_median_host_aed")
        if med and not info.get("synthetic"):
            month = int(d[5:7])
            medians_by_month.setdefault(month, []).append(med)

    if not medians_by_month:
        return {"confidence": "unknown", "note": "No non-synthetic data points"}

    # Compute deviation per month vs seasonal benchmark
    deviations: list[float] = []
    for month, vals in medians_by_month.items():
        if not benchmark:
            break
        sample_med = statistics.median(vals)
        expected = benchmark * SEASONAL_MULT.get(month, 1.0)
        dev = (sample_med - expected) / expected * 100
        deviations.append(dev)

    avg_dev = statistics.mean(deviations) if deviations else None
    abs_avg_dev = abs(avg_dev) if avg_dev is not None else None

    if avg_dev is None or benchmark is None:
        confidence = "unknown"
    elif abs_avg_dev <= BAND_HIGH * 100:
        confidence = "high"
    elif abs_avg_dev <= BAND_MEDIUM * 100:
        confidence = "medium"
        flags.append(f"comp median deviates {avg_dev:+.0f}% from AirROI/AirDNA neighborhood benchmark")
    else:
        confidence = "low"
        flags.append(f"large deviation {avg_dev:+.0f}% from benchmark — check scrape quality")

    # Cross-check scraped data vs Inside Airbnb (if available)
    iab_nb = iab.get("neighborhoods", {}).get(nb, {}) if nb else {}
    iab_median = iab_nb.get("price_aed_median") if iab_nb else None
    sources_agreed = None
    if iab_median:
        # Compare average of our scraped medians vs Inside Airbnb snapshot
        all_medians = [v for vals in medians_by_month.values() for v in vals]
        our_avg = statistics.mean(all_medians) if all_medians else None
        if our_avg:
            diff = abs(our_avg - iab_median) / iab_median
            sources_agreed = diff <= AGREE_PCT
            if not sources_agreed:
                flags.append(
                    f"Airbnb scrape avg AED {our_avg:.0f} vs Inside Airbnb AED {iab_median} "
                    f"({diff*100:.0f}% gap) — consider using wider comp search"
                )

    # Inside Airbnb comp count sanity check
    iab_count = iab_nb.get("listing_count", 0) if iab_nb else 0
    if iab_count < 5:
        flags.append(f"Inside Airbnb found only {iab_count} 1BR comps in {nb} — narrow market")

    # Airbnb scrape comp count check
    comp_counts = [info.get("comp_count", 0) for info in by_date.values()
                   if not info.get("synthetic")]
    avg_count = statistics.mean(comp_counts) if comp_counts else 0
    if 0 < avg_count < 4:
        flags.append(f"low comp count ({avg_count:.1f} avg per scan window) — consider fallback URL")

    horizon_meds = [v for vals in medians_by_month.values() for v in vals]
    sample_median = round(statistics.median(horizon_meds), 0) if horizon_meds else None

    # Build expected range for first date's month
    first_month = min(medians_by_month.keys()) if medians_by_month else None
    if benchmark and first_month:
        exp = benchmark * SEASONAL_MULT.get(first_month, 1.0)
        expected_range = [round(exp * (1 - BAND_HIGH), 0), round(exp * (1 + BAND_HIGH), 0)]
        seasonal_mult = SEASONAL_MULT.get(first_month)
    else:
        expected_range = None
        seasonal_mult = None

    return {
        "confidence": confidence,
        "sources_agreed": sources_agreed,
        "airroi_adr": airroi_adr,
        "airdna_adr": airdna_adr,
        "benchmark_adr": benchmark,
        "seasonal_mult": seasonal_mult,
        "expected_range": expected_range,
        "sample_median": sample_median,
        "deviation_pct": round(avg_dev, 1) if avg_dev is not None else None,
        "insideairbnb_median": iab_median,
        "flags": flags,
    }


if __name__ == "__main__":
    # Quick standalone test
    import sys
    slug = sys.argv[1] if len(sys.argv) > 1 else "port-la-mer"
    latest = ROOT / "data" / "latest.json"
    if not latest.exists():
        print("No latest.json found")
        sys.exit(1)
    data = json.loads(latest.read_text())
    listing = data["listings"].get(slug, {})
    by_date = listing.get("by_date", {})
    is_synth = any(v.get("synthetic") for v in by_date.values())
    result = cross_check_listing(slug, by_date, is_synth)
    print(json.dumps(result, indent=2))
