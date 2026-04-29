# Airbnb Analytics — Pricing Cockpit

## Overview
A personal analytics tool that aggregates competitor pricing for Sema's two Dubai Airbnbs into a single Apple-styled dashboard. Pulls Airbnb comp scrapes, AirROI / AirDNA / Airbtics market data, and overlays Sema's own pricing calendar from each listing's `pricing-rates.csv`. Tracks 90 days forward, archives every snapshot for year-over-year comparison, and fires Telegram alerts when comp prices move materially.

This is an internal tool for Sema only — not public, not shared, not deployed anywhere except locally.

## Live URLs
| Environment | URL |
|-------------|-----|
| Local dashboard | `file:///Users/iibot/Documents/ppppp/workspace/airbnb-analytics/dashboard/index.html` |
| Local archive view | `file:///Users/iibot/Documents/ppppp/workspace/airbnb-analytics/dashboard/archive.html` |
| Local path | `/Users/iibot/Documents/ppppp/workspace/airbnb-analytics/` |

## Listings monitored
| Slug | Building | Area | Listing ID | Source folder |
|------|----------|------|------------|---------------|
| `port-la-mer` | Port de la Mer | Jumeirah 1 | 1553428068710590281 | `workspace/airbnb-port-la-mer/` |
| `mjl-iconic-terrace` | Madinat Jumeirah Living | Umm Suqeim 3 | 1669457478098882661 | `workspace/airbnb-mjl-iconic-terrace/` |

## Tech Stack
- **Python 3.13** standard library only (no pip deps for fetchers — uses `urllib`, `json`, `csv`, `gzip`)
- **Playwright MCP** for Airbnb scrapes (Airbnb blocks headless Python; MCP runs a real browser)
- **Vanilla HTML / CSS / JS** for dashboard — no framework, no build step
- **Chart.js** loaded via CDN for charts
- **Apple design system**: New York font for headings, Inter for body, generous whitespace, 8px grid

## Architecture

```
workspace/airbnb-analytics/
├── CLAUDE.md                  # this file
├── RUNBOOK.md                 # how to refresh data, debug, extend
├── config/
│   ├── listings.json          # both listings + comp search URLs + geo keywords
│   ├── alerts.json            # change-detection thresholds + Telegram chat_id
│   └── events.json            # Dubai 2026/2027 events (F1, DSF, NYE, school breaks)
├── sources/
│   ├── airbnb_scrape.py       # generates Playwright MCP scrape plan + parses results
│   ├── airbnb_extract.js      # DOM extractor (paste into mcp__playwright__browser_evaluate)
│   ├── airroi_fetch.py        # AirROI Dubai market data
│   ├── airdna_fetch.py        # AirDNA Marketminder snapshot
│   └── airbtics_fetch.py      # Airbtics free analytics
├── data/
│   ├── latest.json            # most recent merged snapshot — what dashboard reads
│   ├── history.jsonl          # append-only scan log (last ~90 days kept hot)
│   ├── your_rates.json        # Sema's own pricing calendar, imported from each listing's CSV
│   └── archive/
│       ├── 2026/              # full weekly snapshots, gzipped JSON
│       └── monthly/           # monthly rollup (median per date per listing)
├── dashboard/
│   ├── index.html             # main 90-day cockpit
│   ├── archive.html           # year-over-year + long-term trend view
│   ├── styles.css             # shared Apple-styled CSS
│   └── app.js                 # renders charts from latest.json + history.jsonl
├── notify/
│   └── telegram_alert.py      # sends Telegram messages on threshold breach
└── scripts/
    ├── refresh.sh             # main orchestrator — runs every source, archives, notifies
    ├── import_rates.py        # parses each listing's pricing-rates.csv → your_rates.json
    └── rollup.sh              # monthly: builds rollup files for fast year-over-year reads
```

## Data Sources

| Source | Role | Auth | Refresh cost |
|--------|------|------|-------------|
| Airbnb search results (Playwright MCP) | Per-date comp medians for both listings | None (public) | High — needs MCP browser session, ~2-3min per scan |
| AirROI public Dubai market report | Macro: ADR, occupancy, RevPAR by neighborhood | None | Low — single HTTPS fetch |
| AirDNA Marketminder (free tier) | Cross-validation of neighborhood ADR | None | Low |
| Airbtics free market analytics | 1BR Dubai benchmark | None | Low |
| Listing CSVs (`workspace/airbnb-*/pricing-rates.csv`) | Sema's own rates | None | Trivial — local file read |
| Dubai event calendar (`config/events.json`) | Static event overlay | n/a | Manual (edit JSON when new events announced) |

### Comp search URLs

Stored in `config/listings.json`. Reference patterns:

- Port de la Mer:
  `https://www.airbnb.com/s/Port-de-La-Mer--Dubai/homes?adults=2&room_types%5B%5D=Entire%20home%2Fapt&min_bedrooms=1&query=Port%20de%20La%20Mer%20Dubai&checkin=YYYY-MM-DD&checkout=YYYY-MM-DD`
- MJL Iconic Terrace (primary):
  `https://www.airbnb.com/s/Madinat-Jumeirah-Living--Dubai/homes?adults=2&room_types%5B%5D=Entire%20home%2Fapt&min_bedrooms=1&checkin=YYYY-MM-DD&checkout=YYYY-MM-DD`
- MJL fallback (broader Umm Suqeim sample): `airbnb.com/s/Umm-Suqeim--Dubai/homes?...`

### Conversion math
- Search results show PLN guest-total: PLN × 0.897 = AED guest-total per night (after dividing by stay nights)
- AED guest-total ÷ 1.14 = host AED nightly rate
- Guest-sees per night ≈ host rate × 1.22 (rule of thumb after Airbnb fee + 5% VAT)

## Deployment
- **Phase 1 (current)**: local-only. Sema runs `./scripts/refresh.sh` whenever he wants fresh data.
- **Phase 2 (future)**: move repo out of `~/Documents/` to `~/airbnb-analytics/` (TCC restrictions block LaunchAgents from Documents). Install LaunchAgent to run `refresh.sh` every Monday 09:00 Dubai.
- **Phase 3 (optional)**: private GitHub backup, Telegram weekly digest.

## Environment Variables
| Variable | Purpose | Where stored |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | (Phase 2+) sends alerts via Telegram bot | local `.env` (gitignored), never committed |
| `TELEGRAM_CHAT_ID` | Sema's chat ID for alert delivery | `config/alerts.json` (local-only repo, but still keep out of any backup that leaves the Mac) |

For Phase 1, alerts are written to `data/alerts.log` only — Telegram dispatch is optional.

## Known Issues & Gotchas

- **Airbnb anti-bot**: Headless Python scrapers get blocked fast. Always use Playwright MCP (a real browser session) for Airbnb scrapes. AirROI / AirDNA / Airbtics tolerate plain HTTP fetches.
- **PLN currency**: scrapes from this Mac return PLN, not AED. Conversion is 1 PLN ≈ 0.897 AED. If a future scan returns AED directly, the parser detects this from the price suffix.
- **Small sample sizes for MJL**: 5–11 active 1BR comps per scan. Median can swing ±20%. Cross-reference with Umm Suqeim broader query when comp count < 5.
- **Listing IDs disappear from comps**: Sema's own listings drop out of search results when blocked or booked. Filter them out of comp medians regardless — they're not a comp for himself.
- **Date alignment**: `your_rates.json` uses date *ranges* (16 seasonal periods); comp scans use individual nights. The dashboard expands the range bookkeeping into per-day rates so both align on the same axis.
- **Archive size**: weekly gzipped snapshots run ~10–50KB each. Even with 5 years of weekly scans, archive stays under 15MB. No external storage needed.
- **Chart.js CDN**: dashboard requires internet first time it's opened to load Chart.js. Falls back to a "Charts unavailable offline" banner if blocked.
- **AirROI free public access**: scraped from public report pages; if their site changes layout, fetcher needs updating. Errors are logged but don't fail the whole refresh.
