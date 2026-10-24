"""
Broker-summary providers (bring your own API key).

Every provider returns the same daily rows, one per (date, broker) for one stock:
``date, stock, broker, buy_freq, buy_shares, buy_value, sell_freq, sell_shares, sell_value`` plus the
foreign-investor part ``f_buy_shares, f_buy_value, f_sell_shares, f_sell_value`` (NaN when the
provider call did not include it). Shares, not lots.

- Invezgo  (INVEZGO_API_KEY): ``GET /analysis/summary/stock/{code}`` aggregates its whole range, so one
  request per trading day. Regular market only (``market=RG``) so negotiated crossings do not distort
  average prices. Foreign split costs a second request (``BROKERFLOW_FOREIGN=1``).
- Sectors  (SECTORS_API_KEY): ``GET /v2/broker-summary/{symbol}/`` returns daily rows for up to 14
  calendar days per request (1 credit), foreign split included. No market filter.
"""

import os
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from curl_cffi import requests

ROW_COLUMNS = [
    "date",
    "stock",
    "broker",
    "buy_freq",
    "buy_shares",
    "buy_value",
    "sell_freq",
    "sell_shares",
    "sell_value",
    "f_buy_shares",
    "f_buy_value",
    "f_sell_shares",
    "f_sell_value",
]
TIMEOUT = 30


class ProviderError(RuntimeError):
    """Request failed; `fatal` = stop the whole run (bad key, plan, quota)."""

    def __init__(self, message: str, status: int | None = None, fatal: bool = False):
        super().__init__(message)
        self.status = status
        self.fatal = fatal


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _check(resp, provider: str) -> None:
    if resp.status_code < 400 or resp.status_code == 404:
        return
    fatal = resp.status_code in (401, 402, 403, 429)
    try:
        detail = resp.json().get("message", "")
    except Exception:
        detail = resp.text[:200]
    raise ProviderError(f"{provider} HTTP {resp.status_code}: {detail}", resp.status_code, fatal)


def frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=ROW_COLUMNS)
    for c in ROW_COLUMNS[3:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


@dataclass
class Provider:
    name: str
    api_key: str
    max_window_days: int = 1  # calendar days one request may cover with daily rows

    def windows(self, days: list[str]) -> list[tuple[str, str]]:
        """Group sorted trading days into request windows (start, end) of at most max_window_days."""
        out: list[tuple[str, str]] = []
        for d in sorted(days):
            if out:
                start = date.fromisoformat(out[-1][0])
                if date.fromisoformat(d) - start < timedelta(days=self.max_window_days):
                    out[-1] = (out[-1][0], d)
                    continue
            out.append((d, d))
        return out

    def requests_per_window(self) -> int:
        return 1

    def fetch(self, stock: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError


class Invezgo(Provider):
    BASE = "https://api.invezgo.com"

    def __init__(self, api_key: str, foreign: bool = False, market: str = "RG"):
        super().__init__("invezgo", api_key, max_window_days=1)
        self.foreign = foreign
        self.market = market

    def requests_per_window(self) -> int:
        return 2 if self.foreign else 1

    def _get(self, stock: str, day: str, investor: str) -> list[dict]:
        resp = requests.get(
            f"{self.BASE}/analysis/summary/stock/{stock}",
            params={"from": day, "to": day, "investor": investor, "market": self.market},
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        _check(resp, self.name)
        if resp.status_code == 404:
            return []
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    def fetch(self, stock: str, start: str, end: str) -> pd.DataFrame:
        if start != end:
            raise ValueError("Invezgo aggregates its range; fetch one day at a time")
        foreign = {r["code"]: r for r in self._get(stock, start, "f")} if self.foreign else {}
        rows = []
        for r in self._get(stock, start, "all"):
            f = foreign.get(r["code"], {})
            rows.append(
                {
                    "date": start,
                    "stock": stock,
                    "broker": r["code"],
                    "buy_freq": r.get("buy_freq"),
                    "buy_shares": r.get("buy_volume"),
                    "buy_value": r.get("buy_value"),
                    "sell_freq": r.get("sell_freq"),
                    "sell_shares": r.get("sell_volume"),
                    "sell_value": r.get("sell_value"),
                    "f_buy_shares": f.get("buy_volume", 0 if self.foreign else None),
                    "f_buy_value": f.get("buy_value", 0 if self.foreign else None),
                    "f_sell_shares": f.get("sell_volume", 0 if self.foreign else None),
                    "f_sell_value": f.get("sell_value", 0 if self.foreign else None),
                }
            )
        return frame(rows)


class Sectors(Provider):
    BASE = "https://api.sectors.app"

    def __init__(self, api_key: str):
        super().__init__("sectors", api_key, max_window_days=14)

    def fetch(self, stock: str, start: str, end: str) -> pd.DataFrame:
        resp = requests.get(
            f"{self.BASE}/v2/broker-summary/{stock}/",
            params={"start": start, "end": end},
            headers={"Authorization": self.api_key, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        _check(resp, self.name)
        if resp.status_code == 404:
            return frame([])
        rows = []
        for day in resp.json().get("data", []):
            for r in day.get("summary", []):
                lots = {k: _num(r.get(k)) * 100 for k in ("blot", "slot", "f_blot", "f_slot")}
                rows.append(
                    {
                        "date": day["date"],
                        "stock": stock,
                        "broker": r["broker_code"],
                        "buy_freq": r.get("bfreq"),
                        "buy_shares": lots["blot"],
                        "buy_value": r.get("bval"),
                        "sell_freq": r.get("sfreq"),
                        "sell_shares": lots["slot"],
                        "sell_value": r.get("sval"),
                        # null = no foreign trades that day for this broker
                        "f_buy_shares": 0 if r.get("f_blot") is None else lots["f_blot"],
                        "f_buy_value": r.get("f_bval") or 0,
                        "f_sell_shares": 0 if r.get("f_slot") is None else lots["f_slot"],
                        "f_sell_value": r.get("f_sval") or 0,
                    }
                )
        return frame(rows)


def get_provider(name: str | None = None) -> Provider | None:
    """Provider from the environment: BROKERFLOW_PROVIDER, else whichever key is set (Invezgo first)."""
    name = (name or os.environ.get("BROKERFLOW_PROVIDER", "")).strip().lower()
    inv, sec = os.environ.get("INVEZGO_API_KEY", ""), os.environ.get("SECTORS_API_KEY", "")
    if name == "invezgo" or (not name and inv):
        if not inv:
            return None
        return Invezgo(inv, foreign=os.environ.get("BROKERFLOW_FOREIGN", "0") == "1")
    if name == "sectors" or (not name and sec):
        return Sectors(sec) if sec else None
    return None
