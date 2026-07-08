"""tradingagents dashboard plugin — backend API routes.

Mounted at /api/plugins/tradingagents/ by the dashboard's plugin-API loader
(see hermes_cli/web_server.py::_mount_plugin_api_routes). Routes go through
the dashboard's existing session-token auth middleware like every other
``/api/plugins/...`` route — nothing extra to do here for auth.

This file is imported by path (``importlib.util.spec_from_file_location``)
as a standalone module with no package context, so it can't use the
plugin's own package-relative imports (``from . import store`` would need
``hermes_plugins.tradingagents`` to already be loaded, which isn't
guaranteed — the tool and the dashboard are loaded by two independent
subsystems). Instead it loads ``../store.py`` directly by path.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_STORE_FILE = Path(__file__).resolve().parent.parent / "store.py"
_STORE_MODULE_NAME = "hermes_tradingagents_plugin_store"


def _load_store():
    if _STORE_MODULE_NAME in sys.modules:
        return sys.modules[_STORE_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_STORE_MODULE_NAME, _STORE_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load store module from {_STORE_FILE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_STORE_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


store = _load_store()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(date: str) -> str:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail=f"invalid date: {date!r} (expected YYYY-MM-DD)")
    return date


# ---------------------------------------------------------------------------
# GET/PUT /watchlist
# ---------------------------------------------------------------------------

@router.get("/watchlist")
def get_watchlist():
    return {"tickers": store.load_watchlist()}


class WatchlistBody(BaseModel):
    tickers: list[str]


@router.put("/watchlist")
def put_watchlist(payload: WatchlistBody):
    try:
        tickers = store.save_watchlist(payload.tickers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"tickers": tickers}


# ---------------------------------------------------------------------------
# GET /history — latest run per ticker (watchlist tickers with no runs yet
# are included so the panel shows a row for everything being tracked).
# ---------------------------------------------------------------------------

@router.get("/history")
def get_history():
    latest = store.latest_by_ticker()
    watchlist = store.load_watchlist()

    tickers = sorted(set(latest.keys()) | set(watchlist))
    rows = []
    for ticker in tickers:
        entry = latest.get(ticker)
        if entry is None:
            rows.append({
                "ticker": ticker,
                "date": None,
                "created_at": None,
                "asset_type": None,
                "decision": None,
                "error": None,
                "has_report": False,
            })
        else:
            rows.append(entry)
    return {"rows": rows}


@router.get("/history/{ticker}")
def get_ticker_history(ticker: str):
    try:
        ticker = store.validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ticker": ticker, "runs": store.history_for_ticker(ticker)}


# ---------------------------------------------------------------------------
# GET /reports/{ticker}/{date} — full report markdown for the "open report"
# link/button.
# ---------------------------------------------------------------------------

@router.get("/reports/{ticker}/{date}")
def get_report(ticker: str, date: str):
    try:
        ticker = store.validate_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    date = _validate_date(date)
    content = store.read_report(ticker, date)
    if content is None:
        raise HTTPException(status_code=404, detail=f"no report for {ticker} on {date}")
    return {"ticker": ticker, "date": date, "content": content}
