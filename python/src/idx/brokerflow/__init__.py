"""
Broker flow: per-stock broker summaries from a BYOK provider (Invezgo / Sectors), cached daily on this
machine, and a position engine on top (average cost, floating / realized P/L, accumulation status).

Fetching spends the user's API quota and only happens on request (CLI, API fetch endpoint, worker);
reading positions uses the cache only. See providers.py, cache.py and positions.py.
"""

from datetime import date, timedelta

from idx.brokerflow import cache
from idx.brokerflow.positions import summarize
from idx.brokerflow.providers import get_provider
from idx.core.adjust import adjustment_events
from idx.core.timeseries import read_dataset

DEFAULT_DAYS = 90  # calendar days when no start date is given


def default_range(date_from: str | None, date_to: str | None, days: int = DEFAULT_DAYS):
    end = date_to or date.today().isoformat()
    start = date_from or (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    return start, end


def status() -> dict:
    p = get_provider()
    return {
        "provider": p.name if p else None,
        "configured": p is not None,
        "daily_cap": cache.daily_cap(),
        "usage": cache.usage(),
        "stocks": cache.cached_stocks(),
    }


def fetch(stock: str, date_from=None, date_to=None, *, dry_run=False, refresh=False) -> dict:
    p = get_provider()
    if p is None:
        raise RuntimeError(
            "no broker-flow provider: set INVEZGO_API_KEY or SECTORS_API_KEY in .env"
        )
    start, end = default_range(date_from, date_to)
    return cache.fetch(stock, start, end, p, dry_run=dry_run, refresh=refresh)


def positions(stock: str, date_from=None, date_to=None, recent: int = 5) -> dict:
    """Positions from cached rows only; never calls the provider."""
    stock = stock.upper()
    start, end = default_range(date_from, date_to)
    rows = cache.read_rows(stock, start, end)
    prices = read_dataset("stock_summary", start, end)
    p = prices[prices["StockCode"] == stock] if not prices.empty else prices
    if p.empty:
        closes = p
        events = []
    else:
        closes = p.assign(d=p["Date"].astype(str).str[:10]).set_index("d")["Close"].sort_index()
        ev = adjustment_events(p)
        events = [
            {"date": e.Date.date().isoformat(), "factor": round(float(e.factor), 4)}
            for e in ev.itertuples()
        ]
    res = summarize(stock, rows, closes, recent, events)
    plan = None
    prov = get_provider()
    if prov is not None:
        plan = cache.plan(stock, start, end, prov, prices=prices)
        plan.pop("_missing_days")
    res.update(range={"from": start, "to": end}, plan=plan)
    return res
