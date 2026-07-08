"""TradingAgents integration plugin — installed via `hermes plugins install`.

Registers ``tradingagents_analyze`` so Hermes can request multi-symbol runs
of TauricResearch/TradingAgents through its Docker container, and persists
each run's decision + full report so the bundled dashboard panel (see
``dashboard/``) can list history and link to full reports. See README.md
in this directory for setup and for wiring a daily ``hermes cron`` job.

Imports below are relative (``from .tool import ...``, not
``plugins.tradingagents.tool``): the plugin loader imports this file as
``hermes_plugins.tradingagents`` regardless of whether it lives in the
bundled ``plugins/`` tree or an installed ``~/.hermes/plugins/`` checkout,
so an absolute ``plugins.tradingagents`` import only works by accident when
the plugin happens to also exist inside the host's own ``plugins/`` package.
"""

from __future__ import annotations

from .tool import (
    TRADINGAGENTS_ANALYZE_SCHEMA,
    TRADINGAGENTS_SCREEN_SCHEMA,
    _check_screener_available,
    _check_tradingagents_available,
    _handle_tradingagents_analyze,
    _handle_tradingagents_screen,
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
    ctx.register_tool(
        name="tradingagents_screen",
        toolset="tradingagents",
        schema=TRADINGAGENTS_SCREEN_SCHEMA,
        handler=_handle_tradingagents_screen,
        check_fn=_check_screener_available,
        emoji="🔎",
    )
