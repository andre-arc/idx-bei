"""
Price momentum rank board: tracked deals and capital raises, plus the market's top stocks.

Momentum is measured against the whole IDX market from the stored daily stock summary:

- returns over 1M / 3M / 6M / 12M (21 / 63 / 126 / 250 trading days) on closes adjusted for
  splits and rights issues (idx.core.adjust);
- 3M excess return vs the IHSG (COMPOSITE index);
- distance from the high of the stored window, position vs the 20/50-day averages;
- traded-value attention: 20-day vs 60-day average value;
- score = percentile among all stocks, 20% 1M + 40% 3M + 40% 6M (3M stands in when 6M is missing).

``last_adjustment`` is the most recent ex-date (and factor) applied to a stock. A single-day move
beyond IDX's widest auto-rejection band (35%) left after adjustment is not explained by the data
(e.g. a gap in the history), so such tickers are flagged ``adj_warning``.
"""

from typing import Any

import pandas as pd

from idx.core.adjust import adjusted_closes, adjustment_events
from idx.core.timeseries import read_dataset

WINDOWS = {"1M": 21, "3M": 63, "6M": 126, "12M": 250}
WEIGHTS = {"1M": 0.2, "3M": 0.4, "6M": 0.4}
# beyond IDX auto-rejection limits (35% for Rp50-200) even after adjustment → unexplained;
# the margin keeps exact +35% limit-up days (162/120) out despite float rounding
MAX_DAILY_MOVE = 0.355
MIN_FLAG_PRICE = 50  # below Rp50 one tick is >2% and Rp1 → 2 is +100%: limits don't apply
MIN_HISTORY = 30
STALE_DAYS = 10  # last price older than this vs the market → suspended / not trading


def _ret(s: pd.Series, n: int) -> float | None:
    s = s.dropna()
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


def compute_momentum(
    prices: pd.DataFrame, index: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, dict]:
    """Momentum metrics for every stock.

    prices: columns StockCode, Date, Close, Value, Previous (one row per stock per day; without
            Previous the closes are used unadjusted).
    index:  columns IndexCode, Date, Close (optional; IHSG = COMPOSITE).
    Returns (metrics indexed by stock code, market summary).
    """
    df = prices[["StockCode", "Date", "Close", "Value"]].copy()
    df["Date"] = pd.to_datetime(df["Date"].astype(str).str[:10])
    df = df[df["Close"] > 0]
    px = adjusted_closes(prices)
    events = adjustment_events(prices).sort_values("Date").groupby("StockCode").last()
    val = df.pivot_table(index="Date", columns="StockCode", values="Value").sort_index().fillna(0)
    last = px.index.max()

    ihsg = pd.Series(dtype=float)
    if index is not None and not index.empty:
        ix = index.copy()
        ix["Date"] = pd.to_datetime(ix["Date"].astype(str).str[:10])
        ihsg = (
            ix[ix["IndexCode"].str.upper() == "COMPOSITE"].set_index("Date")["Close"].sort_index()
        )
    ihsg_ret = {k: _ret(ihsg, n) for k, n in WINDOWS.items()} if len(ihsg) else {}

    rows = []
    for code in px.columns:
        s = px[code].dropna()
        if len(s) < MIN_HISTORY or s.index[-1] < last - pd.Timedelta(days=STALE_DAYS):
            continue
        r = {k: _ret(s, n) for k, n in WINDOWS.items()}
        r3, ihsg3 = r["3M"], ihsg_ret.get("3M")
        v = val[code]
        v20, v60 = v.tail(20).mean(), v.tail(60).mean()
        daily = s.pct_change()[s.shift(1) >= MIN_FLAG_PRICE].dropna()
        rows.append(
            {
                "code": code,
                "close": float(s.iloc[-1]),
                "last_adjustment": (
                    {
                        "date": events.loc[code, "Date"].date().isoformat(),
                        "factor": round(float(events.loc[code, "factor"]), 6),
                    }
                    if code in events.index
                    else None
                ),
                **{f"r{k}": x for k, x in r.items()},
                "xs3M": r3 - ihsg3 if r3 is not None and ihsg3 is not None else None,
                "from_high": float(s.iloc[-1] / s.max() - 1),
                "above_ma20": bool(s.iloc[-1] > s.tail(20).mean()),
                "above_ma50": bool(s.iloc[-1] > s.tail(50).mean()),
                "value_20_60": float(v20 / v60) if v60 > 0 else None,
                "avg_value_20d": float(v20),
                "adj_warning": bool((daily.abs() > MAX_DAILY_MOVE).any()),
            }
        )
    m = pd.DataFrame(rows).set_index("code") if rows else pd.DataFrame()
    if not m.empty:
        for k in WEIGHTS:
            m[f"p{k}"] = pd.to_numeric(m[f"r{k}"]).rank(pct=True)
        p6 = m["p6M"].fillna(m["p3M"])
        m["score"] = 100 * (
            WEIGHTS["1M"] * m["p1M"] + WEIGHTS["3M"] * m["p3M"] + WEIGHTS["6M"] * p6
        )
        m["market_rank"] = m["score"].rank(ascending=False, method="min").astype(int)
    market = {
        "as_of": last.date().isoformat() if pd.notna(last) else None,
        "first_date": px.index.min().date().isoformat() if len(px) else None,
        "trading_days": int(len(px)),
        "universe": int(len(m)),
        "ihsg": ihsg_ret,
    }
    return m, market


def _clean(v: Any) -> Any:
    """numpy scalar → plain Python (JSON-safe); NaN → None."""
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and pd.isna(v) else v


def metric_row(metrics: pd.DataFrame, code: str) -> dict:
    """One stock's momentum metrics as a JSON-safe dict (``no_price`` when it has no recent price)."""
    if metrics.empty or code not in metrics.index:
        return {"code": code, "no_price": True}
    m = metrics.loc[code]
    out = {k: _clean(m[k]) for k in metrics.columns if not k.startswith("p")}
    out["score"] = round(float(m["score"]), 1)
    return {"code": code, **out}


def build_board(
    metrics: pd.DataFrame,
    market: dict,
    deals: list[dict],
    rights: list[dict] | None = None,
    top: int = 30,
) -> dict:
    """Rank board groups: deal-tracker tickers, rights-tracker tickers and the market's top ``top``.

    Screen candidates (potential deals) are not ranked here; they are on the leads list (idx.leads).
    """
    groups: list[dict[str, Any]] = [
        {
            "key": "deals",
            "title": "Deal tracker",
            "rows": [
                {
                    **metric_row(metrics, d["target_ticker"]),
                    "context": f"{d.get('deal_type', '')} · {d.get('current_stage', '')} · {d.get('status', '')}",
                    "deal_score": (d.get("backdoor") or {}).get("score"),
                }
                for d in deals
            ],
        }
    ]
    if rights:
        groups.append(
            {
                "key": "rights",
                "title": "Rights tracker",
                "rows": [
                    {
                        **metric_row(metrics, r["ticker"]),
                        "context": f"{r.get('type', '')} · {r.get('stage', '')} · {r.get('status', '')}",
                    }
                    for r in rights
                ],
            }
        )
    if not metrics.empty:
        best = metrics["score"].sort_values(ascending=False).index[:top]
        groups.append(
            {
                "key": "market",
                "title": f"Market top {top}",
                "rows": [metric_row(metrics, c) for c in best],
            }
        )
    for g in groups:
        g["rows"].sort(key=lambda r: -(r.get("score") or -1))
    return {"market": market, "groups": groups}


def load_momentum() -> tuple[pd.DataFrame, dict | None]:
    """Momentum metrics for every stock from the stored prices; (empty, None) without price data."""
    prices = read_dataset("stock_summary")
    if prices.empty:
        return pd.DataFrame(), None
    return compute_momentum(prices, read_dataset("index_summary"))


def load_board(deals: list[dict], top: int = 30, rights: list[dict] | None = None) -> dict:
    metrics, market = load_momentum()
    if market is None:
        return {"market": None, "groups": [], "error": "no price data; run `idx backfill`"}
    return build_board(metrics, market, deals, rights, top=top)
