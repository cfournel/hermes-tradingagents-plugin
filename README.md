# hermes-tradingagents-plugin

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that
lets Hermes request runs of
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
— a multi-agent LLM trading research framework — for one or more tickers
at a time, whether your TradingAgents install runs in **Docker** or as a
**local (non-Docker) checkout**.

TradingAgents' `tradingagents` CLI is interactive-only (it prompts for
ticker/date/provider), so there's no API for this plugin to call directly.
Instead it runs a small non-interactive batch script that must exist in
the target checkout, either inside the Docker container or with a plain
Python interpreter against a local install — see [Reachability](#reachability)
below for how to point the plugin at whichever one you actually run, and
how to verify it can actually reach it before relying on a cron job.

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
prints one JSON object (including the full multi-section report, not just
the one-line decision) to stdout.

Sanity check it directly first, matching however you actually run
TradingAgents:

```bash
# Docker — note --entrypoint python: the image's ENTRYPOINT is the
# `tradingagents` CLI itself, so without overriding it your command gets
# appended as *arguments* to that CLI instead of replacing it.
cd /path/to/TradingAgents
docker compose run --rm -T --entrypoint python tradingagents scripts/batch_analyze.py \
  --tickers AAPL,NVDA --date 2026-07-06

# If this is the first run since adding batch_analyze.py, rebuild first —
# an existing image built before the file was added won't have it:
#   docker compose build tradingagents

# Local (no Docker) — use the interpreter TradingAgents is installed in
cd /path/to/TradingAgents
/path/to/.venv/bin/python scripts/batch_analyze.py \
  --tickers AAPL,NVDA --date 2026-07-06
```

Either way you should get a single line of JSON:
`{"date": "...", "results": [...]}`. If that line doesn't print cleanly,
the plugin won't be able to parse the run's output either — fix it here
before wiring up the plugin.

## 2. Enable the plugin

`hermes plugins install ... --enable` (above) already does this. If you
installed without `--enable`, or need to re-enable it later:

```bash
hermes plugins enable tradingagents
```

## 3. Configure it

Set these in your Hermes environment (e.g. `~/.hermes/.env`):

```bash
TRADINGAGENTS_DIR=/path/to/TradingAgents        # required: has scripts/batch_analyze.py
TRADINGAGENTS_EXEC_MODE=docker                  # "docker" (default) or "local"

# docker mode only:
TRADINGAGENTS_COMPOSE_SERVICE=tradingagents     # optional, default shown
                                                 # `docker` must be on Hermes's PATH and able to reach the daemon

# local mode only:
TRADINGAGENTS_PYTHON=/path/to/.venv/bin/python  # interpreter TradingAgents is installed in — default "python3" on PATH

TRADINGAGENTS_WATCHLIST=AAPL,NVDA,BTC-USD       # optional: default tickers when the tool is called with none
TRADINGAGENTS_TIMEOUT_SECONDS=3600              # optional: per-call subprocess timeout
```

## 4. Use it

Once enabled, the agent has a `tradingagents_analyze` tool:

```
tradingagents_analyze(tickers=["AAPL", "NVDA", "BTC-USD"], date="2026-07-06")
```

All tickers in one call run inside a single batch invocation (one
`docker compose run` or one local process, not one per ticker); a failure
on one ticker (bad symbol, provider error, etc.) is reported in its own
result entry and does not fail the rest of the batch.

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

## 6. Screener — discover new candidates

`tradingagents_analyze` (and the dashboard's Run buttons) require you to
already know which tickers you care about. `tradingagents_screen` is for
the opposite case — "find me something new":

```
tradingagents_screen(asset_classes=["stock", "crypto"], risk="medium", horizon="position", limit=10, price_range="5_50")
```

It runs in two stages:

1. **Discovery** (cheap, no LLM) — filters a broad universe by risk level
   and trade horizon:
   - **stock** → Yahoo Finance's own screener (`yfinance`'s `yf.screen()`),
     using beta as the risk proxy and short-term vs 52-week momentum for
     swing vs hold.
   - **crypto** → CoinGecko's public markets endpoint (no API key), using
     market-cap rank as the risk proxy and 24h vs 7d change for swing vs
     hold.
   - **commodity** → a small static list of liquid futures (gold, oil,
     copper, etc.), ranked by realized momentum over the matching window.
2. **Deep dive** (existing pipeline) — runs the same TradingAgents
   multi-agent analysis as `tradingagents_analyze` on the shortlist from
   stage 1, so each result gets a sentiment and buy/sell/hold direction
   alongside the screen metrics that surfaced it.

`risk` is `low` / `medium` / `high`. `horizon` is `swing` (a quick trade,
a few days) or `position` (a hold, multi-month trend). `price_range` filters
by current price: `all`, `pennies` (under $5), `5_50`, `51_100`, `101_300`,
or `301_plus`. `limit` caps candidates *per asset class* before the deep
dive — keep this small (5–15); each candidate costs a full multi-agent run
in stage 2.

**Two discovery paths, automatic fallback:**

| Path | Requires | When it's used |
|---|---|---|
| Native (fast, in-process) | `yfinance` importable directly in Hermes's own Python environment (`pip install yfinance`) | Preferred whenever available |
| Fallback (subprocess) | Same `TRADINGAGENTS_DIR`/`TRADINGAGENTS_EXEC_MODE` as `tradingagents_analyze`, plus `scripts/screen_candidates.py` copied into the checkout (see `reference/screen_candidates.py`, same as `batch_analyze.py` in step 1) | Used automatically when `yfinance` isn't importable in Hermes's environment — TradingAgents already depends on it, so this path works wherever TradingAgents itself runs |

The dashboard's connectivity status card shows which path is active (and
why, if neither is ready). No new required env vars — the fallback reuses
`TRADINGAGENTS_DIR`/`TRADINGAGENTS_EXEC_MODE`/etc. from step 3.

Results from a screen are also added to the dashboard's screen history and
can be added to the watchlist individually (or all at once) from there.

**Filter selections persist** across a page refresh (stored in the
browser's `sessionStorage`, keyed per tab) — reopening the Screener panel
shows the same asset classes / risk / horizon / price range / limit you
last had selected.

**Scheduling a recurring screen**: the Screener panel has a frequency
dropdown (daily / weekly / monthly) and a "Schedule screen" button next to
Run screen. Clicking it creates a Hermes cron job (using the filters
currently selected) that runs `tradingagents_screen` on that cadence —
daily/weekly/monthly at 09:00. Only one screener cron job is tracked at a
time: once one exists, the button grays out and a "Remove schedule" button
appears next to it. This uses Hermes's own cron subsystem directly (same
one `hermes cron list` shows), so the scheduled runs only fire while the
Hermes gateway is running.

## 7. Dashboard panel

The plugin ships a dashboard tab (`dashboard/`) — open `hermes dashboard`
and you'll get a "TradingAgents" tab with:

- A **watchlist editor** (one ticker per line): saves to the same watchlist
  `tradingagents_analyze` reads when called with no explicit tickers, so
  editing it here changes what the daily cron job covers.
- A **last analysis per security** table: date, asset type, decision, and
  an **open report** button that opens the full run report (all analyst,
  research-debate, trading, and risk-management sections) in a new tab.
- A **Run** button per security, and a **Run all (queued)** button for the
  whole watchlist — triggers a real `tradingagents_analyze` run directly
  from the dashboard, no cron job or agent chat needed. Runs take minutes
  (multi-agent debate per ticker), so these don't block the UI: clicking
  Run enqueues a job and the row shows Queued/Running until it lands,
  then the table refreshes itself. All triggered runs — individual or
  "run all" — share one worker and execute strictly one at a time, so
  mashing the buttons queues work instead of firing overlapping
  docker/local invocations at once.
- A **Screener** panel (see step 6): pick risk / asset class(es) / horizon,
  run a screen, and add any result to the watchlist with one click (or all
  of them at once). Shares the same worker queue as Run/Run all, so a
  screen in flight and an analyze run don't race each other either.

Every `tradingagents_analyze` call — cron-triggered, dashboard-triggered,
or ad hoc from agent chat — writes its result here automatically; there's
nothing extra to configure. Reports and the watchlist are stored under
`~/.hermes/tradingagents/` on the Hermes host (not inside the
TradingAgents container, which is ephemeral).

The dashboard tab also has a **connectivity status card** at the top — see
[Reachability](#reachability) below.

## Reachability

"Will `tradingagents_analyze` actually reach TradingAgents?" depends on
`TRADINGAGENTS_DIR` + `TRADINGAGENTS_EXEC_MODE` matching how you actually
run it:

| You run TradingAgents via... | Set `TRADINGAGENTS_EXEC_MODE` to... | What the plugin needs to find |
|---|---|---|
| `docker compose run ...` (the default) | `docker` (or omit — it's the default) | `docker` on Hermes's `PATH`, a reachable Docker daemon, and `docker-compose.yml` + `scripts/batch_analyze.py` in `TRADINGAGENTS_DIR` |
| A local venv/conda checkout, no Docker | `local` | `scripts/batch_analyze.py` in `TRADINGAGENTS_DIR`, and `TRADINGAGENTS_PYTHON` resolving to the interpreter TradingAgents is actually installed in |

**Don't guess — check.** Open the TradingAgents dashboard tab: the status
card at the top reads Reachable/Not reachable, the resolved mode +
directory, and (if unreachable) exactly what's missing. It calls
`GET /api/plugins/tradingagents/status`, which runs the exact same check
`tradingagents_analyze` runs before every call (`tool.py::diagnose()`) —
so there's no gap between "the card says reachable" and "the tool will
actually run".

**Same-host assumption.** The plugin runs `subprocess.run(...)` on
whatever machine Hermes itself runs on, with `cwd=TRADINGAGENTS_DIR` — it
does not do anything over the network. That means Hermes and TradingAgents
(Docker or local) need to be **on the same machine**, with `TRADINGAGENTS_DIR`
being a real local path Hermes's process can `cd` into. Docker's own
`DOCKER_HOST` can point `docker compose` at a remote daemon if you've set
that up independently, but this plugin doesn't add any remote-execution
support (no SSH, no remote Docker context config) — open an issue if you
need that.
