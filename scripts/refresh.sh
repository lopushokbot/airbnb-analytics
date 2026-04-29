#!/usr/bin/env bash
# Airbnb Analytics — refresh orchestrator
#
# Runs every data source, merges, archives, fires alerts, opens dashboard.
# Phase 1: Airbnb scrape requires manual MCP-driven step (script prints plan).
# Phase 2: replace the manual section with a headless scraper or LaunchAgent.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"

echo "▸ Airbnb Analytics refresh — $(date '+%Y-%m-%d %H:%M:%S')"
echo

echo "[1/6] Importing your rates from listing CSVs"
$PY scripts/import_rates.py
echo

echo "[2/6] Fetching macro data sources"
$PY sources/airroi_fetch.py
$PY sources/airdna_fetch.py
$PY sources/airbtics_fetch.py
echo

echo "[3/6] Generating Airbnb scrape plan"
echo "      (Phase 1: drive these URLs through Playwright MCP, save results to data/sources/)"
$PY sources/airbnb_scrape.py --plan | head -40
echo "      …(full plan in data/scrape_plan.json)"
$PY sources/airbnb_scrape.py --plan > data/scrape_plan.txt
echo

echo "[4/6] Merging scrape files into latest.json"
$PY sources/airbnb_scrape.py --merge
echo

echo "[5/6] Evaluating alerts"
$PY notify/telegram_alert.py
echo

echo "[6/6] Opening dashboard"
if [[ "${OPEN_DASHBOARD:-1}" == "1" ]]; then
  open dashboard/index.html
fi

echo
echo "✓ Refresh complete. Dashboard: file://$ROOT/dashboard/index.html"
