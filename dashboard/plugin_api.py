"""tradingagents dashboard plugin — backend API routes.

Mounted at /api/plugins/tradingagents/ by the dashboard's plugin-API loader
(see hermes_cli/web_server.py::_mount_plugin_api_routes). Routes go through
the dashboard's existing session-token auth middleware like every other
``/api/plugins/...`` route — nothing extra to do here for auth.

This file is imported by path (``importlib.util.spec_from_file_location``)
as a standalone module with no package context, so it can't use the
plugin's own package-relative imports directly. store.py and tool.py both
get loaded as synthetic submodules of a throwaway package
(``_PKG_NAME``) rooted at the plugin directory — that's enough for
tool.py's own ``from . import store`` to resolve normally, so this file
reuses tool.py's diagnose()/run_batch() instead of re-implementing them.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
import types
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_PKG_NAME = "hermes_tradingagents_plugin_pkg"


def _ensure_pkg() -> types.ModuleType:
    if _PKG_NAME not in sys.modules:
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [str(_PLUGIN_ROOT)]
        pkg.__package__ = _PKG_NAME
        sys.modules[_PKG_NAME] = pkg
    return sys.modules[_PKG_NAME]


def _load_sibling(name: str):
    """Load ``<plugin root>/<name>.py`` as ``_PKG_NAME.<name>``.

    Registering it as an attribute of the synthetic parent package (not
    just in sys.modules) is required for ``from . import <name>`` inside
    the loaded module to resolve — Python's relative-import machinery
    checks ``hasattr(parent_pkg, name)`` before falling back to a real
    filesystem import, which would fail here since this package doesn't
    exist on disk.
    """
    pkg = _ensure_pkg()
    full_name = f"{_PKG_NAME}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, _PLUGIN_ROOT / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module from {_PLUGIN_ROOT / f'{name}.py'}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PKG_NAME
    sys.modules[full_name] = module
    setattr(pkg, name, module)
    spec.loader.exec_module(module)
    return module


# Load order matters: store must be fully executed before tool, since
# tool.py's top-level `from . import store` needs it already resolvable.
store = _load_sibling("store")
tool = _load_sibling("tool")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(date: str) -> str:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail=f"invalid date: {date!r} (expected YYYY-MM-DD)")
    return date


# ---------------------------------------------------------------------------
# GET /status — connectivity diagnostics ("will tradingagents_analyze
# actually reach TradingAgents right now?"). Delegates to tool.diagnose(),
# the same check the tool itself runs before every call.
# ---------------------------------------------------------------------------

@router.get("/status")
def get_status():
    return tool.diagnose()


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


# ---------------------------------------------------------------------------
# POST /run + GET /run/status — trigger a re-analysis from the dashboard,
# either for one security or "all" (the whole watchlist). Runs take
# minutes (multi-agent debate rounds per ticker), so this can't be a
# synchronous request/response: it enqueues a job on a single background
# worker thread and returns immediately. The dashboard polls /run/status
# and refreshes /history once a job finishes.
#
# "Queued" runs share one worker (not one thread per click) so an
# enthusiastic double-click or a "run all" plus a few individual reruns
# don't pile up N concurrent docker/local invocations fighting over the
# same LLM rate limits — jobs execute strictly one at a time, in the
# order submitted.
# ---------------------------------------------------------------------------

_job_queue: "list[dict[str, Any]]" = []  # FIFO via .pop(0); small N, simplicity over a real Queue
_jobs_by_id: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_queue_not_empty = threading.Condition(_jobs_lock)
_worker_started = False
_MAX_TRACKED_JOBS = 200


def _worker_loop() -> None:
    while True:
        with _queue_not_empty:
            while not _job_queue:
                _queue_not_empty.wait()
            job = _job_queue.pop(0)
            job["status"] = "running"
            job["started_at"] = time.time()

        try:
            result = tool.run_batch(job["tickers"], job.get("date"))
            with _jobs_lock:
                job["status"] = "done"
                job["result"] = result
        except tool.TradingAgentsRunError as exc:
            with _jobs_lock:
                job["status"] = "error"
                # Fold in output_tail (subprocess stderr/stdout tail) when
                # present — the bare message ("exited with code 2") isn't
                # actionable on its own; the tail usually has the real
                # reason (missing file, bad CLI args, provider auth, ...).
                message = str(exc)
                tail = exc.extra.get("output_tail")
                if tail:
                    message = f"{message}\n\n{tail}"
                job["error"] = message
        except Exception as exc:  # noqa: BLE001 — worker must never die
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with _jobs_lock:
                job["finished_at"] = time.time()


def _ensure_worker() -> None:
    global _worker_started
    with _jobs_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, name="tradingagents-run-worker", daemon=True).start()


def _prune_jobs_locked() -> None:
    if len(_jobs_by_id) <= _MAX_TRACKED_JOBS:
        return
    for job_id in sorted(_jobs_by_id, key=lambda j: _jobs_by_id[j]["queued_at"])[: len(_jobs_by_id) - _MAX_TRACKED_JOBS]:
        del _jobs_by_id[job_id]


class RunBody(BaseModel):
    tickers: Optional[list[str]] = None
    all: bool = False
    date: Optional[str] = None


@router.post("/run")
def post_run(payload: RunBody):
    _ensure_worker()

    if payload.all:
        tickers = store.load_watchlist()
        if not tickers:
            raise HTTPException(status_code=400, detail="Watchlist is empty — add tickers first.")
    else:
        try:
            tickers = [store.validate_ticker(t) for t in (payload.tickers or [])]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not tickers:
            raise HTTPException(status_code=400, detail="tickers is required (or set all=true).")

    job = {
        "id": uuid.uuid4().hex[:12],
        "tickers": tickers,
        "date": payload.date,
        "status": "queued",
        "queued_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
    }
    with _queue_not_empty:
        _jobs_by_id[job["id"]] = job
        _prune_jobs_locked()
        _job_queue.append(job)
        _queue_not_empty.notify()

    return {"job": job}


@router.get("/run/status")
def get_run_status():
    with _jobs_lock:
        jobs = sorted(_jobs_by_id.values(), key=lambda j: j["queued_at"], reverse=True)
    return {"jobs": jobs}
