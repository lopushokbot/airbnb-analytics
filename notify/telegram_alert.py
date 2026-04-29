#!/usr/bin/env python3
"""Diff today's snapshot vs the previous one in history.jsonl, evaluate
thresholds in config/alerts.json, log alerts to data/alerts.log, and (if
enabled) deliver to Telegram via bot API.

Phase 1 default: Telegram disabled, alerts log only. Phase 2: flip
config/alerts.json::telegram.enabled to true and set TELEGRAM_BOT_TOKEN env.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config" / "alerts.json").read_text())
HISTORY = ROOT / "data" / "history.jsonl"
LATEST = ROOT / "data" / "latest.json"
LOG = ROOT / Path(CFG.get("log_path", "data/alerts.log"))


def load_history_tail(n: int = 2):
    if not HISTORY.exists():
        return []
    lines = [l.strip() for l in HISTORY.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines[-n:]]


def pct_change(a, b):
    if not a or not b:
        return None
    return round((b - a) / a * 100, 1)


def evaluate():
    th = CFG["thresholds"]
    history = load_history_tail(2)
    if not history:
        return []
    current = history[-1]
    previous = history[-2] if len(history) > 1 else None
    alerts = []
    for slug, listing in current.get("listings", {}).items():
        prev_listing = (previous or {}).get("listings", {}).get(slug, {})
        for d, info in listing.get("by_date", {}).items():
            if info.get("synthetic"):
                continue  # don't alert on placeholder data
            comp_med = info.get("comp_median_host_aed")
            your_now = info.get("your_rate_now")
            comp_count = info.get("comp_count", 0)

            # Comp count floor
            if comp_count is not None and comp_count > 0 and comp_count < th["comp_count_floor"]:
                alerts.append({
                    "kind": "comp_count_low",
                    "listing": slug, "date": d,
                    "comp_count": comp_count,
                    "msg": f"{slug} {d}: only {comp_count} comps (floor {th['comp_count_floor']}) — median unreliable",
                })

            # WoW comp median move
            prev_info = prev_listing.get("by_date", {}).get(d, {}) if prev_listing else {}
            prev_med = prev_info.get("comp_median_host_aed")
            delta = pct_change(prev_med, comp_med)
            if delta is not None and abs(delta) >= th["comp_median_wow_pct"]:
                arrow = "↑" if delta > 0 else "↓"
                alerts.append({
                    "kind": "comp_wow_move",
                    "listing": slug, "date": d,
                    "from": prev_med, "to": comp_med, "delta_pct": delta,
                    "msg": f"{slug} {d}: comp median {arrow}{abs(delta)}% ({prev_med}→{comp_med} AED)",
                })

            # Your rate vs comp spread
            if comp_med and your_now:
                spread = pct_change(comp_med, your_now)
                if spread is not None:
                    if spread <= -th["your_rate_below_comp_pct"]:
                        alerts.append({
                            "kind": "your_rate_low",
                            "listing": slug, "date": d,
                            "spread_pct": spread,
                            "your": your_now, "comp": comp_med,
                            "msg": f"{slug} {d}: your rate {abs(spread)}% below comp median — leaving money on table",
                        })
                    elif spread >= th["your_rate_above_comp_pct"]:
                        alerts.append({
                            "kind": "your_rate_high",
                            "listing": slug, "date": d,
                            "spread_pct": spread,
                            "your": your_now, "comp": comp_med,
                            "msg": f"{slug} {d}: your rate {spread}% above comp median — confirm date is filling",
                        })
    return alerts


def write_log(alerts):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with LOG.open("a") as f:
        f.write(f"\n=== scan {ts} — {len(alerts)} alerts ===\n")
        for a in alerts:
            f.write(f"  [{a['kind']}] {a['msg']}\n")


def send_telegram(alerts):
    cfg = CFG.get("telegram", {})
    if not cfg.get("enabled"):
        return False
    token = os.environ.get(cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"))
    chat_id = cfg.get("chat_id")
    if not token or not chat_id:
        return False
    if not alerts:
        return False
    text = "🏠 *Airbnb Analytics Alert*\n\n" + "\n".join(f"• {a['msg']}" for a in alerts[:20])
    if len(alerts) > 20:
        text += f"\n…and {len(alerts) - 20} more (see data/alerts.log)"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    try:
        with urllib.request.urlopen(url, body, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  ! telegram send failed: {e}")
        return False


def main():
    alerts = evaluate()
    write_log(alerts)
    delivered = send_telegram(alerts)
    suffix = " (telegram delivered)" if delivered else " (log only)"
    print(f"  → {len(alerts)} alerts{suffix}")
    for a in alerts[:10]:
        print(f"    [{a['kind']}] {a['msg']}")
    if len(alerts) > 10:
        print(f"    …and {len(alerts) - 10} more in {LOG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
