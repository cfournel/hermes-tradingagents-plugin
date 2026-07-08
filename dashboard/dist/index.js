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

  const RUN_POLL_INTERVAL_MS = 3000;

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
    const [runErr, setRunErr] = useState(null);

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

    const activeTickers = useMemo(function () {
      const map = {};
      Object.keys(jobs).forEach(function (id) {
        const job = jobs[id];
        job.tickers.forEach(function (t) { map[t] = job.status; });
      });
      return map;
    }, [jobs]);

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
            disabled: allRunActive || !tickers.length,
          }, allRunActive ? "Running all…" : "Run all (queued)"),
          h(Button, { size: "sm", variant: "outline", onClick: load, disabled: loading },
            loading ? "Loading…" : "Refresh"),
        ),
      ),
      loadErr ? h("div", { className: "text-sm text-destructive" }, loadErr) : null,
      runErr
        ? h("pre", {
            className: "text-sm text-destructive hermes-tradingagents-run-error",
          }, runErr)
        : null,
      h(StatusCard, { status: status, onRefresh: load }),
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
