"""Shared on-disk storage for the tradingagents plugin.

Used by both the tool handler (``tool.py``, writes) and the dashboard
backend (``dashboard/plugin_api.py``, reads + watchlist writes) so the two
surfaces never drift. Everything lives under
``<hermes_home>/tradingagents/``:

    watchlist.json          {"tickers": [...]}   — dashboard-editable
    history.json             append-only list of run records (bounded)
    reports/<TICKER>/<DATE>.md   full markdown report per run

No database — this is low-volume (one run per ticker per day at most,
typically), so flat files keep the plugin dependency-free.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home

_TICKER_RE = re.compile(r"^[A-Za-z0-9._\-^=]{1,32}$")
_MAX_HISTORY_ENTRIES = 2000
_MAX_SCREEN_RUNS = 200

# Guards read-modify-write of the JSON files against concurrent runs
# (e.g. two tool calls in flight, or a tool call racing a dashboard edit).
_lock = threading.Lock()


def base_dir() -> Path:
    d = get_hermes_home() / "tradingagents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = base_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _watchlist_path() -> Path:
    return base_dir() / "watchlist.json"


def _history_path() -> Path:
    return base_dir() / "history.json"


def _safe_component(value: str) -> str:
    """Collapse a ticker/date into a filesystem-safe path component."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", value) or "_"


def validate_ticker(raw: str) -> str:
    ticker = str(raw).strip().upper()
    if not ticker or not _TICKER_RE.match(ticker):
        raise ValueError(f"invalid ticker symbol: {raw!r}")
    return ticker


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def load_watchlist() -> list[str]:
    path = _watchlist_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    tickers = data.get("tickers") if isinstance(data, dict) else None
    if not isinstance(tickers, list):
        return []
    return [str(t).strip().upper() for t in tickers if str(t).strip()]


def save_watchlist(tickers: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in tickers:
        text = str(raw).strip()
        if not text:
            continue
        ticker = validate_ticker(text)
        if ticker not in cleaned:
            cleaned.append(ticker)
    with _lock:
        _watchlist_path().write_text(
            json.dumps({"tickers": cleaned}, indent=2), encoding="utf-8"
        )
    return cleaned


def add_to_watchlist(tickers: list[str]) -> list[str]:
    """Merge `tickers` into the existing watchlist (dedup, preserve existing
    order, append new ones) — used by the screener panel's "add to
    watchlist" action, as opposed to save_watchlist's full-replace."""
    existing = load_watchlist()
    merged = list(existing)
    for raw in tickers:
        ticker = validate_ticker(str(raw))
        if ticker not in merged:
            merged.append(ticker)
    return save_watchlist(merged)


# ---------------------------------------------------------------------------
# Reports (full markdown body per ticker+date)
# ---------------------------------------------------------------------------

def report_path(ticker: str, date: str) -> Path:
    d = reports_dir() / _safe_component(ticker.upper())
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_safe_component(date)}.md"


def save_report(ticker: str, date: str, report_markdown: str) -> Path:
    path = report_path(ticker, date)
    path.write_text(report_markdown, encoding="utf-8")
    return path


def read_report(ticker: str, date: str) -> Optional[str]:
    path = report_path(ticker, date)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# History (one entry per run attempt, success or failure)
# ---------------------------------------------------------------------------

def load_history() -> list[dict[str, Any]]:
    path = _history_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def append_history(entry: dict[str, Any]) -> None:
    with _lock:
        history = load_history()
        history.append(entry)
        if len(history) > _MAX_HISTORY_ENTRIES:
            history = history[-_MAX_HISTORY_ENTRIES:]
        _history_path().write_text(json.dumps(history, indent=2), encoding="utf-8")


def history_for_ticker(ticker: str) -> list[dict[str, Any]]:
    ticker = ticker.upper()
    entries = [e for e in load_history() if str(e.get("ticker", "")).upper() == ticker]
    entries.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return entries


def latest_by_ticker() -> dict[str, dict[str, Any]]:
    """Most recent history entry per ticker, keyed by ticker."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in load_history():
        ticker = str(entry.get("ticker", "")).upper()
        if not ticker:
            continue
        current = latest.get(ticker)
        if current is None or entry.get("created_at", 0) >= current.get("created_at", 0):
            latest[ticker] = entry
    return latest


# ---------------------------------------------------------------------------
# Screen runs (stage A discovery + stage B deep-dive, bundled per run) — used
# by the dashboard's Screener panel to show past runs without re-screening.
# ---------------------------------------------------------------------------

def _screen_history_path() -> Path:
    return base_dir() / "screen_history.json"


def load_screen_history() -> list[dict[str, Any]]:
    path = _screen_history_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_screen_run(entry: dict[str, Any]) -> None:
    entry = dict(entry)
    entry.setdefault("created_at", None)
    with _lock:
        runs = load_screen_history()
        runs.append(entry)
        if len(runs) > _MAX_SCREEN_RUNS:
            runs = runs[-_MAX_SCREEN_RUNS:]
        _screen_history_path().write_text(json.dumps(runs, indent=2), encoding="utf-8")
