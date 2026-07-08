"""TradingAgents integration plugin — bundled, opt-in.

Registers ``tradingagents_analyze`` so Hermes can request multi-symbol runs
of TauricResearch/TradingAgents through its Docker container. See README.md
in this directory for setup (including the one-file addition required on
the TradingAgents side) and for wiring a daily ``hermes cron`` job.
"""

from __future__ import annotations

from plugins.tradingagents.tool import (
    TRADINGAGENTS_ANALYZE_SCHEMA,
    _check_tradingagents_available,
    _handle_tradingagents_analyze,
)


def register(ctx) -> None:
    ctx.register_tool(
        name="tradingagents_analyze",
        toolset="tradingagents",
        schema=TRADINGAGENTS_ANALYZE_SCHEMA,
        handler=_handle_tradingagents_analyze,
        check_fn=_check_tradingagents_available,
        emoji="📈",
    )
