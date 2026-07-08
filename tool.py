"""tradingagents_analyze tool — runs TradingAgents (Docker or a local
checkout) so Hermes can request multi-symbol trading analyses.

TradingAgents (https://github.com/TauricResearch/TradingAgents) ships an
interactive-only CLI; there is no HTTP API to call. Instead this tool runs
a small non-interactive batch entry point (``scripts/batch_analyze.py``)
that must exist in the target checkout — see this plugin's README for the
one-file addition required on the TradingAgents side — either inside its
Docker container (``TRADINGAGENTS_EXEC_MODE=docker``, the default) or with
a plain Python interpreter against a local, non-Docker checkout
(``TRADINGAGENTS_EXEC_MODE=local``).

Config (environment variables, e.g. in ~/.hermes/.env or the process env):

    TRADINGAGENTS_DIR               Path to a TradingAgents checkout that
                                     contains scripts/batch_analyze.py (and,
                                     for docker mode, docker-compose.yml).
                                     Required.
    TRADINGAGENTS_EXEC_MODE          "docker" (default) or "local".
    TRADINGAGENTS_COMPOSE_SERVICE   docker mode only. Compose service to
                                     run. Default: "tradingagents".
    TRADINGAGENTS_PYTHON             local mode only. Python interpreter to
                                     run the batch script with — point this
                                     at the venv/conda env TradingAgents is
                                     installed in, e.g.
                                     "/path/to/.venv/bin/python". Default:
                                     "python3" (whatever that resolves to
                                     on PATH).
    TRADINGAGENTS_WATCHLIST         Comma-separated default tickers used when
                                     the tool is called without `tickers` AND
                                     no watchlist has been saved yet via the
                                     dashboard panel (see dashboard/). Once a
                                     dashboard watchlist exists, it wins.
    TRADINGAGENTS_TIMEOUT_SECONDS   Per-call subprocess timeout. Default: 3600.

``diagnose()`` is the single source of truth for "is this reachable" — the
tool's own preflight check and the dashboard's connectivity status card
(``dashboard/plugin_api.py::GET /status``) both call it, so they can never
disagree about whether a run would actually work.

Every run (success or failure, dashboard-triggered or not) is recorded via
``store.py`` so the dashboard panel can show the latest decision per ticker
and link to the full report — see ``dashboard/plugin_api.py``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, List

from tools.registry import tool_error, tool_result

from . import store

_TICKER_RE = re.compile(r"^[A-Za-z0-9._\-^=]{1,32}$")
_DEFAULT_TIMEOUT_SECONDS = 3600
_VALID_EXEC_MODES = {"docker", "local"}


def _tradingagents_dir() -> Path | None:
    raw = os.environ.get("TRADINGAGENTS_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def _exec_mode() -> str:
    mode = os.environ.get("TRADINGAGENTS_EXEC_MODE", "docker").strip().lower()
    return mode if mode in _VALID_EXEC_MODES else "docker"


def _python_bin() -> str:
    return os.environ.get("TRADINGAGENTS_PYTHON", "python3").strip() or "python3"


def _python_resolvable(python_bin: str) -> bool:
    # A path (has a separator, or explicitly relative/home) must exist and
    # be executable; a bare command name is resolved against PATH instead.
    if os.sep in python_bin or python_bin.startswith("~") or python_bin.startswith("."):
        candidate = Path(python_bin).expanduser()
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(python_bin) is not None


def diagnose() -> dict[str, Any]:
    """Report whether the configured TradingAgents target is reachable.

    Returns at least ``{"mode": ..., "directory": ..., "ready": bool,
    "detail": str}``; docker mode also returns ``compose_service``, local
    mode also returns ``python``.
    """
    mode = _exec_mode()
    directory = _tradingagents_dir()
    info: dict[str, Any] = {"mode": mode, "directory": str(directory) if directory else None}

    if directory is None:
        info.update(ready=False, detail="TRADINGAGENTS_DIR is not set.")
        return info
    if not directory.is_dir():
        info.update(ready=False, detail=f"TRADINGAGENTS_DIR does not exist: {directory}")
        return info

    script = directory / "scripts" / "batch_analyze.py"
    if not script.is_file():
        info.update(ready=False, detail=(
            f"scripts/batch_analyze.py not found under {directory}. Copy it from "
            "this plugin's reference/ directory into the TradingAgents checkout."
        ))
        return info

    if mode == "docker":
        if not (directory / "docker-compose.yml").is_file():
            info.update(ready=False, detail=f"No docker-compose.yml found in {directory}.")
            return info
        if shutil.which("docker") is None:
            info.update(ready=False, detail="The 'docker' binary is not on PATH.")
            return info
        service = os.environ.get("TRADINGAGENTS_COMPOSE_SERVICE", "tradingagents").strip() or "tradingagents"
        info.update(ready=True, detail="docker + docker-compose.yml + batch script found.", compose_service=service)
        return info

    # mode == "local"
    python_bin = _python_bin()
    if not _python_resolvable(python_bin):
        info.update(ready=False, detail=(
            f"Python interpreter not found: {python_bin!r}. Set TRADINGAGENTS_PYTHON "
            "to the interpreter TradingAgents is installed in."
        ))
        return info
    info.update(ready=True, detail="local python interpreter + batch script found.", python=python_bin)
    return info


def _check_tradingagents_available() -> bool:
    return bool(diagnose().get("ready"))


def _parse_tickers(raw: Any) -> List[str]:
    if raw is None:
        # Dashboard-saved watchlist wins when present; otherwise fall back
        # to the env var (bootstrap path for users who haven't opened the
        # dashboard yet).
        raw = store.load_watchlist()
        if not raw:
            watchlist = os.environ.get("TRADINGAGENTS_WATCHLIST", "")
            raw = [item.strip() for item in watchlist.split(",") if item.strip()]
    elif isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        raw = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raw = []
    return [t.upper() for t in raw]


class TradingAgentsRunError(RuntimeError):
    """Raised by run_batch() on any failure; carries structured extras
    (e.g. mode, output_tail) that callers can surface however fits them —
    tool_error() kwargs for the LLM tool, an HTTPException detail for the
    dashboard's queued /run endpoint."""

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra = extra


def run_batch(tickers: List[str], trade_date: str | None = None) -> dict:
    """Run one TradingAgents batch invocation for `tickers` and persist
    results (report + history) via store.py.

    Shared by the tradingagents_analyze tool handler and the dashboard's
    queued run worker (dashboard/plugin_api.py) — one code path for "how
    does a batch actually get run", so the two surfaces can't drift.

    Raises TradingAgentsRunError on any failure. Returns the trimmed
    summary dict (see _persist_and_summarize) on success.
    """
    diag = diagnose()
    if not diag.get("ready"):
        raise TradingAgentsRunError(
            diag.get("detail") or "TradingAgents is not reachable — check configuration.",
            mode=diag.get("mode"),
        )
    directory = Path(diag["directory"])
    mode = diag["mode"]

    if not tickers:
        raise TradingAgentsRunError(
            "No tickers given, and no watchlist is configured (dashboard panel "
            "or TRADINGAGENTS_WATCHLIST). Pass tickers explicitly, e.g. "
            "[\"AAPL\", \"NVDA\", \"BTC-USD\"]."
        )
    invalid = [t for t in tickers if not _TICKER_RE.match(t)]
    if invalid:
        raise TradingAgentsRunError(f"Invalid ticker symbol(s): {', '.join(invalid)}")

    timeout_seconds = int(os.environ.get("TRADINGAGENTS_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS))

    if mode == "docker":
        cmd = [
            "docker", "compose", "run", "--rm", "-T", diag["compose_service"],
            "python", "scripts/batch_analyze.py",
            "--tickers", ",".join(tickers),
        ]
    else:
        cmd = [diag["python"], "scripts/batch_analyze.py", "--tickers", ",".join(tickers)]
    if trade_date:
        cmd += ["--date", trade_date]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(directory),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise TradingAgentsRunError(
            f"tradingagents run timed out after {timeout_seconds}s for tickers: "
            f"{', '.join(tickers)} (mode={mode})"
        )
    except Exception as exc:  # noqa: BLE001
        raise TradingAgentsRunError(f"Failed to launch tradingagents (mode={mode}): {type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-40:])
        raise TradingAgentsRunError(
            f"tradingagents process exited with code {proc.returncode} (mode={mode})", output_tail=tail
        )

    payload = None
    for line in reversed(proc.stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            break

    if payload is None:
        tail = "\n".join(proc.stdout.splitlines()[-40:])
        raise TradingAgentsRunError("Could not find JSON output from batch_analyze.py", output_tail=tail)

    return _persist_and_summarize(payload)


def _handle_tradingagents_analyze(args: dict, **kw) -> str:
    tickers = _parse_tickers(args.get("tickers"))
    trade_date = str(args.get("date") or "").strip() or None
    try:
        result = run_batch(tickers, trade_date)
    except TradingAgentsRunError as exc:
        return tool_error(str(exc), **exc.extra)
    return tool_result(result)


def _persist_and_summarize(payload: dict) -> dict:
    """Save each result's full report + a history entry, and return a
    trimmed summary (no report bodies) for the LLM's tool-result context.

    The full report is large (multiple analyst + debate transcripts) and
    already saved to disk for the dashboard's "open full report" link, so
    there's no reason to duplicate it into the agent's context window.
    """
    now = int(time.time())
    summarized = []
    for result in payload.get("results", []):
        ticker = result.get("ticker")
        date = result.get("date")
        entry = {
            "ticker": ticker,
            "date": date,
            "created_at": now,
            "asset_type": result.get("asset_type"),
            "decision": result.get("decision"),
            "error": result.get("error"),
        }
        report_text = result.get("report")
        if ticker and date and report_text:
            try:
                store.save_report(ticker, date, report_text)
                entry["has_report"] = True
            except Exception:
                entry["has_report"] = False
        else:
            entry["has_report"] = False
        try:
            store.append_history(entry)
        except Exception:
            pass  # dashboard history is best-effort; must not fail the tool call
        summarized.append({k: v for k, v in entry.items() if k != "created_at"})

    return {"date": payload.get("date"), "results": summarized}


TRADINGAGENTS_ANALYZE_SCHEMA = {
    "name": "tradingagents_analyze",
    "description": (
        "Run the TradingAgents multi-agent LLM research pipeline for one or more "
        "tickers (via Docker or a local install, per TRADINGAGENTS_EXEC_MODE), and "
        "return each ticker's trade decision. "
        "Use for stocks (e.g. AAPL, 0700.HK), crypto (e.g. BTC-USD), or any other "
        "Yahoo Finance ticker. If no tickers are given, falls back to the "
        "watchlist configured in the tradingagents dashboard panel (or "
        "TRADINGAGENTS_WATCHLIST if no dashboard watchlist is saved yet). "
        "Each result is saved for the dashboard's history view and full-report link."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ticker symbols to analyze, e.g. [\"AAPL\", \"NVDA\", \"BTC-USD\"].",
            },
            "date": {
                "type": "string",
                "description": "Analysis date as YYYY-MM-DD. Defaults to today.",
            },
        },
        "required": [],
    },
}
