"""
Broker flow: per-stock broker summaries from a BYOK provider (Invezgo / Sectors), cached daily on this
machine, and a position engine on top (average cost, floating / realized P/L, accumulation status).

The user picks the provider per request (``provider=``; default BROKERFLOW_PROVIDER or the first key
set). Each provider has its own cache, request log and daily cap under ``data/brokerflow/{provider}/``:
their numbers differ (Invezgo regular market only, Sectors all markets), so a stock's history is never
mixed across providers.

Fetching spends the user's API quota and only happens on request (CLI, API fetch endpoint, worker);
reading positions uses the cache only. See providers.py, cache.py and positions.py.
"""

import os
from datetime import date, timedelta

from idx.brokerflow import cache
from idx.brokerflow.positions import summarize
from idx.brokerflow.providers import (
    PROVIDERS,
    configured_providers,
    default_provider,
    frame,
    get_provider,
)
from idx.core.adjust import adjustment_events
from idx.core.timeseries import read_dataset

DEFAULT_DAYS = 90  # calendar days when no start date is given


def default_range(date_from: str | None, date_to: str | None, days: int = DEFAULT_DAYS):
    end = date_to or date.today().isoformat()
    start = date_from or (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    return start, end


def cache_dir(provider: str) -> str:
    return os.path.join(cache.CACHE_DIR, provider)


def _name(provider: str | None) -> str | None:
    """Validated provider name (also used as a directory name), else the default provider."""
    if provider:
        provider = provider.strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}; choose one of {', '.join(PROVIDERS)}")
        return provider
    return default_provider()


def status(provider: str | None = None) -> dict:
    name = _name(provider)
    configured = configured_providers()
    out = {
        "provider": name,
        "configured": name in configured,
        "providers": [{"name": n, "configured": n in configured} for n in PROVIDERS],
        "daily_cap": cache.daily_cap(),
        "usage": {"day": date.today().isoformat(), "today": 0, "month": 0, "by_provider": {}},
        "stocks": [],
    }
    if name:
        out["usage"] = cache.usage(base=cache_dir(name))
        out["stocks"] = cache.cached_stocks(cache_dir(name))
    return out


def fetch(
    stock: str, date_from=None, date_to=None, *, dry_run=False, refresh=False, provider=None
) -> dict:
    name = _name(provider)
    p = get_provider(name)
    if p is None:
        raise RuntimeError(
            f"no API key for {name}"
            if name
            else "no broker-flow provider: set INVEZGO_API_KEY or SECTORS_API_KEY in .env"
        )
    start, end = default_range(date_from, date_to)
    return cache.fetch(stock, start, end, p, dry_run=dry_run, refresh=refresh, base=cache_dir(name))


def positions(stock: str, date_from=None, date_to=None, recent: int = 5, provider=None) -> dict:
    """Positions from cached rows only; never calls the provider."""
    stock = stock.upper()
    name = _name(provider)
    start, end = default_range(date_from, date_to)
    rows = cache.read_rows(stock, start, end, cache_dir(name)) if name else frame([])
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
    prov = get_provider(name)
    if prov is not None:
        plan = cache.plan(stock, start, end, prov, base=cache_dir(name), prices=prices)
        plan.pop("_missing_days")
    res.update(range={"from": start, "to": end}, plan=plan, provider=name)
    return res
