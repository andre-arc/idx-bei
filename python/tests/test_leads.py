"""
Tests for the leads list (idx.leads): screen candidates with momentum and tracked flags.
"""

import json
import os

import pandas as pd
from fastapi.testclient import TestClient

import idx.leads as leads
from idx.api import _TTL_CACHE, app
from idx.leads import build_leads, latest_screen

SCREEN = {
    "date_from": "2025-01-01",
    "date_to": "2026-10-23",
    "filings_scanned": 1000,
    "signals": {"CC": {"label": "change of control", "group": "ma", "weight": 4}},
    "candidates": [
        {
            "ticker": "AAAA",
            "signals": ["CC", "TO"],
            "stage": "tender offer",
            "score_ma": 8,
            "score_capital": 0,
            "last": "2026-10-01",
        },
        {
            "ticker": "BBBB",
            "signals": ["RI"],
            "stage": "",
            "score_ma": 0,
            "score_capital": 4,
            "last": "2026-09-01",
        },
        {
            "ticker": "CCCC",
            "signals": ["EGM", "IQ"],
            "stage": "",
            "score_ma": 0,
            "score_capital": 0,
            "last": "2026-08-01",
        },
        {
            "ticker": "DDDD",
            "signals": ["NG", "PP"],
            "stage": "negotiation",
            "score_ma": 3,
            "score_capital": 2,
            "last": "2026-07-01",
            "key_titles": [["2026-07-01", "NG", f"t{i}"] for i in range(12)],
        },
    ],
}
METRICS = pd.DataFrame(
    {
        "score": [80.0, 20.0],
        "close": [100.0, 50.0],
        "r1M": [0.1, -0.1],
        "r3M": [0.2, -0.2],
        "r6M": [0.3, None],
        "from_high": [0.0, -0.5],
        "value_20_60": [1.2, 0.8],
        "avg_value_20d": [2e9, 1e8],
        "p1M": [0.9, 0.1],
    },
    index=["AAAA", "DDDD"],
)


def test_build_leads_filters_ranks_and_flags():
    res = build_leads(
        SCREEN,
        METRICS,
        deals=[{"target_ticker": "AAAA"}],
        rights=[{"ticker": "AAAA"}, {"ticker": "BBBB"}],
    )
    json.dumps(res)
    assert [x["ticker"] for x in res["leads"]] == [
        "AAAA",
        "DDDD",
        "BBBB",
    ]  # CCCC: support signals only
    a, d, b = res["leads"]
    assert a["tracked"] == ["deal", "rights"] and b["tracked"] == ["rights"] and d["tracked"] == []
    assert a["momentum"]["score"] == 80 and "p1M" not in a["momentum"]
    assert b["momentum"] is None  # no price
    assert d["stage"] == "negotiation" and b["stage"] is None
    assert len(d["key_titles"]) == 8
    assert res["screen"] == {
        "date_from": "2025-01-01",
        "date_to": "2026-10-23",
        "filings_scanned": 1000,
    }
    assert res["signals"]["CC"]["label"] == "change of control"


def test_build_leads_without_screen():
    assert build_leads(None, METRICS, [], []) == {"screen": None, "signals": {}, "leads": []}


def test_latest_screen_picks_newest(tmp_path):
    for name, mtime in (
        ("screen_2025-01-01_2026-10-22.json", 1),
        ("screen_2025-01-01_2026-10-23.json", 2),
    ):
        p = tmp_path / name
        p.write_text(json.dumps({"date_to": name[-15:-5]}), encoding="utf-8")
        os.utime(p, (mtime, mtime))
    (tmp_path / "screen_2025-01-01_2026-10-23.md").write_text("x")
    assert latest_screen(str(tmp_path))["date_to"] == "2026-10-23"
    assert latest_screen(str(tmp_path / "missing")) is None


def test_leads_endpoint(monkeypatch):
    _TTL_CACHE.pop("leads", None)
    calls = []

    def fake_load_leads(deals, rights):
        calls.append(1)
        return {"screen": None, "signals": {}, "leads": [], "prices_as_of": "2026-10-23"}

    monkeypatch.setattr(leads, "load_leads", fake_load_leads)
    client = TestClient(app)
    assert client.get("/api/leads").json()["prices_as_of"] == "2026-10-23"
    client.get("/api/leads")
    assert calls == [1]  # second call served from cache
    _TTL_CACHE.pop("leads", None)
