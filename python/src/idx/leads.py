"""
Leads: every potential deal or capital raise found by the market-wide title screen (``idx screen``).

The screen scores each ticker from its filing titles (M&A and capital-raising signals). Leads add
price momentum from the same metrics as the momentum board and mark tickers already tracked by
hand in ``data/deals.json`` / ``data/rights.json``. A lead is a reason to read the filings, not a
confirmed deal.
"""

import glob
import json
import os
from typing import Any

import pandas as pd

from idx.core.utils import DATA_DIR
from idx.momentum import load_momentum, metric_row

SCREENS_DIR = os.path.join(DATA_DIR, "screens")
MOMENTUM_FIELDS = (
    "score",
    "close",
    "r1M",
    "r3M",
    "r6M",
    "from_high",
    "value_20_60",
    "avg_value_20d",
)
KEY_TITLES = 8


def latest_screen(screens_dir: str = SCREENS_DIR) -> dict | None:
    files = sorted(glob.glob(os.path.join(screens_dir, "screen_*.json")), key=os.path.getmtime)
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def build_leads(
    screen: dict | None,
    metrics: pd.DataFrame,
    deals: list[dict],
    rights: list[dict],
) -> dict[str, Any]:
    """Screen candidates with an M&A or capital-raising signal, strongest first."""
    if not screen:
        return {"screen": None, "signals": {}, "leads": []}
    tracked: dict[str, list[str]] = {}
    for d in deals:
        tracked.setdefault(d["target_ticker"], []).append("deal")
    for r in rights:
        tracked.setdefault(r["ticker"], []).append("rights")

    leads = []
    for c in screen.get("candidates", []):
        if not (c.get("score_ma") or c.get("score_capital")):
            continue  # support signals only (EGM, inquiry, volatility ...)
        m = metric_row(metrics, c["ticker"])
        leads.append(
            {
                "ticker": c["ticker"],
                "score_ma": c.get("score_ma", 0),
                "score_capital": c.get("score_capital", 0),
                "signals": c.get("signals", []),
                "stage": c.get("stage") or None,
                "first": c.get("first"),
                "last": c.get("last"),
                "filings": c.get("filings"),
                "key_titles": (c.get("key_titles") or [])[:KEY_TITLES],
                "momentum": None if m.get("no_price") else {k: m.get(k) for k in MOMENTUM_FIELDS},
                "tracked": tracked.get(c["ticker"], []),
            }
        )
    leads.sort(key=lambda x: (-(x["score_ma"] + x["score_capital"]), x["ticker"]))
    return {
        "screen": {
            "date_from": screen.get("date_from"),
            "date_to": screen.get("date_to"),
            "filings_scanned": screen.get("filings_scanned"),
        },
        "signals": screen.get("signals", {}),
        "leads": leads,
    }


def load_leads(deals: list[dict], rights: list[dict]) -> dict[str, Any]:
    metrics, market = load_momentum()
    res = build_leads(latest_screen(), metrics, deals, rights)
    res["prices_as_of"] = market["as_of"] if market else None
    return res
