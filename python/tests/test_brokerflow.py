import pandas as pd
import pytest

from idx.brokerflow import cache, providers
from idx.brokerflow.positions import apply_trade, broker_positions, classify, summarize
from idx.brokerflow.providers import Invezgo, Provider, ProviderError, Sectors, frame


def row(day, broker, bsh=0, bpx=0, ssh=0, spx=0, stock="TEST"):
    return {
        "date": day,
        "stock": stock,
        "broker": broker,
        "buy_freq": 1,
        "buy_shares": bsh,
        "buy_value": bsh * bpx,
        "sell_freq": 1,
        "sell_shares": ssh,
        "sell_value": ssh * spx,
        "f_buy_shares": None,
        "f_buy_value": None,
        "f_sell_shares": None,
        "f_sell_value": None,
    }


# ---------- position engine ----------


def test_apply_trade_average_and_realize():
    pos, cost, rz = apply_trade(0, 0, 1000, 100)
    assert (pos, cost, rz) == (1000, 100, 0)
    pos, cost, rz = apply_trade(pos, cost, 1000, 90)
    assert (pos, cost) == (2000, 95)
    pos, cost, rz = apply_trade(pos, cost, -500, 105)
    assert (pos, cost, rz) == (1500, 95, 5000)
    pos, cost, rz = apply_trade(pos, cost, -2000, 80)  # closes 1500, flips 500 short at 80
    assert (pos, cost, rz) == (-500, 80, -22500)


def test_classify():
    assert classify(0, 0, 0, 0, 0) == "inactive"
    assert classify(0, 0, 1000, 100, 0) == "fresh_accum"
    assert classify(-500, 90, 1000, 100, 0) == "fresh_accum"
    assert classify(1000, 100, 2000, 90, 0) == "avg_down"
    assert classify(1000, 100, 2000, 110, 0) == "avg_up"
    assert classify(1000, 100, 500, 0, 120) == "trim"
    assert classify(1000, 100, 500, 0, 80) == "cut_loss"
    assert classify(1000, 100, 0, 0, 120) == "exit"
    assert classify(1000, 100, 1010, 100, 0) == "hold"
    assert classify(-1000, 100, -2000, 0, 90) == "distribute"
    assert classify(-1000, 100, -500, 95, 0) == "cover"


def test_broker_positions_statuses_and_pl():
    rows = frame(
        [
            row("2026-10-01", "AA", bsh=1000, bpx=100),
            row("2026-10-02", "AA", bsh=1000, bpx=90),  # avg down
            row("2026-10-01", "BB", bsh=1000, bpx=100),
            row("2026-10-02", "BB", ssh=400, spx=120),  # trim
            row("2026-10-02", "CC", bsh=500, bpx=95),  # fresh accum
            row("2026-10-01", "DD", bsh=100, bpx=100, ssh=100, spx=110),  # round trip only
            row("2026-10-02", "EE", ssh=300, spx=100),  # sold inventory from before the window
        ]
    )
    pos = broker_positions(rows, close=100, recent=1).set_index("broker")
    assert pos.loc["AA", "status"] == "avg_down"
    assert pos.loc["AA", "position"] == 2000 and pos.loc["AA", "avg_cost"] == 95
    assert pos.loc["AA", "floating_pl"] == 2000 * 5
    assert pos.loc["BB", "status"] == "trim"
    assert pos.loc["BB", "realized_pl"] == 400 * 20
    assert pos.loc["CC", "status"] == "fresh_accum"
    assert pos.loc["DD", "status"] == "inactive" and pos.loc["DD", "realized_pl"] == 1000
    assert pos.loc["EE", "status"] == "distribute" and pos.loc["EE", "position"] == -300
    assert pos.index[0] == "AA"  # largest long position first


def test_summarize_json_ready():
    rows = frame(
        [row("2026-10-01", "AA", bsh=1000, bpx=100), row("2026-10-02", "AA", bsh=1000, bpx=90)]
    )
    closes = pd.Series({"2026-10-01": 100.0, "2026-10-02": 92.0})
    res = summarize("TEST", rows, closes, recent=1)
    assert res["close"] == 92.0 and res["days"] == 2
    assert res["status_counts"] == {"avg_down": 1}
    assert res["brokers"][0]["foreign_net_value"] is None
    assert res["long_avg_cost"] == 95


# ---------- providers ----------


class FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_invezgo_parses_one_day(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params)
        sample = {
            "code": "LG",
            "buy_freq": "3832",
            "buy_volume": "14305000",
            "buy_value": "141395320000",
            "sell_freq": "4529",
            "sell_volume": "11255300",
            "sell_value": "112346392500",
        }
        return FakeResp(200, [sample])

    monkeypatch.setattr(providers.requests, "get", fake_get)
    df = Invezgo("k").fetch("BBCA", "2026-10-01", "2026-10-01")
    assert calls == [{"from": "2026-10-01", "to": "2026-10-01", "investor": "all", "market": "RG"}]
    assert df.loc[0, "buy_shares"] == 14305000 and df.loc[0, "sell_value"] == 112346392500
    assert df["f_buy_value"].isna().all()
    with pytest.raises(ValueError):
        Invezgo("k").fetch("BBCA", "2026-10-01", "2026-10-02")


def test_sectors_parses_lots_and_foreign(monkeypatch):
    payload = {
        "data": [
            {
                "date": "2025-05-02",
                "summary": [
                    {
                        "broker_code": "AF",
                        "bfreq": 1,
                        "blot": 55,
                        "bval": 48950000,
                        "sfreq": 1,
                        "slot": 50,
                        "sval": 44875000,
                        "f_blot": None,
                        "f_bval": None,
                        "f_slot": 10,
                        "f_sval": 8975000,
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr(providers.requests, "get", lambda *a, **k: FakeResp(200, payload))
    df = Sectors("k").fetch("BBCA", "2025-05-01", "2025-05-14")
    r = df.iloc[0]
    assert r["buy_shares"] == 5500 and r["sell_shares"] == 5000
    assert r["f_buy_shares"] == 0 and r["f_sell_shares"] == 1000


def test_fatal_errors(monkeypatch):
    monkeypatch.setattr(
        providers.requests, "get", lambda *a, **k: FakeResp(402, {"message": "plan"})
    )
    with pytest.raises(ProviderError) as e:
        Invezgo("k").fetch("BBCA", "2026-10-01", "2026-10-01")
    assert e.value.fatal


def test_windows():
    days = ["2026-10-01", "2026-10-02", "2026-10-14", "2026-10-15", "2026-10-30"]
    assert Sectors("k").windows(days) == [
        ("2026-10-01", "2026-10-14"),
        ("2026-10-15", "2026-10-15"),
        ("2026-10-30", "2026-10-30"),
    ]
    assert len(Invezgo("k").windows(days)) == 5


def test_get_provider(monkeypatch):
    for k in ("BROKERFLOW_PROVIDER", "INVEZGO_API_KEY", "SECTORS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert providers.get_provider() is None
    monkeypatch.setenv("SECTORS_API_KEY", "s")
    assert providers.get_provider().name == "sectors"
    monkeypatch.setenv("INVEZGO_API_KEY", "i")
    assert providers.get_provider().name == "invezgo"
    monkeypatch.setenv("BROKERFLOW_PROVIDER", "sectors")
    assert providers.get_provider().name == "sectors"
    assert providers.get_provider("invezgo").name == "invezgo"  # the user's choice wins
    assert providers.configured_providers() == ["invezgo", "sectors"]
    monkeypatch.delenv("SECTORS_API_KEY")
    assert providers.default_provider() == "invezgo"  # preferred one has no key: fall back
    assert providers.get_provider("sectors") is None


def test_provider_choice_keeps_separate_caches(monkeypatch, tmp_path):
    import idx.brokerflow as bf

    monkeypatch.setattr(bf.cache, "CACHE_DIR", str(tmp_path))
    for k in ("BROKERFLOW_PROVIDER", "INVEZGO_API_KEY", "SECTORS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SECTORS_API_KEY", "s")
    st = bf.status()
    assert st["provider"] == "sectors" and st["configured"]
    assert st["providers"] == [
        {"name": "invezgo", "configured": False},
        {"name": "sectors", "configured": True},
    ]
    assert bf.status("invezgo")["configured"] is False
    assert bf.cache_dir("sectors") == str(tmp_path / "sectors")
    with pytest.raises(ValueError):
        bf.status("../etc")  # the name becomes a directory: only known providers
    with pytest.raises(RuntimeError):
        bf.fetch("BBCA", provider="invezgo", dry_run=True)


# ---------- cache ----------


class FakeProvider(Provider):
    def __init__(self, empty_days=()):
        super().__init__("fake", "k", max_window_days=1)
        self.calls = []
        self.empty_days = set(empty_days)

    def fetch(self, stock, start, end):
        self.calls.append(start)
        if start in self.empty_days:
            return frame([])
        return frame([row(start, "AA", bsh=100, bpx=100, stock=stock)])


def prices(days, stock="TEST", zero=()):
    return pd.DataFrame(
        {
            "StockCode": stock,
            "Date": [f"{d}T00:00:00" for d in days],
            "Volume": [0 if d in zero else 1000 for d in days],
            "Close": 100.0,
        }
    )


def test_fetch_only_missing_trading_days(tmp_path):
    days = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"]
    px = prices(days, zero={"2026-10-05"})
    prov = FakeProvider(empty_days={"2026-10-06"})
    base = str(tmp_path)

    est = cache.fetch("test", "2026-10-01", "2026-10-06", prov, dry_run=True, base=base, prices=px)
    assert est["requests"] == 3 and prov.calls == []

    res = cache.fetch("test", "2026-10-01", "2026-10-06", prov, base=base, prices=px, cap=100)
    assert prov.calls == ["2026-10-01", "2026-10-02", "2026-10-06"]
    assert res["fetched_days"] == 3 and res["rows"] == 2
    assert set(cache.fetched_days("TEST", base)) == {"2026-10-01", "2026-10-02", "2026-10-06"}

    again = cache.fetch("test", "2026-10-01", "2026-10-06", prov, base=base, prices=px, cap=100)
    assert again["requests"] == 0 and len(prov.calls) == 3

    rows = cache.read_rows("TEST", "2026-10-01", "2026-10-31", base)
    assert list(rows["date"]) == ["2026-10-01", "2026-10-02"]
    assert cache.usage(base=base)["today"] == 3
    assert cache.cached_stocks(base)[0]["days"] == 3


def test_fetch_stops_at_daily_cap(tmp_path):
    days = ["2026-10-01", "2026-10-02", "2026-10-05"]
    prov = FakeProvider()
    res = cache.fetch(
        "TEST", "2026-10-01", "2026-10-05", prov, base=str(tmp_path), prices=prices(days), cap=2
    )
    assert prov.calls == ["2026-10-01", "2026-10-02"]
    assert res["stopped"] == "daily cap 2 reached"


def test_fatal_error_stops_run(tmp_path):
    class Broken(FakeProvider):
        def fetch(self, stock, start, end):
            self.calls.append(start)
            raise ProviderError("bad key", 401, fatal=True)

    prov = Broken()
    days = ["2026-10-01", "2026-10-02"]
    res = cache.fetch(
        "TEST", "2026-10-01", "2026-10-02", prov, base=str(tmp_path), prices=prices(days), cap=10
    )
    assert prov.calls == ["2026-10-01"] and res["stopped"] == "bad key"
    assert cache.fetched_days("TEST", str(tmp_path)) == {}


# ---------- worker ----------


def test_worker_job_skips_without_key(monkeypatch):
    from datetime import datetime

    from idx import worker

    for k in ("BROKERFLOW_PROVIDER", "INVEZGO_API_KEY", "SECTORS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert worker.job_brokerflow(datetime(2026, 10, 23, 19, 30)).startswith("skipped")
    assert "brokerflow" in [j.name for j in worker.default_jobs()]


def test_api_blocks_cross_site_writes(monkeypatch):
    from fastapi.testclient import TestClient

    from idx.api import app

    for k in ("INVEZGO_API_KEY", "SECTORS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    client = TestClient(app)
    url = "/api/brokerflow/BBCA/fetch?dry_run=true"
    assert client.post(url, headers={"Origin": "https://evil.example"}).status_code == 403
    # same origin, the Vite dev proxy, and non-browser clients (no Origin) reach the endpoint
    assert client.post(url, headers={"Origin": "http://testserver"}).status_code == 400
    assert client.post(url, headers={"Origin": "http://localhost:5176"}).status_code == 400
    assert client.post(url).status_code == 400
    assert client.post(url + "&provider=../x").status_code == 400
