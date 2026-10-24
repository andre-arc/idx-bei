"""
Local daily cache of broker summaries: each (stock, trading day) is requested from the provider once.

Layout (``data/brokerflow/``, gitignored, never leaves this machine):

- ``{STOCK}/{YYYY-MM}.parquet``  daily rows (see providers.ROW_COLUMNS)
- ``{STOCK}/fetched.json``       {day: provider} for every day already requested, including days the
                                  provider returned nothing for (so they are not requested again)
- ``usage.jsonl``                one line per provider request (for the daily cap and quota tracking)

Closed trading days do not change, so cached days are never re-fetched unless ``refresh=True``.
Trading days come from the stored IDX stock summary: only days on which the stock traded
(Volume > 0) are requested, which skips holidays, suspensions and illiquid days.
"""

import json
import os
from datetime import datetime

import pandas as pd

from idx.brokerflow.providers import ROW_COLUMNS, Provider, ProviderError, frame
from idx.core.timeseries import read_dataset
from idx.core.utils import DATA_DIR, get_logger, load_json, save_json

log = get_logger("idx.brokerflow")

CACHE_DIR = os.path.join(DATA_DIR, "brokerflow")
DEFAULT_DAILY_CAP = 500


def _stock_dir(stock: str, base: str) -> str:
    return os.path.join(base, stock.upper())


def fetched_days(stock: str, base: str = CACHE_DIR) -> dict[str, str]:
    return load_json(os.path.join(_stock_dir(stock, base), "fetched.json"), {})


def cached_stocks(base: str = CACHE_DIR) -> list[dict]:
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        days = fetched_days(name, base) if os.path.isdir(os.path.join(base, name)) else {}
        if days:
            out.append({"stock": name, "days": len(days), "first": min(days), "last": max(days)})
    return out


def read_rows(stock: str, start: str | None = None, end: str | None = None, base: str = CACHE_DIR):
    d = _stock_dir(stock, base)
    if not os.path.isdir(d):
        return frame([])
    months = sorted(f for f in os.listdir(d) if f.endswith(".parquet"))
    if start:
        months = [m for m in months if m[:7] >= start[:7]]
    if end:
        months = [m for m in months if m[:7] <= end[:7]]
    if not months:
        return frame([])
    df = pd.concat([pd.read_parquet(os.path.join(d, m)) for m in months], ignore_index=True)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df.sort_values(["date", "broker"], ignore_index=True)


def write_rows(
    stock: str, rows: pd.DataFrame, days: list[str], provider: str, base: str = CACHE_DIR
):
    """Store rows for `days` (replacing any earlier rows for those days) and mark the days fetched."""
    d = _stock_dir(stock, base)
    os.makedirs(d, exist_ok=True)
    days_set = set(days)
    for month in sorted({day[:7] for day in days}):
        path = os.path.join(d, f"{month}.parquet")
        old = pd.read_parquet(path) if os.path.exists(path) else frame([])
        old = old[~old["date"].isin(days_set)]
        new = rows[rows["date"].str[:7] == month]
        merged = pd.concat([old, new], ignore_index=True)[ROW_COLUMNS]
        merged = merged.sort_values(["date", "broker"], ignore_index=True)
        tmp = path + ".tmp"
        merged.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    fetched = fetched_days(stock, base)
    fetched.update({day: provider for day in days})
    save_json(os.path.join(d, "fetched.json"), dict(sorted(fetched.items())))


def log_usage(
    provider: str, stock: str, start: str, end: str, n: int, status: str, base: str = CACHE_DIR
):
    os.makedirs(base, exist_ok=True)
    line = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "stock": stock,
        "start": start,
        "end": end,
        "requests": n,
        "status": status,
    }
    with open(os.path.join(base, "usage.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def usage(day: str | None = None, base: str = CACHE_DIR) -> dict:
    """Requests made on `day` (default today) and in its month, per provider."""
    day = day or datetime.now().date().isoformat()
    out = {"day": day, "today": 0, "month": 0, "by_provider": {}}
    path = os.path.join(base, "usage.jsonl")
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            u = json.loads(line)
            if u["ts"][:7] != day[:7]:
                continue
            out["month"] += u["requests"]
            out["by_provider"][u["provider"]] = (
                out["by_provider"].get(u["provider"], 0) + u["requests"]
            )
            if u["ts"][:10] == day:
                out["today"] += u["requests"]
    return out


def daily_cap() -> int:
    try:
        return int(os.environ.get("BROKERFLOW_DAILY_CAP", DEFAULT_DAILY_CAP))
    except ValueError:
        return DEFAULT_DAILY_CAP


def trading_days(stock: str, start: str, end: str, prices: pd.DataFrame | None = None) -> list[str]:
    """Days in [start, end] on which `stock` traded, from the stored IDX stock summary."""
    if prices is None:
        prices = read_dataset("stock_summary", start, end)
    if prices.empty:
        return []
    p = prices[(prices["StockCode"] == stock.upper()) & (prices["Volume"] > 0)]
    days = p["Date"].astype(str).str[:10]
    return sorted(d for d in days.unique() if start <= d <= end)


def plan(
    stock: str,
    start: str,
    end: str,
    provider: Provider,
    refresh: bool = False,
    base: str = CACHE_DIR,
    prices: pd.DataFrame | None = None,
) -> dict:
    """Which days are missing and which requests would fetch them."""
    days = trading_days(stock, start, end, prices)
    have = {} if refresh else fetched_days(stock, base)
    missing = [d for d in days if d not in have]
    windows = provider.windows(missing)
    return {
        "stock": stock.upper(),
        "trading_days": len(days),
        "cached": len(days) - len(missing),
        "missing": len(missing),
        "windows": windows,
        "requests": len(windows) * provider.requests_per_window(),
        "_missing_days": missing,
    }


def fetch(
    stock: str,
    start: str,
    end: str,
    provider: Provider,
    *,
    refresh: bool = False,
    dry_run: bool = False,
    cap: int | None = None,
    base: str = CACHE_DIR,
    prices: pd.DataFrame | None = None,
) -> dict:
    """Fetch the missing days of [start, end] into the cache, stopping at the daily request cap."""
    stock = stock.upper()
    p = plan(stock, start, end, provider, refresh, base, prices)
    missing = p.pop("_missing_days")
    result = {**p, "provider": provider.name, "fetched_days": 0, "rows": 0, "stopped": None}
    if dry_run or not p["windows"]:
        return result
    cap = daily_cap() if cap is None else cap
    per = provider.requests_per_window()
    used = usage(base=base)["today"]
    made = 0
    for w_start, w_end in p["windows"]:
        if used + made + per > cap:
            result["stopped"] = f"daily cap {cap} reached"
            break
        try:
            rows = provider.fetch(stock, w_start, w_end)
        except ProviderError as exc:
            log_usage(provider.name, stock, w_start, w_end, per, f"error {exc.status}", base)
            made += per
            if exc.fatal:
                result["stopped"] = str(exc)
                break
            log.warning("brokerflow %s %s..%s failed: %s", stock, w_start, w_end, exc)
            continue
        made += per
        log_usage(provider.name, stock, w_start, w_end, per, "ok", base)
        days = [d for d in missing if w_start <= d <= w_end]
        write_rows(stock, rows, days, provider.name, base)
        result["fetched_days"] += len(days)
        result["rows"] += len(rows)
    result["requests_made"] = made
    return result
