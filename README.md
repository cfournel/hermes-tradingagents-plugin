# hermes-tradingagents-plugin

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that
lets Hermes request runs of
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
— a multi-agent LLM trading research framework — through its Docker
container, for one or more tickers at a time.

TradingAgents' `tradingagents` CLI is interactive-only (it prompts for
ticker/date/provider), so there's no API for this plugin to call directly.
Instead it drives `docker compose run` against a small non-interactive
batch script that must exist in the TradingAgents checkout.

## 0. Install the plugin

```bash
hermes plugins install git@github.com:cfournel/hermes-tradingagents-plugin.git --enable
```

(The `owner/repo` shorthand tries an HTTPS clone, which fails with
`could not read Username for 'https://github.com'` if you don't have an
HTTPS credential helper configured — use the full SSH URL above if that
happens, assuming you have `gh`/git SSH auth set up.)

This clones the repo into `~/.hermes/plugins/tradingagents/` (the plugin's
manifest `name`, not the repo name). `hermes plugins list` /
`hermes plugins enable|disable|update` manage it from there like any other
installed plugin.

## 1. Add the batch script to TradingAgents

Copy `scripts/batch_analyze.py` from this plugin's `reference/` directory
into your TradingAgents checkout at `scripts/batch_analyze.py` (or apply it
as a patch if you're tracking the upstream repo). It wraps
`TradingAgentsGraph.propagate()` for a comma-separated ticker list and
prints one JSON object to stdout — no changes to `docker-compose.yml` or
the Dockerfile are needed since the container already has the `tradingagents`
package and its `scripts/` directory available at `/home/appuser/app`.

Sanity check it directly first:

```bash
cd /path/to/TradingAgents
docker compose run --rm -T tradingagents python scripts/batch_analyze.py \
  --tickers AAPL,NVDA --date 2026-07-06
```

You should get a single line of JSON: `{"date": "...", "results": [...]}`.

## 2. Enable the plugin

`hermes plugins install ... --enable` (above) already does this. If you
installed without `--enable`, or need to re-enable it later:

```bash
hermes plugins enable tradingagents
```

## 3. Configure it

Set these in your Hermes environment (e.g. `~/.hermes/.env`):

```bash
TRADINGAGENTS_DIR=/path/to/TradingAgents        # required: has docker-compose.yml + scripts/batch_analyze.py
TRADINGAGENTS_COMPOSE_SERVICE=tradingagents     # optional, default shown
TRADINGAGENTS_WATCHLIST=AAPL,NVDA,BTC-USD       # optional: default tickers when the tool is called with none
TRADINGAGENTS_TIMEOUT_SECONDS=3600              # optional: per-call subprocess timeout
```

`docker` must be on Hermes's `PATH` and able to reach the Docker daemon
(same requirements as running `docker compose` by hand).

## 4. Use it

Once enabled, the agent has a `tradingagents_analyze` tool:

```
tradingagents_analyze(tickers=["AAPL", "NVDA", "BTC-USD"], date="2026-07-06")
```

Each ticker runs independently inside the container; a failure on one
ticker (bad symbol, provider error, etc.) is reported in its own result
entry and does not fail the whole batch.

## 5. Run it daily

Wire a `hermes cron` job that asks the agent to use the tool on a schedule:

```bash
hermes cron create "0 9 * * *" \
  "Run tradingagents_analyze for the configured watchlist and summarize each ticker's decision (buy/hold/sell and why) in a short report." \
  --name daily-tradingagents
```

Leave `tickers` out of the prompt to fall back to the watchlist (dashboard
panel if you've saved one there, else `TRADINGAGENTS_WATCHLIST`), or name
specific symbols in the cron prompt itself.

## 6. Dashboard panel

The plugin ships a dashboard tab (`dashboard/`) — open `hermes dashboard`
and you'll get a "TradingAgents" tab with:

- A **watchlist editor** (one ticker per line): saves to the same watchlist
  `tradingagents_analyze` reads when called with no explicit tickers, so
  editing it here changes what the daily cron job covers.
- A **last analysis per security** table: date, asset type, decision, and
  an **open report** button that opens the full run report (all analyst,
  research-debate, trading, and risk-management sections) in a new tab.

Every `tradingagents_analyze` call — cron-triggered or ad hoc — writes its
result here automatically; there's nothing extra to configure. Reports and
the watchlist are stored under `~/.hermes/tradingagents/` on the Hermes
host (not inside the TradingAgents container, which is ephemeral).
