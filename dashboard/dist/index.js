/**
 * TradingAgents — Dashboard Plugin
 *
 * Shows the latest TradingAgents decision per watched security, a text
 * area to edit the watchlist (persisted via the plugin backend and read
 * by the `tradingagents_analyze` tool when it's called with no explicit
 * tickers — e.g. from a daily `hermes cron` job), and a link to open each
 * run's full report (all analyst + debate + risk sections).
 *
 * Plain IIFE, no build step — uses window.__HERMES_PLUGIN_SDK__ for React
 * + shared UI primitives, matching the pattern established by the kanban
 * dashboard plugin.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Label } = SDK.components;
  const { useState, useEffect, useCallback, useMemo } = SDK.hooks;
  const { cn, timeAgo } = SDK.utils;

  const API = "/api/plugins/tradingagents";

  function parseApiErrorMessage(err) {
    return String((err && err.message) || err || "Request failed");
  }

  function fetchWatchlist() {
    return SDK.fetchJSON(`${API}/watchlist`);
  }

  function saveWatchlist(tickers) {
    return SDK.fetchJSON(`${API}/watchlist`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: tickers }),
    });
  }

  function fetchHistory() {
    return SDK.fetchJSON(`${API}/history`);
  }

  function fetchStatus() {
    return SDK.fetchJSON(`${API}/status`);
  }

  function fetchReport(ticker, date) {
    return SDK.fetchJSON(`${API}/reports/${encodeURIComponent(ticker)}/${encodeURIComponent(date)}`);
  }

  function postRun(body) {
    return SDK.fetchJSON(`${API}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function fetchRunStatus() {
    return SDK.fetchJSON(`${API}/run/status`);
  }

  function postScreen(body) {
    return SDK.fetchJSON(`${API}/screen`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function fetchScreenStatus() {
    return SDK.fetchJSON(`${API}/screen/status`);
  }

  function fetchScreenHistory() {
    return SDK.fetchJSON(`${API}/screen/history`);
  }

  function postWatchlistAdd(tickers) {
    return SDK.fetchJSON(`${API}/watchlist/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: tickers }),
    });
  }

  function fetchScreenCron() {
    return SDK.fetchJSON(`${API}/screen/cron`);
  }

  function postScreenCron(body) {
    return SDK.fetchJSON(`${API}/screen/cron`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function deleteScreenCron() {
    return SDK.fetchJSON(`${API}/screen/cron`, { method: "DELETE" });
  }

  function fetchWatchlistCron() {
    return SDK.fetchJSON(`${API}/watchlist/cron`);
  }

  function postWatchlistCron(body) {
    return SDK.fetchJSON(`${API}/watchlist/cron`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function deleteWatchlistCron() {
    return SDK.fetchJSON(`${API}/watchlist/cron`, { method: "DELETE" });
  }

  const RUN_POLL_INTERVAL_MS = 3000;
  const ASSET_CLASSES = ["stock", "crypto", "commodity"];
  const SCREENER_FILTERS_STORAGE_KEY = "hermes-tradingagents-screener-filters";

  function loadStoredScreenerFilters() {
    try {
      const raw = window.sessionStorage.getItem(SCREENER_FILTERS_STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function saveStoredScreenerFilters(filters) {
    try {
      window.sessionStorage.setItem(SCREENER_FILTERS_STORAGE_KEY, JSON.stringify(filters));
    } catch (e) { /* sessionStorage unavailable (private mode, quota) — filters just won't persist */ }
  }

  // Parse a textarea's free-form contents (newline- or comma-separated,
  // any mix of whitespace) into a clean, deduped, upper-cased ticker list.
  function parseTickerText(text) {
    const seen = new Set();
    const out = [];
    String(text || "")
      .split(/[\n,]/)
      .map(function (s) { return s.trim().toUpperCase(); })
      .filter(Boolean)
      .forEach(function (t) {
        if (!seen.has(t)) { seen.add(t); out.push(t); }
      });
    return out;
  }

  function decisionBadgeVariant(decision) {
    const text = String(decision || "").toUpperCase();
    if (text.indexOf("BUY") !== -1) return "default";
    if (text.indexOf("SELL") !== -1) return "destructive";
    return "secondary";
  }

  function StatusCard(props) {
    const status = props.status;
    if (!status) {
      return null;
    }
    const ready = !!status.ready;
    const modeLabel = status.mode === "local" ? "local (no Docker)" : "docker";
    return h(Card, {
      className: cn(
        "hermes-tradingagents-status",
        ready ? "hermes-tradingagents-status-ok" : "hermes-tradingagents-status-bad",
      ),
    },
      h(CardContent, { className: "flex items-center justify-between gap-3 py-3" },
        h("div", { className: "flex items-center gap-2" },
          h(Badge, { variant: ready ? "default" : "destructive" }, ready ? "Reachable" : "Not reachable"),
          h("span", { className: "text-sm" }, `mode: ${modeLabel}`),
          status.directory
            ? h("span", { className: "text-xs text-muted-foreground" }, status.directory)
            : null,
        ),
        h(Button, { size: "sm", variant: "outline", onClick: props.onRefresh }, "Recheck"),
      ),
      h(CardContent, { className: "pt-0 text-xs text-muted-foreground" }, status.detail),
      status.screener
        ? h(CardContent, { className: "pt-0 text-xs text-muted-foreground" },
            `Screener: ${status.screener.ready ? `ready (${status.screener.path})` : "not ready"} — ${status.screener.detail}`,
          )
        : null,
    );
  }

  function WatchlistEditor(props) {
    const [text, setText] = useState(props.tickers.join("\n"));
    const [saving, setSaving] = useState(false);
    const [err, setErr] = useState(null);

    useEffect(function () {
      setText(props.tickers.join("\n"));
    }, [props.tickers]);

    const dirty = useMemo(function () {
      return parseTickerText(text).join(",") !== props.tickers.join(",");
    }, [text, props.tickers]);

    function onSave() {
      setSaving(true);
      setErr(null);
      saveWatchlist(parseTickerText(text))
        .then(function (result) {
          setSaving(false);
          props.onSaved(result.tickers || []);
        })
        .catch(function (e) {
          setSaving(false);
          setErr(parseApiErrorMessage(e));
        });
    }

    return h(Card, { className: "hermes-tradingagents-watchlist" },
      h(CardHeader, null,
        h(CardTitle, null, "Watchlist"),
      ),
      h(CardContent, { className: "flex flex-col gap-2" },
        h("p", { className: "text-xs text-muted-foreground" },
          "One ticker per line (or comma-separated). Used by tradingagents_analyze " +
          "when it's called with no explicit tickers — e.g. a daily hermes cron job."
        ),
        h("textarea", {
          className: "hermes-tradingagents-textarea",
          rows: 8,
          value: text,
          spellCheck: false,
          placeholder: "AAPL\nNVDA\nBTC-USD",
          onChange: function (e) { setText(e.target.value); },
        }),
        err ? h("div", { className: "text-xs text-destructive" }, err) : null,
        h("div", { className: "flex items-center gap-2" },
          h(Button, {
            size: "sm",
            disabled: !dirty || saving,
            onClick: onSave,
          }, saving ? "Saving…" : "Save watchlist"),
          dirty ? h("span", { className: "text-xs text-muted-foreground" }, "Unsaved changes") : null,
        ),
      ),
    );
  }

  function ReportLink(props) {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState(null);

    function open() {
      setBusy(true);
      setErr(null);
      fetchReport(props.ticker, props.date)
        .then(function (result) {
          setBusy(false);
          const blob = new Blob([result.content || ""], { type: "text/plain;charset=utf-8" });
          const url = URL.createObjectURL(blob);
          window.open(url, "_blank", "noopener,noreferrer");
          setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
        })
        .catch(function (e) {
          setBusy(false);
          setErr(parseApiErrorMessage(e));
        });
    }

    if (!props.hasReport) {
      return h("span", { className: "text-xs text-muted-foreground" }, "—");
    }
    return h("div", { className: "flex flex-col gap-0.5" },
      h(Button, {
        size: "sm",
        variant: "outline",
        disabled: busy,
        onClick: open,
      }, busy ? "Opening…" : "Open report"),
      err ? h("span", { className: "text-xs text-destructive" }, err) : null,
    );
  }

  function RunRowButton(props) {
    const jobState = props.jobState; // undefined | "queued" | "running"
    if (jobState) {
      return h(Badge, { variant: "secondary" }, jobState === "running" ? "Running…" : "Queued…");
    }
    return h(Button, {
      size: "sm",
      variant: "outline",
      onClick: function () { props.onRun(props.ticker); },
    }, "Run");
  }

  function HistoryTable(props) {
    if (!props.rows.length) {
      return h("div", { className: "text-sm text-muted-foreground p-4" },
        "No securities on the watchlist yet. Add tickers above and save, " +
        "then run tradingagents_analyze (directly, or via a daily hermes cron job)."
      );
    }
    return h("table", { className: "hermes-tradingagents-table" },
      h("thead", null,
        h("tr", null,
          h("th", null, "Ticker"),
          h("th", null, "Last run"),
          h("th", null, "Type"),
          h("th", null, "Decision"),
          h("th", null, "Report"),
          h("th", null, "Rerun"),
        ),
      ),
      h("tbody", null,
        props.rows.map(function (row) {
          return h("tr", { key: row.ticker },
            h("td", null, h("span", { className: "font-medium" }, row.ticker)),
            h("td", null,
              row.date
                ? h("span", { title: row.date }, row.created_at ? timeAgo(row.created_at * 1000) : row.date)
                : h("span", { className: "text-xs text-muted-foreground" }, "never analyzed"),
            ),
            h("td", null, row.asset_type
              ? h(Badge, { variant: "secondary" }, row.asset_type)
              : null),
            h("td", null,
              row.error
                ? h(Badge, { variant: "destructive", title: row.error }, "error")
                : row.decision
                  ? h(Badge, { variant: decisionBadgeVariant(row.decision) }, String(row.decision).slice(0, 60))
                  : h("span", { className: "text-xs text-muted-foreground" }, "—"),
            ),
            h("td", null, h(ReportLink, { ticker: row.ticker, date: row.date, hasReport: !!row.has_report })),
            h("td", null, h(RunRowButton, {
              ticker: row.ticker,
              jobState: props.activeTickers[row.ticker],
              onRun: props.onRun,
            })),
          );
        }),
      ),
    );
  }

  function ScreenResultRow(props) {
    const row = props.row;
    const [added, setAdded] = useState(false);
    const [busy, setBusy] = useState(false);

    function onAdd() {
      setBusy(true);
      postWatchlistAdd([row.ticker])
        .then(function () {
          setBusy(false);
          setAdded(true);
          props.onAdded();
        })
        .catch(function () { setBusy(false); });
    }

    const metrics = row.screen_metrics || {};
    const metricsText = Object.keys(metrics)
      .filter(function (k) { return metrics[k] !== null && metrics[k] !== undefined; })
      .map(function (k) { return `${k}: ${metrics[k]}`; })
      .join(", ");

    return h("tr", { key: row.ticker },
      h("td", null, h("span", { className: "font-medium" }, row.ticker)),
      h("td", null, row.asset_type ? h(Badge, { variant: "secondary" }, row.asset_type) : null),
      h("td", null,
        row.error
          ? h(Badge, { variant: "destructive", title: row.error }, "error")
          : row.decision
            ? h(Badge, { variant: decisionBadgeVariant(row.decision) }, String(row.decision).slice(0, 60))
            : h("span", { className: "text-xs text-muted-foreground" }, "—"),
      ),
      h("td", null, h("span", { className: "text-xs text-muted-foreground", title: metricsText }, row.screen_source || "—")),
      h("td", null,
        added
          ? h("span", { className: "text-xs text-muted-foreground" }, "Added")
          : h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: onAdd }, busy ? "Adding…" : "+ Watchlist"),
      ),
    );
  }

  function ScreenResultsTable(props) {
    if (!props.rows.length) {
      return h("div", { className: "text-sm text-muted-foreground p-4" },
        "No screen results yet. Pick a risk level, asset class(es), and horizon above, then run a screen."
      );
    }
    return h("table", { className: "hermes-tradingagents-table" },
      h("thead", null,
        h("tr", null,
          h("th", null, "Ticker"),
          h("th", null, "Type"),
          h("th", null, "Direction"),
          h("th", null, "Screen source"),
          h("th", null, "Add"),
        ),
      ),
      h("tbody", null, props.rows.map(function (row) {
        return h(ScreenResultRow, { key: row.ticker, row: row, onAdded: props.onAdded });
      })),
    );
  }

  const PRICE_RANGES = [
    { value: "all", label: "All prices" },
    { value: "pennies", label: "Pennies (< $5)" },
    { value: "5_50", label: "$5 – $50" },
    { value: "51_100", label: "$51 – $100" },
    { value: "101_300", label: "$101 – $300" },
    { value: "301_plus", label: "$301+" },
  ];

  const CRON_FREQUENCIES = [
    { value: "daily", label: "Daily" },
    { value: "weekly", label: "Weekly" },
    { value: "monthly", label: "Monthly" },
  ];

  function ScreenerPanel(props) {
    const storedFilters = useMemo(loadStoredScreenerFilters, []);
    const [assetClasses, setAssetClasses] = useState((storedFilters && storedFilters.assetClasses) || ["stock"]);
    const [risk, setRisk] = useState((storedFilters && storedFilters.risk) || "medium");
    const [horizon, setHorizon] = useState((storedFilters && storedFilters.horizon) || "position");
    const [limit, setLimit] = useState((storedFilters && storedFilters.limit) || 10);
    const [priceRange, setPriceRange] = useState((storedFilters && storedFilters.priceRange) || "all");
    const [job, setJob] = useState(null); // { id, status }
    const [results, setResults] = useState([]);
    const [err, setErr] = useState(null);

    // Persist filter selections so they survive a page refresh — read once
    // above (lazy initial state), written back here on every change.
    useEffect(function () {
      saveStoredScreenerFilters({ assetClasses: assetClasses, risk: risk, horizon: horizon, limit: limit, priceRange: priceRange });
    }, [assetClasses, risk, horizon, limit, priceRange]);

    const [cronJob, setCronJob] = useState(null); // null while loading; {} shape once known: {exists, schedule, ...}
    const [cronFrequency, setCronFrequency] = useState("daily");
    const [cronBusy, setCronBusy] = useState(false);
    const [cronErr, setCronErr] = useState(null);

    const loadCronStatus = useCallback(function () {
      fetchScreenCron()
        .then(function (result) { setCronJob(result); })
        .catch(function () { setCronJob({ exists: false }); });
    }, []);

    useEffect(function () { loadCronStatus(); }, [loadCronStatus]);

    function onCreateCron() {
      setCronBusy(true);
      setCronErr(null);
      postScreenCron({
        frequency: cronFrequency, asset_classes: assetClasses, risk: risk,
        horizon: horizon, limit: Number(limit) || 10, price_range: priceRange,
      })
        .then(function (result) {
          setCronBusy(false);
          setCronJob(result);
        })
        .catch(function (e) {
          setCronBusy(false);
          setCronErr(parseApiErrorMessage(e));
        });
    }

    function onDeleteCron() {
      setCronBusy(true);
      setCronErr(null);
      deleteScreenCron()
        .then(function () {
          setCronBusy(false);
          setCronJob({ exists: false });
        })
        .catch(function (e) {
          setCronBusy(false);
          setCronErr(parseApiErrorMessage(e));
        });
    }

    function toggleAssetClass(cls) {
      setAssetClasses(function (prev) {
        if (prev.indexOf(cls) !== -1) {
          return prev.filter(function (c) { return c !== cls; });
        }
        return prev.concat([cls]);
      });
    }

    function onRunScreen() {
      setErr(null);
      setResults([]);
      postScreen({
        asset_classes: assetClasses, risk: risk, horizon: horizon,
        limit: Number(limit) || 10, price_range: priceRange,
      })
        .then(function (result) {
          setJob({ id: result.job.id, status: result.job.status, total: 0 });
        })
        .catch(function (e) { setErr(parseApiErrorMessage(e)); });
    }

    // Sync with the server's screen-job registry once on mount, same reason
    // as TradingAgentsPanel's analogous effect: a page refresh mid-screen
    // would otherwise show "Run screen" as clickable again even though the
    // worker is still busy with a screen from before the refresh.
    useEffect(function () {
      fetchScreenStatus()
        .then(function (result) {
          const active = (result.jobs || []).find(function (j) {
            return j.status === "queued" || j.status === "running";
          });
          if (active) {
            setJob({ id: active.id, status: active.status, total: (active.tickers || []).length });
            if (active.result && active.result.results) {
              setResults(active.result.results);
            }
          }
        })
        .catch(function () { /* best-effort */ });
    }, []);

    // Stage B deep-dives tickers one at a time server-side, updating
    // job.result.results after each one finishes — so every poll (not just
    // the one that sees "done") can pick up newly-finished rows and fill
    // the table in progressively instead of it staying empty until the
    // whole shortlist is done.
    useEffect(function () {
      if (!job || job.status === "done" || job.status === "error") {
        return undefined;
      }
      let cancelled = false;
      const interval = setInterval(function () {
        fetchScreenStatus()
          .then(function (result) {
            if (cancelled) return;
            const found = (result.jobs || []).find(function (j) { return j.id === job.id; });
            if (!found) return;
            const partialResults = (found.result && found.result.results) || null;
            if (partialResults) {
              setResults(partialResults);
            }
            if (found.status === "done") {
              setJob({ id: found.id, status: "done" });
            } else if (found.status === "error") {
              setJob({ id: found.id, status: "error" });
              setErr(found.error);
            } else {
              setJob({ id: found.id, status: found.status, total: (found.tickers || []).length });
            }
          })
          .catch(function () { /* transient poll failure — try again next tick */ });
      }, RUN_POLL_INTERVAL_MS);
      return function () { cancelled = true; clearInterval(interval); };
    }, [job]);

    const running = !!job && (job.status === "queued" || job.status === "running");

    return h(Card, null,
      h(CardHeader, null, h(CardTitle, null, "Screener — find new candidates")),
      h(CardContent, { className: "flex flex-col gap-3" },
        h("p", { className: "text-xs text-muted-foreground" },
          "Cheap quantitative discovery (Yahoo Finance screener for stocks, CoinGecko for " +
          "crypto, a static futures list for commodities), then a TradingAgents deep-dive " +
          "on the shortlist for sentiment and direction."
        ),
        h("div", { className: "flex flex-wrap items-end gap-4" },
          h("div", { className: "flex flex-col gap-1" },
            h(Label, null, "Asset classes"),
            h("div", { className: "flex gap-3" },
              ASSET_CLASSES.map(function (cls) {
                return h("label", { key: cls, className: "flex items-center gap-1 text-sm" },
                  h("input", {
                    type: "checkbox",
                    checked: assetClasses.indexOf(cls) !== -1,
                    onChange: function () { toggleAssetClass(cls); },
                  }),
                  cls,
                );
              }),
            ),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, null, "Risk"),
            h("select", { value: risk, onChange: function (e) { setRisk(e.target.value); } },
              h("option", { value: "low" }, "Low"),
              h("option", { value: "medium" }, "Medium"),
              h("option", { value: "high" }, "High"),
            ),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, null, "Horizon"),
            h("select", { value: horizon, onChange: function (e) { setHorizon(e.target.value); } },
              h("option", { value: "swing" }, "Swing (a few days)"),
              h("option", { value: "position" }, "Hold (6 months)"),
            ),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, null, "Price"),
            h("select", { value: priceRange, onChange: function (e) { setPriceRange(e.target.value); } },
              PRICE_RANGES.map(function (p) {
                return h("option", { key: p.value, value: p.value }, p.label);
              }),
            ),
          ),
          h("div", { className: "flex flex-col gap-1" },
            h(Label, null, "Limit / class"),
            h("input", {
              type: "number", min: 1, max: 50, value: limit,
              onChange: function (e) { setLimit(e.target.value); },
              style: { width: "5rem" },
            }),
          ),
          h(Button, {
            size: "sm",
            disabled: running || !assetClasses.length,
            onClick: onRunScreen,
          }, running
              ? (job.status === "queued"
                  ? "Queued…"
                  : `Running… (${results.length}/${job.total || "?"} analyzed)`)
              : "Run screen"),
          h("div", { className: "flex items-center gap-2" },
            h("select", {
              value: cronFrequency,
              disabled: cronBusy || (cronJob && cronJob.exists),
              onChange: function (e) { setCronFrequency(e.target.value); },
            },
              CRON_FREQUENCIES.map(function (f) {
                return h("option", { key: f.value, value: f.value }, f.label);
              }),
            ),
            h(Button, {
              size: "sm",
              variant: "outline",
              disabled: cronBusy || !cronJob || cronJob.exists,
              onClick: onCreateCron,
            }, cronJob && cronJob.exists ? `Scheduled (${cronJob.schedule || cronFrequency})` : "Schedule screen"),
            (cronJob && cronJob.exists)
              ? h(Button, {
                  size: "sm",
                  variant: "outline",
                  disabled: cronBusy,
                  onClick: onDeleteCron,
                }, "Remove schedule")
              : null,
          ),
        ),
        cronErr ? h("div", { className: "text-sm text-destructive" }, cronErr) : null,
        err ? h("div", { className: "text-sm text-destructive" }, err) : null,
        h(ScreenResultsTable, { rows: results, onAdded: props.onWatchlistChanged }),
      ),
    );
  }

  function TradingAgentsPanel() {
    const [status, setStatus] = useState(null);
    const [tickers, setTickers] = useState([]);
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadErr, setLoadErr] = useState(null);

    // jobs: jobId -> { tickers: string[], isAll: bool, status: "queued"|"running" }
    // Only tracks jobs this browser tab triggered and hasn't seen finish yet —
    // the source of truth for "is anything running" is the poll below, this
    // is just which job ids we're waiting on.
    const [jobs, setJobs] = useState({});
    // screenJobs: jobId -> { tickers: string[], status } — a running screen
    // job's stage B deep-dives tickers too, via run_batch, but *outside* the
    // /run endpoint (the worker calls it directly), so those tickers never
    // show up in `jobs` above. Tracked separately here so the watchlist's
    // per-ticker Run buttons (and Run all) gray out for them as well.
    const [screenJobs, setScreenJobs] = useState({});
    const [runErr, setRunErr] = useState(null);

    const [watchlistCronJob, setWatchlistCronJob] = useState(null); // null while loading; {} shape once known: {exists, schedule, ...}
    const [watchlistCronFrequency, setWatchlistCronFrequency] = useState("daily");
    const [watchlistCronBusy, setWatchlistCronBusy] = useState(false);
    const [watchlistCronErr, setWatchlistCronErr] = useState(null);

    const loadWatchlistCronStatus = useCallback(function () {
      fetchWatchlistCron()
        .then(function (result) { setWatchlistCronJob(result); })
        .catch(function () { setWatchlistCronJob({ exists: false }); });
    }, []);

    useEffect(function () { loadWatchlistCronStatus(); }, [loadWatchlistCronStatus]);

    function onCreateWatchlistCron() {
      setWatchlistCronBusy(true);
      setWatchlistCronErr(null);
      postWatchlistCron({ frequency: watchlistCronFrequency })
        .then(function (result) {
          setWatchlistCronBusy(false);
          setWatchlistCronJob(result);
        })
        .catch(function (e) {
          setWatchlistCronBusy(false);
          setWatchlistCronErr(parseApiErrorMessage(e));
        });
    }

    function onDeleteWatchlistCron() {
      setWatchlistCronBusy(true);
      setWatchlistCronErr(null);
      deleteWatchlistCron()
        .then(function () {
          setWatchlistCronBusy(false);
          setWatchlistCronJob({ exists: false });
        })
        .catch(function (e) {
          setWatchlistCronBusy(false);
          setWatchlistCronErr(parseApiErrorMessage(e));
        });
    }

    const load = useCallback(function () {
      setLoading(true);
      setLoadErr(null);
      Promise.all([fetchStatus(), fetchWatchlist(), fetchHistory()])
        .then(function (results) {
          setStatus(results[0] || null);
          setTickers((results[1] && results[1].tickers) || []);
          setRows((results[2] && results[2].rows) || []);
          setLoading(false);
        })
        .catch(function (e) {
          setLoading(false);
          setLoadErr(parseApiErrorMessage(e));
        });
    }, []);

    useEffect(function () { load(); }, [load]);

    // Sync with the server's job registry once on mount — it's the source of
    // truth for "is anything running" and survives a page refresh (it's kept
    // in the dashboard backend process, not the browser), but this tab only
    // learns about jobs it triggered itself unless it asks. Without this, a
    // refresh mid-run shows every Run button as clickable again even though
    // the worker is still busy with a job from before the refresh.
    useEffect(function () {
      fetchRunStatus()
        .then(function (result) {
          const active = {};
          (result.jobs || []).forEach(function (j) {
            if (j.status === "queued" || j.status === "running") {
              active[j.id] = { tickers: j.tickers || [], isAll: !!j.is_all, status: j.status };
            }
          });
          if (Object.keys(active).length) {
            setJobs(function (prev) { return Object.assign({}, active, prev); });
          }
        })
        .catch(function () { /* best-effort — polling below will retry once triggered locally */ });
    }, []);

    // Poll /run/status while we're waiting on any job, and refresh
    // watchlist/history the moment one finishes so the table picks up the
    // new decision + report link without the user hitting Refresh.
    useEffect(function () {
      const activeIds = Object.keys(jobs);
      if (!activeIds.length) {
        return undefined;
      }
      let cancelled = false;
      const interval = setInterval(function () {
        fetchRunStatus()
          .then(function (result) {
            if (cancelled) return;
            const byId = {};
            (result.jobs || []).forEach(function (j) { byId[j.id] = j; });
            let anyFinished = false;
            setJobs(function (prev) {
              const next = {};
              Object.keys(prev).forEach(function (id) {
                const server = byId[id];
                if (!server || server.status === "done" || server.status === "error") {
                  anyFinished = true;
                  if (server && server.status === "error") {
                    setRunErr(`${(server.tickers || []).join(", ")}: ${server.error}`);
                  }
                  return; // drop from tracked set
                }
                next[id] = Object.assign({}, prev[id], { status: server.status });
              });
              return next;
            });
            if (anyFinished) load();
          })
          .catch(function () { /* transient poll failure — try again next tick */ });
      }, RUN_POLL_INTERVAL_MS);
      return function () {
        cancelled = true;
        clearInterval(interval);
      };
    }, [jobs, load]);

    // Sync + poll screen jobs the same way as analyze jobs above, so the
    // watchlist table's per-ticker Run buttons (and Run all) also gray out
    // while a screen's stage B is deep-diving them. Unlike analyze jobs,
    // a screen job's ticker list starts empty and fills in mid-run (stage A
    // discovery happens inside the worker), so every poll re-reads tickers,
    // not just status.
    useEffect(function () {
      fetchScreenStatus()
        .then(function (result) {
          const active = {};
          (result.jobs || []).forEach(function (j) {
            if (j.status === "queued" || j.status === "running") {
              active[j.id] = { tickers: j.tickers || [], status: j.status };
            }
          });
          if (Object.keys(active).length) {
            setScreenJobs(function (prev) { return Object.assign({}, active, prev); });
          }
        })
        .catch(function () { /* best-effort */ });
    }, []);

    useEffect(function () {
      if (!Object.keys(screenJobs).length) {
        return undefined;
      }
      let cancelled = false;
      const interval = setInterval(function () {
        fetchScreenStatus()
          .then(function (result) {
            if (cancelled) return;
            const byId = {};
            (result.jobs || []).forEach(function (j) { byId[j.id] = j; });
            let anyFinished = false;
            setScreenJobs(function (prev) {
              const next = {};
              Object.keys(prev).forEach(function (id) {
                const server = byId[id];
                if (!server || server.status === "done" || server.status === "error") {
                  anyFinished = true;
                  return;
                }
                next[id] = { tickers: server.tickers || [], status: server.status };
              });
              return next;
            });
            if (anyFinished) load();
          })
          .catch(function () { /* transient poll failure — try again next tick */ });
      }, RUN_POLL_INTERVAL_MS);
      return function () {
        cancelled = true;
        clearInterval(interval);
      };
    }, [screenJobs, load]);

    const activeTickers = useMemo(function () {
      const map = {};
      Object.keys(jobs).forEach(function (id) {
        const job = jobs[id];
        job.tickers.forEach(function (t) { map[t] = job.status; });
      });
      Object.keys(screenJobs).forEach(function (id) {
        const job = screenJobs[id];
        job.tickers.forEach(function (t) { map[t] = job.status; });
      });
      return map;
    }, [jobs, screenJobs]);

    // Any job in flight — individual, "all", or a screen's deep-dive — disables
    // Run all: analyze and screen jobs share the same single worker, so
    // queuing another just piles up behind whatever's already running, and
    // graying the button makes that visible instead of inviting a confusing
    // extra click.
    const anyRunActive = useMemo(function () {
      return Object.keys(jobs).length > 0 || Object.keys(screenJobs).length > 0;
    }, [jobs, screenJobs]);

    const allRunActive = useMemo(function () {
      return Object.keys(jobs).some(function (id) { return jobs[id].isAll; });
    }, [jobs]);

    function triggerRun(body, isAll) {
      setRunErr(null);
      postRun(body)
        .then(function (result) {
          const job = result.job;
          setJobs(function (prev) {
            return Object.assign({}, prev, {
              [job.id]: { tickers: job.tickers, isAll: isAll, status: job.status },
            });
          });
        })
        .catch(function (e) { setRunErr(parseApiErrorMessage(e)); });
    }

    const onRunOne = useCallback(function (ticker) {
      triggerRun({ tickers: [ticker] }, false);
    }, []);

    const onRunAll = useCallback(function () {
      triggerRun({ all: true }, true);
    }, []);

    return h("div", { className: "hermes-tradingagents-panel flex flex-col gap-4 p-4" },
      h("div", { className: "flex items-center justify-between" },
        h("h2", { className: "text-lg font-semibold" }, "TradingAgents"),
        h("div", { className: "flex items-center gap-2" },
          h(Button, {
            size: "sm",
            onClick: onRunAll,
            disabled: anyRunActive || !tickers.length,
          }, allRunActive ? "Running all…" : anyRunActive ? "Analysis running…" : "Run all (queued)"),
          h("select", {
            value: watchlistCronFrequency,
            disabled: watchlistCronBusy || (watchlistCronJob && watchlistCronJob.exists),
            onChange: function (e) { setWatchlistCronFrequency(e.target.value); },
          },
            CRON_FREQUENCIES.map(function (f) {
              return h("option", { key: f.value, value: f.value }, f.label);
            }),
          ),
          h(Button, {
            size: "sm",
            variant: "outline",
            disabled: watchlistCronBusy || !watchlistCronJob || watchlistCronJob.exists,
            onClick: onCreateWatchlistCron,
          }, watchlistCronJob && watchlistCronJob.exists
              ? `Scheduled (${watchlistCronJob.schedule || watchlistCronFrequency})`
              : "Schedule watchlist"),
          (watchlistCronJob && watchlistCronJob.exists)
            ? h(Button, {
                size: "sm",
                variant: "outline",
                disabled: watchlistCronBusy,
                onClick: onDeleteWatchlistCron,
              }, "Remove schedule")
            : null,
          h(Button, { size: "sm", variant: "outline", onClick: load, disabled: loading },
            loading ? "Loading…" : "Refresh"),
        ),
      ),
      loadErr ? h("div", { className: "text-sm text-destructive" }, loadErr) : null,
      watchlistCronErr ? h("div", { className: "text-sm text-destructive" }, watchlistCronErr) : null,
      runErr
        ? h("pre", {
            className: "text-sm text-destructive hermes-tradingagents-run-error",
          }, runErr)
        : null,
      h(StatusCard, { status: status, onRefresh: load }),
      h(ScreenerPanel, { onWatchlistChanged: load }),
      h(WatchlistEditor, {
        tickers: tickers,
        onSaved: function (saved) { setTickers(saved); load(); },
      }),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Last analysis by security")),
        h(CardContent, { className: "p-0" },
          h(HistoryTable, { rows: rows, activeTickers: activeTickers, onRun: onRunOne }),
        ),
      ),
    );
  }

  window.__HERMES_PLUGINS__.register("tradingagents", TradingAgentsPanel);
})();
