#!/usr/bin/env python3
"""Non-interactive, multi-ticker entry point for TradingAgents.

Runs ``TradingAgentsGraph.propagate()`` for each ticker and prints a single
JSON object to stdout. Intended to be invoked from outside the container
(e.g. ``docker compose run --rm -T tradingagents python scripts/batch_analyze.py
--tickers AAPL,NVDA,BTC-USD``) by an external scheduler or agent that cannot
drive the interactive ``tradingagents`` CLI.

A failure on one ticker is recorded in its result entry and does not stop
the rest of the batch.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from cli.models import AnalystType
from cli.utils import detect_asset_type, filter_analysts_for_asset_type
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers",
        required=True,
        help="Comma-separated ticker list, e.g. AAPL,NVDA,BTC-USD",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Analysis date YYYY-MM-DD (default: today)",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def analyze_one(ticker: str, trade_date: str, debug: bool) -> dict:
    asset_type = detect_asset_type(ticker)
    analysts = filter_analysts_for_asset_type(list(AnalystType), asset_type)
    config = DEFAULT_CONFIG.copy()
    graph = TradingAgentsGraph(
        selected_analysts=tuple(a.value for a in analysts),
        debug=debug,
        config=config,
    )
    _, decision = graph.propagate(ticker, trade_date, asset_type=asset_type.value)
    return {
        "ticker": ticker,
        "date": trade_date,
        "asset_type": asset_type.value,
        "decision": decision,
    }


def main() -> int:
    args = parse_args()
    trade_date = args.date or date.today().isoformat()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print(json.dumps({"date": trade_date, "results": [], "error": "no tickers provided"}))
        return 1

    results = []
    for ticker in tickers:
        try:
            results.append(analyze_one(ticker, trade_date, args.debug))
        except Exception as exc:  # noqa: BLE001 - batch must not die on one bad ticker
            results.append(
                {"ticker": ticker, "date": trade_date, "error": f"{type(exc).__name__}: {exc}"}
            )

    print(json.dumps({"date": trade_date, "results": results}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
