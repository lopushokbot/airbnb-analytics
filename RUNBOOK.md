# Airbnb Analytics — Runbook

> Read CLAUDE.md before doing any work on this project.

## Quick Reference
| Task | When | Command |
|------|------|---------|
| Refresh all data + dashboard | On demand (Phase 1), weekly Monday 9 AM Dubai (Phase 2) | `./scripts/refresh.sh` |
| Re-import Sema's own rates | After editing `pricing-rates.csv` in either listing | `python3 scripts/import_rates.py` |
| Build monthly archive rollup | Last day of each month | `./scripts/rollup.sh` |
| View dashboard | Any time | `open dashboard/index.html` |
| View archive | Any time | `open dashboard/archive.html` |

---

## Task: Full data refresh (`./scripts/refresh.sh`)

### Steps the script runs
1. **Import own rates** — `import_rates.py` parses `pricing-rates.csv` from each listing folder and expands the 16 seasonal periods into per-day rates → `data/your_rates.json`.
2. **Fetch macro data** — `airroi_fetch.py`, `airdna_fetch.py`, `airbtics_fetch.py` write neighborhood ADR/occupancy snapshots to `data/sources/`.
3. **Generate Airbnb scrape plan** — `airbnb_scrape.py --plan` prints the list of URLs (13 weekly windows × 2 listings = 26 URLs) the operator should hit with Playwright MCP. The MCP-driven browser_evaluate calls write raw results to `data/sources/airbnb_*.json`.
4. **Merge** — `airbnb_scrape.py --merge` consolidates all source files into `data/latest.json` and appends to `data/history.jsonl`.
5. **Diff** — compare new snapshot to previous; fire alerts via `notify/telegram_alert.py` (or write to `data/alerts.log` if Telegram not configured).
6. **Archive** — gzip the new snapshot into `data/archive/<YYYY>/<YYYY-Www>.json.gz`.
7. **Open dashboard** — `open dashboard/index.html`.

### Manual MCP scrape phase (Phase 1)
Until a headless scraper that survives Airbnb's anti-bot is built, the Airbnb step is run by Claude in a session:

1. Run `python3 sources/airbnb_scrape.py --plan` — get the URL list.
2. For each URL, call `mcp__playwright__browser_navigate` then `mcp__playwright__browser_evaluate` with the contents of `sources/airbnb_extract.js`.
3. Save each scrape result as `data/sources/airbnb_<listing>_<checkin>.json`.
4. Run `python3 sources/airbnb_scrape.py --merge` to consolidate.

The plan output includes ready-to-paste JSON shapes so any Claude session can drive the MCP scrapes mechanically.

### Validation
- [ ] `data/latest.json` has both listings with `comps` array of >=4 entries per date
- [ ] `data/history.jsonl` ends with today's timestamp
- [ ] `data/archive/<YYYY>/<YYYY-Www>.json.gz` was created/overwritten
- [ ] Dashboard renders in Chrome with no console errors

### If something goes wrong
| Symptom | Cause | Fix |
|---------|-------|-----|
| Airbnb scrape returns 0 results | Anti-bot block, geo-IP changed, search filters too narrow | Reload page in MCP, broaden query (drop `min_bedrooms=1` filter, re-add via JS) |
| Currency in scrape is AED not PLN | Different Airbnb session | `airbnb_scrape.py` auto-detects currency suffix; verify `currency` field in raw scrape file |
| AirROI fetcher 403 | They added Cloudflare | Move to Playwright fetch in `airroi_fetch.py` (set `USE_PLAYWRIGHT=True`) |
| Dashboard charts blank | Chart.js CDN blocked / `latest.json` malformed | Open Chrome devtools — check Network tab for CDN, check Console for JSON parse errors |
| Telegram alerts not sending | Bot token or chat_id missing | Phase 1 falls back to `data/alerts.log` — that's expected. Configure `.env` for Phase 2. |

---

## Task: Adjust alert thresholds

Edit `config/alerts.json`. Defaults:

| Trigger | Default threshold |
|---------|-------------------|
| Comp median moved week-over-week | 10% |
| Your rate below comp median | 20% |
| Your rate above comp median (date still empty) | 20% |
| Comp count drops below | 4 listings |
| AirROI neighborhood ADR moved month-over-month | 10% |

Re-run `refresh.sh` after editing — alerts re-evaluate against latest snapshot.

---

## Task: Year-over-year comparison

Once 12+ months of history exist:
1. Open `dashboard/archive.html`
2. Use the date range picker to compare any window vs same window 1 year prior
3. Cards show: median ADR YoY %, occupancy YoY %, your revenue YoY (if available)

For now (Phase 1, no history yet) the archive page shows a "Building history..." state with the count of weekly snapshots collected.

---

## Task: Add a third listing later

1. Add entry in `config/listings.json` with `slug`, `listing_id`, `comp_search_urls`, `geo_keywords`, `path_to_pricing_csv`
2. Add same `slug` to whichever listing's CSV path you point to
3. Re-run `refresh.sh` — dashboard auto-discovers new listings

No code changes needed — `app.js` iterates whatever `latest.json` contains.

---

## Changelog
| Date | Change |
|------|--------|
| 2026-04-29 | Initial scaffold. Two listings (`port-la-mer`, `mjl-iconic-terrace`). Phase 1 = manual on-demand refresh, MCP-driven Airbnb scrape. Dashboard renders 90-day cockpit + archive view. Telegram alerts plumbed but optional. |
