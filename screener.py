"""In-process candidate discovery ("screener") — stage A of tradingagents_screen.

Preferred path when ``yfinance`` is importable directly in Hermes's own
Python environment: runs here with no subprocess, no TRADINGAGENTS_DIR
dependency. When it isn't importable, ``tool.py::run_screen`` falls back to
shelling out to ``scripts/screen_candidates.py`` inside the configured
TradingAgents checkout (which always has yfinance, since TradingAgents
depends on it) — see that module's docstring for the shared contract.

Deliberately duplicates tradingagents/dataflows/screener.py's logic rather
than importing across repos: these are two independently installed/updated
packages, and the module is small enough that keeping the shapes in sync
by hand is simpler than a shared dependency.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

VALID_RISK_LEVELS = {"low", "medium", "high"}
VALID_HORIZONS = {"swing", "position"}
VALID_ASSET_CLASSES = {"stock", "crypto", "commodity"}

_COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

COMMODITY_UNIVERSE = [
    {"ticker": "GC=F", "name": "Gold", "risk_tier": "low"},
    {"ticker": "SI=F", "name": "Silver", "risk_tier": "medium"},
    {"ticker": "PL=F", "name": "Platinum", "risk_tier": "medium"},
    {"ticker": "PA=F", "name": "Palladium", "risk_tier": "high"},
    {"ticker": "HG=F", "name": "Copper", "risk_tier": "medium"},
    {"ticker": "CL=F", "name": "Crude Oil (WTI)", "risk_tier": "high"},
    {"ticker": "NG=F", "name": "Natural Gas", "risk_tier": "high"},
    {"ticker": "ZC=F", "name": "Corn", "risk_tier": "medium"},
    {"ticker": "ZS=F", "name": "Soybeans", "risk_tier": "medium"},
    {"ticker": "ZW=F", "name": "Wheat", "risk_tier": "medium"},
    {"ticker": "KC=F", "name": "Coffee", "risk_tier": "high"},
]


def native_available() -> bool:
    """Whether the in-process path can run at all (yfinance importable)."""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return False
    return True


def _equity_beta_bounds(risk: str) -> tuple[float, float]:
    return {
        "low": (0.0, 1.0),
        "medium": (1.0, 1.8),
        "high": (1.8, 10.0),
    }[risk]


def screen_equities(risk: str, horizon: str, limit: int = 20) -> list[dict[str, Any]]:
    import yfinance as yf
    from yfinance import EquityQuery

    beta_lo, beta_hi = _equity_beta_bounds(risk)
    sort_field = "percentchange" if horizon == "swing" else "fiftytwowkpercentchange"

    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("btwn", ["beta", beta_lo, beta_hi]),
        EquityQuery("gte", ["intradaymarketcap", 300_000_000]),
        EquityQuery("gt", ["dayvolume", 100_000]),
    ])
    result = yf.screen(query, sortField=sort_field, sortAsc=False, size=limit)
    quotes = result.get("quotes", []) if isinstance(result, dict) else []

    candidates = []
    for q in quotes[:limit]:
        candidates.append({
            "ticker": q.get("symbol"),
            "asset_type": "stock",
            "source": "yfinance_screen",
            "metrics": {
                "beta": q.get("beta"),
                "percent_change": q.get("regularMarketChangePercent"),
                "fifty_two_week_change": q.get("fiftyTwoWeekChangePercent"),
                "market_cap": q.get("marketCap"),
                "sector": q.get("sector"),
            },
        })
    return candidates


def _coingecko_risk_bounds(risk: str) -> tuple[int, int]:
    return {
        "low": (1, 10),
        "medium": (11, 100),
        "high": (101, 250),
    }[risk]


def screen_crypto(risk: str, horizon: str, limit: int = 20) -> list[dict[str, Any]]:
    import requests

    min_rank, max_rank = _coingecko_risk_bounds(risk)
    change_field = "price_change_percentage_24h_in_currency" if horizon == "swing" \
        else "price_change_percentage_7d_in_currency"

    resp = requests.get(
        _COINGECKO_MARKETS_URL,
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": max_rank,
            "page": 1,
            "price_change_percentage": "24h,7d",
        },
        timeout=15,
    )
    resp.raise_for_status()
    coins = resp.json()

    banded = [c for c in coins[min_rank - 1:max_rank] if c.get(change_field) is not None]
    banded.sort(key=lambda c: c[change_field], reverse=True)

    candidates = []
    for c in banded[:limit]:
        candidates.append({
            "ticker": f"{c['symbol'].upper()}-USD",
            "asset_type": "crypto",
            "source": "coingecko",
            "metrics": {
                "market_cap_rank": c.get("market_cap_rank"),
                "price_change_24h_pct": c.get("price_change_percentage_24h_in_currency"),
                "price_change_7d_pct": c.get("price_change_percentage_7d_in_currency"),
                "market_cap": c.get("market_cap"),
            },
        })
    return candidates


def screen_commodities(risk: str, horizon: str, limit: int = 20) -> list[dict[str, Any]]:
    tier_universe = [c for c in COMMODITY_UNIVERSE if c["risk_tier"] == risk]
    period = "5d" if horizon == "swing" else "6mo"

    try:
        import yfinance as yf
    except ImportError:
        return [
            {"ticker": c["ticker"], "asset_type": "commodity", "source": "static_list",
             "metrics": {"name": c["name"]}}
            for c in tier_universe[:limit]
        ]

    scored = []
    for c in tier_universe:
        try:
            hist = yf.Ticker(c["ticker"]).history(period=period)
            if hist.empty:
                continue
            pct_change = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
        except Exception as exc:  # noqa: BLE001
            logger.warning("commodity screen: failed to fetch %s: %s", c["ticker"], exc)
            continue
        scored.append({
            "ticker": c["ticker"],
            "asset_type": "commodity",
            "source": "yfinance_history",
            "metrics": {"name": c["name"], "percent_change": round(float(pct_change), 2)},
        })

    scored.sort(key=lambda x: x["metrics"]["percent_change"], reverse=True)
    return scored[:limit] if scored else [
        {"ticker": c["ticker"], "asset_type": "commodity", "source": "static_list",
         "metrics": {"name": c["name"]}}
        for c in tier_universe[:limit]
    ]


def discover(
    asset_classes: list[str],
    risk: str,
    horizon: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if risk not in VALID_RISK_LEVELS:
        raise ValueError(f"risk must be one of {sorted(VALID_RISK_LEVELS)}, got {risk!r}")
    if horizon not in VALID_HORIZONS:
        raise ValueError(f"horizon must be one of {sorted(VALID_HORIZONS)}, got {horizon!r}")
    invalid_classes = set(asset_classes) - VALID_ASSET_CLASSES
    if invalid_classes:
        raise ValueError(f"invalid asset class(es): {sorted(invalid_classes)}")

    results: list[dict[str, Any]] = []
    for asset_class in asset_classes:
        if asset_class == "stock":
            results.extend(screen_equities(risk, horizon, limit))
        elif asset_class == "crypto":
            results.extend(screen_crypto(risk, horizon, limit))
        elif asset_class == "commodity":
            results.extend(screen_commodities(risk, horizon, limit))
    return results
