"""tradingagents_analyze tool — shells out to the TradingAgents Docker
container so Hermes can request multi-symbol trading analyses.

TradingAgents (https://github.com/TauricResearch/TradingAgents) ships an
interactive-only CLI; there is no HTTP API to call. Instead this tool drives
``docker compose run`` against a small non-interactive batch entry point
(``scripts/batch_analyze.py``) that must exist in the target checkout — see
this plugin's README for the one-file addition required on the TradingAgents
side.

Config (environment variables, e.g. in ~/.hermes/.env or the process env):

    TRADINGAGENTS_DIR               Path to a TradingAgents checkout that
                                     contains docker-compose.yml and
                                     scripts/batch_analyze.py. Required.
    TRADINGAGENTS_COMPOSE_SERVICE   Compose service to run. Default: "tradingagents".
    TRADINGAGENTS_WATCHLIST         Comma-separated default tickers used when
                                     the tool is called without `tickers`
                                     (handy for a daily `hermes cron` job).
    TRADINGAGENTS_TIMEOUT_SECONDS   Per-call subprocess timeout. Default: 3600.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, List

from tools.registry import tool_error, tool_result

_TICKER_RE = re.compile(r"^[A-Za-z0-9._\-^=]{1,32}$")
_DEFAULT_TIMEOUT_SECONDS = 3600


def _tradingagents_dir() -> Path | None:
    raw = os.environ.get("TRADINGAGENTS_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def _check_tradingagents_available() -> bool:
    directory = _tradingagents_dir()
    if directory is None:
        return False
    if not (directory / "docker-compose.yml").is_file():
        return False
    return shutil.which("docker") is not None


def _parse_tickers(raw: Any) -> List[str]:
    if raw is None:
        watchlist = os.environ.get("TRADINGAGENTS_WATCHLIST", "")
        raw = [item.strip() for item in watchlist.split(",") if item.strip()]
    elif isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, list):
        raw = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raw = []
    return [t.upper() for t in raw]


def _handle_tradingagents_analyze(args: dict, **kw) -> str:
    directory = _tradingagents_dir()
    if directory is None:
        return tool_error(
            "TRADINGAGENTS_DIR is not set. Point it at a TradingAgents checkout "
            "(containing docker-compose.yml and scripts/batch_analyze.py)."
        )
    if not (directory / "docker-compose.yml").is_file():
        return tool_error(f"No docker-compose.yml found in TRADINGAGENTS_DIR ({directory}).")
    if shutil.which("docker") is None:
        return tool_error("The 'docker' binary is not on PATH.")

    tickers = _parse_tickers(args.get("tickers"))
    if not tickers:
        return tool_error(
            "No tickers given and TRADINGAGENTS_WATCHLIST is not set. "
            "Pass tickers explicitly, e.g. [\"AAPL\", \"NVDA\", \"BTC-USD\"]."
        )
    invalid = [t for t in tickers if not _TICKER_RE.match(t)]
    if invalid:
        return tool_error(f"Invalid ticker symbol(s): {', '.join(invalid)}")

    trade_date = str(args.get("date") or "").strip() or None
    service = os.environ.get("TRADINGAGENTS_COMPOSE_SERVICE", "tradingagents").strip() or "tradingagents"
    timeout_seconds = int(os.environ.get("TRADINGAGENTS_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS))

    cmd = [
        "docker", "compose", "run", "--rm", "-T", service,
        "python", "scripts/batch_analyze.py",
        "--tickers", ",".join(tickers),
    ]
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
        return tool_error(
            f"tradingagents batch run timed out after {timeout_seconds}s for tickers: {', '.join(tickers)}"
        )
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"Failed to launch docker compose: {type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-40:])
        return tool_error(f"tradingagents container exited with code {proc.returncode}", output_tail=tail)

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
        return tool_error("Could not find JSON output from batch_analyze.py", output_tail=tail)

    return tool_result(payload)


TRADINGAGENTS_ANALYZE_SCHEMA = {
    "name": "tradingagents_analyze",
    "description": (
        "Run the TradingAgents multi-agent LLM research pipeline for one or more "
        "tickers via its Docker container, and return each ticker's trade decision. "
        "Use for stocks (e.g. AAPL, 0700.HK), crypto (e.g. BTC-USD), or any other "
        "Yahoo Finance ticker. If no tickers are given, falls back to the "
        "TRADINGAGENTS_WATCHLIST configured on the host."
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
