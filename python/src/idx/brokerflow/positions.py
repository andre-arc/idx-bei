"""
Broker position engine: each broker's position, cost and P/L for one stock over a window.

The window starts every broker at zero, so a position is "net shares bought since the start date".
Each day a broker's matched buys and sells (min(buy, sell)) are an intraday round trip, realized at
(sell avg − buy avg); only the net goes into the position, at that day's buy avg (net buy) or sell
avg (net sell). The position keeps a moving-average cost; reducing it realizes P/L against that cost.
A negative position means the broker sold more than it bought in the window (inventory from before
the window, or distribution).

Status compares the position `recent` trading days ago with now:

- ``fresh_accum``  flat or net short before, net long now
- ``avg_down`` / ``avg_up``  added to a long position below / at or above its average cost
- ``trim``         sold part of a long position at or above its average cost (profit taking)
- ``cut_loss``     sold part of a long position below its average cost
- ``exit``         was long, now flat or net short
- ``distribute``   net short and selling more;  ``cover``  net short and buying back
- ``hold``         position changed by less than HOLD_TOLERANCE;  ``inactive``  flat throughout

Caveats: one broker code is many clients, not one investor; holdings from before the window are
unknown; ex-dates (rights issues, splits) inside the window change the share count and price basis.
"""

import math

import pandas as pd

HOLD_TOLERANCE = 0.02  # change smaller than 2% of the earlier position = hold
STATUSES = (
    "fresh_accum",
    "avg_down",
    "avg_up",
    "trim",
    "cut_loss",
    "exit",
    "distribute",
    "cover",
    "hold",
    "inactive",
)


def _avg(value: float, shares: float) -> float:
    return value / shares if shares > 0 else 0.0


def apply_trade(pos: float, cost: float, qty: float, px: float) -> tuple[float, float, float]:
    """Signed position `pos` at average `cost` trades `qty` (signed) at `px`; returns pos, cost, realized."""
    if qty == 0:
        return pos, cost, 0.0
    if pos == 0 or (pos > 0) == (qty > 0):
        new = pos + qty
        return new, (abs(pos) * cost + abs(qty) * px) / abs(new), 0.0
    closing = min(abs(qty), abs(pos))
    realized = closing * (px - cost) * (1 if pos > 0 else -1)
    new = pos + qty
    if new == 0:
        return 0.0, 0.0, realized
    if (new > 0) == (pos > 0):
        return new, cost, realized
    return new, px, realized  # flipped side: remainder opened at px


def classify(p0: float, c0: float, p1: float, buy_px: float, sell_px: float) -> str:
    """Status from the position/cost `recent` days ago (p0, c0) to now (p1)."""
    if p0 == 0 and p1 == 0:
        return "inactive"
    if abs(p1 - p0) <= HOLD_TOLERANCE * abs(p0):
        return "hold"
    if p0 <= 0 < p1:
        return "fresh_accum"
    if p0 > 0:
        if p1 > p0:
            return "avg_down" if buy_px < c0 else "avg_up"
        if p1 <= 0:
            return "exit"
        return "cut_loss" if sell_px < c0 else "trim"
    return "distribute" if p1 < p0 else "cover"


def broker_positions(rows: pd.DataFrame, close: float, recent: int = 5) -> pd.DataFrame:
    """One row per broker from daily rows of one stock (providers.ROW_COLUMNS)."""
    if rows.empty:
        return pd.DataFrame()
    days = sorted(rows["date"].unique())
    cutoff = days[-recent - 1] if len(days) > recent else None  # state at end of this day = p0
    out = []
    for broker, g in rows.sort_values("date").groupby("broker"):
        pos = cost = realized = 0.0
        p0 = c0 = 0.0
        r_buy_sh = r_buy_val = r_sell_sh = r_sell_val = 0.0
        for r in g.itertuples(index=False):
            b_avg, s_avg = _avg(r.buy_value, r.buy_shares), _avg(r.sell_value, r.sell_shares)
            matched = min(r.buy_shares, r.sell_shares)
            realized += matched * (s_avg - b_avg)
            net = r.buy_shares - r.sell_shares
            pos, cost, rz = apply_trade(pos, cost, net, b_avg if net > 0 else s_avg)
            realized += rz
            if cutoff is not None and r.date <= cutoff:
                p0, c0 = pos, cost
            else:
                r_buy_sh += r.buy_shares
                r_buy_val += r.buy_value
                r_sell_sh += r.sell_shares
                r_sell_val += r.sell_value
        buy_sh, sell_sh = g["buy_shares"].sum(), g["sell_shares"].sum()
        buy_val, sell_val = g["buy_value"].sum(), g["sell_value"].sum()
        f_net = (g["f_buy_value"] - g["f_sell_value"]).sum(min_count=1)
        floating = pos * (close - cost) if pos else 0.0
        out.append(
            {
                "broker": broker,
                "status": classify(
                    p0, c0, pos, _avg(r_buy_val, r_buy_sh), _avg(r_sell_val, r_sell_sh)
                ),
                "position": pos,
                "position_value": pos * close,
                "avg_cost": cost or None,
                "floating_pl": floating,
                "floating_pct": (close / cost - 1) * (1 if pos > 0 else -1) * 100 if cost else None,
                "realized_pl": realized,
                "total_pl": floating + realized,
                "buy_shares": buy_sh,
                "sell_shares": sell_sh,
                "buy_avg": _avg(buy_val, buy_sh) or None,
                "sell_avg": _avg(sell_val, sell_sh) or None,
                "net_value": buy_val - sell_val,
                "recent_net_shares": r_buy_sh - r_sell_sh,
                "foreign_net_value": None if pd.isna(f_net) else f_net,
                "active_days": int(((g["buy_shares"] + g["sell_shares"]) > 0).sum()),
                "first_day": g["date"].iloc[0],
                "last_day": g["date"].iloc[-1],
            }
        )
    df = pd.DataFrame(out)
    total = rows["buy_shares"].sum()
    df["share_of_volume"] = (
        (df["buy_shares"] + df["sell_shares"]) / (2 * total) * 100 if total else 0.0
    )
    return df.sort_values("position_value", ascending=False, ignore_index=True)


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def summarize(
    stock: str,
    rows: pd.DataFrame,
    closes: pd.Series,
    recent: int = 5,
    adj_events: list[dict] | None = None,
) -> dict:
    """Positions plus a per-status summary, ready for JSON. `closes`: Close by 'YYYY-MM-DD' date."""
    if rows.empty or closes.empty:
        return {"stock": stock, "days": 0, "brokers": [], "status_counts": {}}
    days = sorted(rows["date"].unique())
    close = float(closes.loc[: days[-1]].iloc[-1])
    pos = broker_positions(rows, close, recent)
    counts = pos["status"].value_counts()
    long = pos[pos["position"] > 0]
    return {
        "stock": stock,
        "from": days[0],
        "to": days[-1],
        "days": len(days),
        "recent_days": min(recent, len(days)),
        "close": close,
        "first_close": float(closes.loc[days[0] :].iloc[0]),
        "status_counts": {s: int(counts.get(s, 0)) for s in STATUSES if counts.get(s, 0)},
        "net_long_value": float(long["position_value"].sum()),
        "long_avg_cost": float((long["position"] * long["avg_cost"]).sum() / long["position"].sum())
        if not long.empty
        else None,
        "foreign": bool(rows["f_buy_value"].notna().any()),
        "adjustment_events": adj_events or [],
        "brokers": [{k: _clean(v) for k, v in r.items()} for r in pos.to_dict("records")],
    }
