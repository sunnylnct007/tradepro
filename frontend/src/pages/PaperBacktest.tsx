import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useEventStream } from "../hooks/useEventStream";

// Paper-trading backtest dashboard. List of reports the Mac has
// pushed (single-strategy walk-forward OR multi-strategy comparator).
// Click a report to see the per-entry scoreboard + per-session equity
// curve. The report payload is pre-shaped on the Mac side — see
// validator.to_summary() / comparator.to_summary() — so this page
// is a thin renderer with no business logic.

type ReportSummary = {
  reportId: string;
  kind: string;
  symbol: string;
  start?: string;
  end?: string;
  entryCount: number;
  receivedAtUtc: string;
};

type ComparatorEntry = {
  strategy_id: string;
  label: string;
  symbol: string;
  session_count: number;
  total_realised_pnl: number;
  total_fills: number;
  total_commission: number;
  avg_session_pnl: number;
  stdev_session_pnl: number;
  win_session_pct: number;
  sharpe_per_session: number;
  max_drawdown: number;
  best_session?: { date: string; realised_pnl: number };
  worst_session?: { date: string; realised_pnl: number };
  equity_curve: Array<[string, number]>;
};

type ComparatorPayload = {
  symbol: string;
  start: string;
  end: string;
  entries: ComparatorEntry[];
  rankings: {
    by_total_pnl: string[];
    by_sharpe: string[];
    by_drawdown: string[];
  };
};

type BacktestPayload = ComparatorEntry & {
  kind: string;
  report_id: string;
  // Single-backtest payload is one strategy's WalkForwardResult.to_summary();
  // we normalise it into a one-entry comparator shape so the renderer
  // doesn't fork on payload type.
};

function isComparatorPayload(p: unknown): p is ComparatorPayload {
  return !!p && typeof p === "object" && Array.isArray((p as ComparatorPayload).entries);
}

function normalisePayload(p: unknown): ComparatorPayload {
  if (isComparatorPayload(p)) return p;
  const single = p as BacktestPayload;
  return {
    symbol: single.symbol,
    start: single.equity_curve?.[0]?.[0] ?? "",
    end: single.equity_curve?.[single.equity_curve.length - 1]?.[0] ?? "",
    entries: [{ ...single, label: single.strategy_id }],
    rankings: {
      by_total_pnl: [single.strategy_id],
      by_sharpe: [single.strategy_id],
      by_drawdown: [single.strategy_id],
    },
  };
}

type StrategySpec = {
  name: string;
  class: string;
  summary: string;
  default_params: Record<string, unknown>;
};

type SnapshotSummary = {
  sessionLabel: string;
  broker: string;
  asOfUtc: string;
  strategyCount: number;
  totalFills: number;
  receivedAtUtc: string;
};

type SnapshotPayload = {
  as_of_utc: string;
  session_label?: string;
  broker?: string;
  strategies: Array<{
    strategy_id: string;
    realised_pnl: number;
    unrealised_pnl: number;
    equity: number;
    commission_paid: number;
    fills_count: number;
    positions: Array<{
      symbol: string;
      quantity: number;
      avg_entry_price: number;
      last_mark: number;
      unrealised_pnl: number;
    }>;
    recent_fills: Array<{
      order_id: string;
      symbol: string;
      side: string;
      quantity: number;
      fill_price: number;
      fill_time: string;
      commission: number;
    }>;
  }>;
};

type Tab = "backtests" | "live" | "pending";

type PendingOrder = {
  orderId: string;
  broker: string;
  brokerMode: string;
  strategyId: string;
  symbol: string;
  t212Ticker: string;
  side: string;
  quantity: number;
  orderType: string;
  tag?: string | null;
  suggestedAtUtc: string;
  barAtEmitClose?: number | null;
  barAtEmitTime?: string | null;
  state: string;
  receivedAtUtc: string;
  decidedAtUtc?: string | null;
  brokerOrderId?: number | null;
  brokerStatus?: string | null;
  rejectionReason?: string | null;
  error?: string | null;
  responseBody?: string | null;
};

export function PaperBacktest() {
  const [tab, setTab] = useState<Tab>("backtests");
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [payload, setPayload] = useState<ComparatorPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [strategies, setStrategies] = useState<StrategySpec[] | null>(null);
  const [strategiesError, setStrategiesError] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[] | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<string | null>(null);
  const [snapshotPayload, setSnapshotPayload] = useState<SnapshotPayload | null>(null);
  const [loadingSnap, setLoadingSnap] = useState(false);
  const [pendingOrders, setPendingOrders] = useState<PendingOrder[] | null>(null);
  const [pendingBusy, setPendingBusy] = useState<string | null>(null);

  // Reusable refresher for the pending-orders list — used on mount,
  // after every Approve/Reject, on tab switch, AND on every relevant
  // SSE event (Phase 7 of the unicorn arc). The SSE hook below pulses
  // when an order_emitted / order_risk_* / order_place_failed event
  // arrives; refreshPending is invoked from that pulse via useEffect.
  const refreshPending = () => {
    api
      .paperPendingOrders()
      .then(setPendingOrders)
      .catch(() => setPendingOrders([]));
  };

  // Live event stream — refreshes the pending-orders list the moment
  // any order-shaped event lands. `pulse` increments per event; the
  // useEffect below depends on it so the refetch happens automatically.
  // We don't filter by `type` because we care about three distinct
  // event types (order_emitted, order_risk_approved/rejected,
  // order_place_failed) and per-type subscriptions would mean three
  // open streams.
  const eventStream = useEventStream({});

  useEffect(() => {
    if (eventStream.pulse === 0) return; // ignore initial state
    refreshPending();
  }, [eventStream.pulse]);

  useEffect(() => {
    api
      .paperBacktestReports()
      .then(setReports)
      .catch((e) => setError(String(e)));
    api
      .paperStrategies()
      .then((c) => setStrategies(c.strategies))
      .catch((e) => setStrategiesError(String(e)));
    api
      .paperSnapshots()
      .then((s) => setSnapshots(s))
      .catch(() => setSnapshots([]));
    refreshPending();
  }, []);

  useEffect(() => {
    if (!selectedSnapshot) {
      setSnapshotPayload(null);
      return;
    }
    setLoadingSnap(true);
    api
      .paperSnapshot(selectedSnapshot)
      .then((p) => setSnapshotPayload(p as SnapshotPayload))
      .catch((e) => setError(String(e)))
      .finally(() => setLoadingSnap(false));
  }, [selectedSnapshot]);

  useEffect(() => {
    if (!selectedId) {
      setPayload(null);
      return;
    }
    setLoadingDetail(true);
    api
      .paperBacktestReport(selectedId)
      .then((p) => setPayload(normalisePayload(p)))
      .catch((e) => setError(String(e)))
      .finally(() => setLoadingDetail(false));
  }, [selectedId]);

  return (
    <div>
      <h2 style={{ margin: "0 0 4px" }}>Backtest</h2>
      <p style={{ color: "var(--text-dim)", fontSize: 13, marginTop: 0 }}>
        Paper-trading walk-forward + multi-strategy comparator results pushed
        from the Mac (<code>tradepro-paper-compare --push</code>,
        <code> tradepro-paper-backtest --push</code>).
      </p>
      {error && (
        <div
          style={{
            padding: "10px 14px",
            margin: "8px 0",
            border: "1px solid var(--down)",
            background: "var(--down-soft)",
            color: "var(--down)",
            borderRadius: 8,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}
      <StrategyCatalog strategies={strategies} error={strategiesError} />

      <div style={{ display: "flex", gap: 4, marginTop: 16, borderBottom: "1px solid var(--border)", alignItems: "center" }}>
        <TabBtn label="Backtest reports" active={tab === "backtests"} onClick={() => setTab("backtests")}
                count={reports?.length} />
        <TabBtn label="Live sessions" active={tab === "live"} onClick={() => setTab("live")}
                count={snapshots?.length} />
        <TabBtn
          label="Pending orders"
          active={tab === "pending"}
          onClick={() => { setTab("pending"); refreshPending(); }}
          count={pendingOrders?.filter((o) => o.state === "Pending").length}
          highlight={(pendingOrders?.filter((o) => o.state === "Pending").length ?? 0) > 0}
        />
        {/* Live SSE pip — connected = green dot, otherwise muted.
            Tooltip carries the last-seen event seq so an operator can
            tell whether the stream is making progress at a glance. */}
        <span
          title={eventStream.connected
            ? `Live event stream connected${eventStream.lastSeq ? ` · last seq ${eventStream.lastSeq}` : ""}`
            : "Event stream disconnected — falling back to polling"}
          style={{
            marginLeft: "auto",
            marginRight: 8,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: "var(--text-muted)",
            cursor: "help",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: eventStream.connected ? "var(--up)" : "var(--text-muted)",
              boxShadow: eventStream.connected ? "0 0 6px var(--up)" : "none",
            }}
          />
          live
        </span>
      </div>

      {tab === "backtests" && (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 1fr) 2fr", gap: 16, marginTop: 16 }}>
          <ReportList
            reports={reports}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <ReportDetail loading={loadingDetail} payload={payload} />
        </div>
      )}
      {tab === "live" && (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 1fr) 2fr", gap: 16, marginTop: 16 }}>
          <SnapshotList
            snapshots={snapshots}
            selectedLabel={selectedSnapshot}
            onSelect={setSelectedSnapshot}
          />
          <SnapshotDetail loading={loadingSnap} payload={snapshotPayload} />
        </div>
      )}
      {tab === "pending" && (
        <PendingOrdersPanel
          orders={pendingOrders}
          busy={pendingBusy}
          onApprove={async (id) => {
            setPendingBusy(id);
            try { await api.approvePendingOrder(id); }
            catch (e) { setError(String(e)); }
            finally { setPendingBusy(null); refreshPending(); }
          }}
          onReject={async (id, reason) => {
            setPendingBusy(id);
            try { await api.rejectPendingOrder(id, reason); }
            catch (e) { setError(String(e)); }
            finally { setPendingBusy(null); refreshPending(); }
          }}
        />
      )}
    </div>
  );
}

function TabBtn(props: {
  label: string; active: boolean; onClick: () => void;
  count?: number | null; highlight?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      style={{
        padding: "8px 14px",
        fontSize: 12,
        fontWeight: props.active ? 600 : 500,
        borderRadius: 0,
        cursor: "pointer",
        border: "none",
        borderBottom: `2px solid ${props.active ? "var(--up)" : "transparent"}`,
        background: "transparent",
        color: props.active ? "var(--text)" : "var(--text-dim)",
        marginBottom: -1,
        position: "relative",
      }}
    >
      {props.label}
      {typeof props.count === "number" && (
        <span
          style={{
            marginLeft: 6,
            fontSize: 10,
            padding: props.highlight ? "1px 6px" : "0",
            borderRadius: 999,
            background: props.highlight ? "var(--neutral)" : "transparent",
            color: props.highlight ? "var(--bg)" : "var(--text-muted)",
            fontWeight: props.highlight ? 700 : 400,
          }}
        >
          {props.count}
        </span>
      )}
    </button>
  );
}

function PendingOrdersPanel(props: {
  orders: PendingOrder[] | null;
  busy: string | null;
  onApprove: (id: string) => void | Promise<void>;
  onReject: (id: string, reason?: string) => void | Promise<void>;
}) {
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkErr, setBulkErr] = useState<string | null>(null);

  if (props.orders === null) return <div style={{ color: "var(--text-muted)" }}>Loading…</div>;
  const pending = props.orders.filter((o) => o.state === "Pending");
  const history = props.orders.filter((o) => o.state !== "Pending");

  // Detect the legacy "EURUSD_US_EQ" rows the strategy generated before
  // commit 015204a fixed the T212 ticker mapping. These can never
  // approve cleanly (T212 doesn't have *_US_EQ FX instruments) so we
  // offer a one-click way to mass-reject them.
  const brokenFxCount = pending.filter((o) =>
    /^(EUR|GBP|USD|AUD|NZD)[A-Z]{3}_US_EQ$/.test(o.t212Ticker ?? ""),
  ).length;

  const bulkReject = async (tickerLike: string | undefined, label: string) => {
    if (!confirm(`Reject ${label}? They'll move to History as 'bulk_reject'.`)) return;
    setBulkBusy(true);
    setBulkErr(null);
    try {
      await api.bulkRejectPending(tickerLike, "bulk_reject_stale");
      // Caller refreshes via the event stream; small grace period.
      await new Promise((r) => setTimeout(r, 500));
      window.location.reload();
    } catch (e) {
      setBulkErr(String(e));
      setBulkBusy(false);
    }
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 16 }}>
      <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.55, maxWidth: 820 }}>
        Manual-mode orders the Mac engine pushed up for human review. Click
        Approve to place against T212 from the API box (using the same T212
        creds the read-only portfolio integration uses). Reject just marks
        the order dead — no broker call.
        <br />
        Trigger from the Mac: <code>tradepro-paper --broker t212 --placement-mode manual --symbol AAPL --date 2026-05-15</code>
      </div>

      {(pending.length > 0 || bulkErr) && (
        <section style={{
          display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center",
          padding: "8px 12px",
          background: "rgba(217,119,6,0.06)",
          border: "1px solid rgba(217,119,6,0.25)",
          borderRadius: 6,
        }}>
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            Stale-rows cleanup:
          </div>
          {brokenFxCount > 0 && (
            <button
              onClick={() => bulkReject("%_US_EQ", `${brokenFxCount} legacy FX rows with broken _US_EQ ticker`)}
              disabled={bulkBusy}
              style={{
                padding: "4px 10px", fontSize: 11,
                border: "1px solid #d97706", borderRadius: 4,
                background: "transparent", color: "#d97706",
                cursor: bulkBusy ? "wait" : "pointer",
              }}
              title="Mass-reject the EURUSD_US_EQ/etc rows generated before the ticker fix"
            >
              Reject {brokenFxCount} broken FX rows
            </button>
          )}
          <button
            onClick={() => bulkReject(undefined, `ALL ${pending.length} Pending rows`)}
            disabled={bulkBusy || pending.length === 0}
            style={{
              padding: "4px 10px", fontSize: 11,
              border: "1px solid var(--border)", borderRadius: 4,
              background: "transparent", color: "var(--text-muted)",
              cursor: bulkBusy ? "wait" : "pointer",
            }}
          >
            Reject all {pending.length}
          </button>
          {bulkErr && <span style={{ fontSize: 11, color: "var(--down)" }}>{bulkErr}</span>}
        </section>
      )}

      <section>
        <div className="stat-label">Awaiting review ({pending.length})</div>
        {pending.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12, padding: "8px 0" }}>
            Nothing pending right now.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
            {pending.map((o) => (
              <PendingOrderRow
                key={o.orderId}
                order={o}
                busy={props.busy === o.orderId}
                onApprove={() => props.onApprove(o.orderId)}
                onReject={() => props.onReject(o.orderId)}
              />
            ))}
          </div>
        )}
      </section>

      {history.length > 0 && (
        <section>
          <div className="stat-label">History ({history.length})</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
            {history.map((o) => (
              <PendingOrderRow
                key={o.orderId}
                order={o}
                busy={false}
                onApprove={() => {}}
                onReject={() => {}}
                terminal
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function PendingOrderRow(props: {
  order: PendingOrder;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  terminal?: boolean;
}) {
  const o = props.order;
  const stateColour =
    o.state === "Pending" ? "var(--neutral)" :
    o.state === "Placed" ? "var(--up)" :
    o.state === "Rejected" ? "var(--text-muted)" :
    "var(--down)";
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderLeft: `4px solid ${stateColour}`,
        borderRadius: 8,
        padding: "10px 12px",
        background: "var(--bg-elev)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <div>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: o.side === "BUY" ? "var(--up)" : "var(--down)",
              marginRight: 8,
            }}
          >
            {o.side}
          </span>
          <strong style={{ fontSize: 13 }}>{o.symbol}</strong>
          <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-dim)" }}>
            qty {o.quantity} · {o.orderType}
          </span>
          <span style={{ marginLeft: 8, fontSize: 10, color: "var(--text-muted)" }}>
            {o.broker} ({o.brokerMode})
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color: stateColour, fontWeight: 600 }}>
            {o.state.toUpperCase()}
          </span>
          {!props.terminal && (
            <>
              <button
                onClick={props.onApprove}
                disabled={props.busy}
                className="primary"
                style={{ fontSize: 12, padding: "5px 12px" }}
              >
                {props.busy ? "Placing…" : "Approve"}
              </button>
              <button
                onClick={props.onReject}
                disabled={props.busy}
                style={{ fontSize: 12, padding: "5px 12px" }}
              >
                Reject
              </button>
            </>
          )}
        </div>
      </div>
      <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 6 }}>
        Strategy <code>{o.strategyId}</code>
        {o.barAtEmitClose != null && (
          <> · bar close <span className="num">{o.barAtEmitClose.toFixed(2)}</span></>
        )}
        {" · "}suggested {new Date(o.suggestedAtUtc).toLocaleString()}
      </div>
      {o.tag && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, fontStyle: "italic" }}>
          {o.tag}
        </div>
      )}
      {o.brokerOrderId != null && (
        <div style={{ fontSize: 11, color: "var(--up)", marginTop: 4 }}>
          Placed: T212 order #{o.brokerOrderId} · status {o.brokerStatus ?? "?"}
        </div>
      )}
      {o.error && (
        <div style={{ fontSize: 11, color: "var(--down)", marginTop: 4 }}>
          {o.error}
        </div>
      )}
      {o.rejectionReason && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
          Rejected: {o.rejectionReason}
        </div>
      )}
    </div>
  );
}

function SnapshotList(props: {
  snapshots: SnapshotSummary[] | null;
  selectedLabel: string | null;
  onSelect: (label: string) => void;
}) {
  if (props.snapshots === null) return <div style={{ color: "var(--text-muted)" }}>Loading sessions…</div>;
  if (props.snapshots.length === 0) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        No live sessions pushed yet. Run a paper session with{" "}
        <code>--push</code> from the Mac:
        <pre
          style={{
            marginTop: 8,
            padding: 8,
            background: "var(--bg-elev)",
            borderRadius: 6,
            fontSize: 12,
            overflowX: "auto",
          }}
        >
{`uv run tradepro-paper --broker yfinance \\
  --symbol AAPL --date 2026-05-15 --push

# Or against T212 demo
uv run tradepro-paper --broker t212 \\
  --symbol AAPL --date 2026-05-15 \\
  --max-position-value-usd 1000 --push`}
        </pre>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {props.snapshots.map((s) => {
        const active = s.sessionLabel === props.selectedLabel;
        return (
          <button
            key={s.sessionLabel}
            onClick={() => props.onSelect(s.sessionLabel)}
            style={{
              textAlign: "left",
              padding: "10px 12px",
              border: `1px solid ${active ? "var(--up)" : "var(--border)"}`,
              background: active ? "var(--bg-hover)" : "transparent",
              borderRadius: 8,
              cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <strong style={{ fontSize: 13 }}>{s.sessionLabel}</strong>
              <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase" }}>{s.broker}</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
              {s.strategyCount} {s.strategyCount === 1 ? "strategy" : "strategies"} · {s.totalFills} fills
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
              {new Date(s.receivedAtUtc).toLocaleString()}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function SnapshotDetail(props: { loading: boolean; payload: SnapshotPayload | null }) {
  if (props.loading) return <div style={{ color: "var(--text-muted)" }}>Loading…</div>;
  if (!props.payload) return <div style={{ color: "var(--text-muted)" }}>Pick a session on the left.</div>;
  const p = props.payload;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <strong style={{ fontSize: 14 }}>{p.session_label ?? "(unlabelled session)"}</strong>
        <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>
          {p.broker}
        </span>
        <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-dim)" }}>
          as of {new Date(p.as_of_utc).toLocaleString()}
        </span>
      </div>

      {p.strategies.map((s) => (
        <div key={s.strategy_id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
            <strong style={{ fontSize: 13 }}>{s.strategy_id}</strong>
            <div style={{ display: "flex", gap: 14, fontSize: 11 }}>
              <KV label="Realised" value={s.realised_pnl} colour={s.realised_pnl >= 0 ? "var(--up)" : "var(--down)"} />
              <KV label="Unrealised" value={s.unrealised_pnl} colour={s.unrealised_pnl >= 0 ? "var(--up)" : "var(--down)"} />
              <KV label="Equity" value={s.equity} colour="var(--text)" />
              <KV label="Commission" value={-s.commission_paid} colour="var(--text-muted)" />
              <KV label="Fills" value={s.fills_count} colour="var(--text-dim)" raw />
            </div>
          </div>

          {s.positions.length > 0 ? (
            <div style={{ marginTop: 10 }}>
              <div className="stat-label" style={{ marginBottom: 4 }}>Open positions</div>
              <table className="num" style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid var(--border-soft)" }}>
                    <th style={{ textAlign: "left", padding: "4px 6px" }}>Symbol</th>
                    <th style={{ textAlign: "right", padding: "4px 6px" }}>Qty</th>
                    <th style={{ textAlign: "right", padding: "4px 6px" }}>Avg entry</th>
                    <th style={{ textAlign: "right", padding: "4px 6px" }}>Last mark</th>
                    <th style={{ textAlign: "right", padding: "4px 6px" }}>Unrealised</th>
                  </tr>
                </thead>
                <tbody>
                  {s.positions.map((pos) => (
                    <tr key={pos.symbol}>
                      <td style={{ padding: "4px 6px" }}>{pos.symbol}</td>
                      <td style={{ textAlign: "right", padding: "4px 6px" }}>{pos.quantity}</td>
                      <td style={{ textAlign: "right", padding: "4px 6px" }}>{pos.avg_entry_price.toFixed(2)}</td>
                      <td style={{ textAlign: "right", padding: "4px 6px" }}>{pos.last_mark.toFixed(2)}</td>
                      <td style={{ textAlign: "right", padding: "4px 6px", color: pos.unrealised_pnl >= 0 ? "var(--up)" : "var(--down)" }}>
                        {pos.unrealised_pnl.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-muted)" }}>Flat — no open positions.</div>
          )}

          {s.recent_fills.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="stat-label" style={{ marginBottom: 4 }}>
                Recent fills <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>(newest first)</span>
              </div>
              <table className="num" style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ color: "var(--text-dim)", borderBottom: "1px solid var(--border-soft)" }}>
                    <th style={{ textAlign: "left", padding: "3px 6px" }}>Time</th>
                    <th style={{ textAlign: "left", padding: "3px 6px" }}>Side</th>
                    <th style={{ textAlign: "left", padding: "3px 6px" }}>Symbol</th>
                    <th style={{ textAlign: "right", padding: "3px 6px" }}>Qty</th>
                    <th style={{ textAlign: "right", padding: "3px 6px" }}>Price</th>
                    <th style={{ textAlign: "right", padding: "3px 6px" }}>Commission</th>
                  </tr>
                </thead>
                <tbody>
                  {[...s.recent_fills].reverse().map((f, i) => (
                    <tr key={`${f.order_id}-${i}`}>
                      <td style={{ padding: "3px 6px", color: "var(--text-muted)" }}>
                        {new Date(f.fill_time).toLocaleTimeString()}
                      </td>
                      <td style={{ padding: "3px 6px", color: f.side === "BUY" ? "var(--up)" : "var(--down)" }}>
                        {f.side}
                      </td>
                      <td style={{ padding: "3px 6px" }}>{f.symbol}</td>
                      <td style={{ textAlign: "right", padding: "3px 6px" }}>{f.quantity}</td>
                      <td style={{ textAlign: "right", padding: "3px 6px" }}>{f.fill_price.toFixed(4)}</td>
                      <td style={{ textAlign: "right", padding: "3px 6px", color: "var(--text-muted)" }}>
                        {f.commission.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function KV({ label, value, colour, raw = false }: { label: string; value: number; colour: string; raw?: boolean }) {
  return (
    <span style={{ color: "var(--text-dim)" }}>
      {label}{": "}
      <span style={{ color: colour, fontWeight: 600 }}>
        {raw ? value : value.toFixed(2)}
      </span>
    </span>
  );
}

function StrategyCatalog(props: { strategies: StrategySpec[] | null; error: string | null }) {
  if (props.error || props.strategies === null) {
    // Empty / unavailable catalog isn't blocking — the rest of the page works.
    // Show a small hint pointing to the push CLI rather than a scary error.
    return (
      <div
        style={{
          padding: "10px 14px",
          marginTop: 12,
          background: "var(--bg-elev)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          fontSize: 12,
          color: "var(--text-dim)",
        }}
      >
        Strategy catalog not loaded yet. Run{" "}
        <code>uv run tradepro-paper-strategies-push</code> from the Mac to
        populate it.
      </div>
    );
  }
  if (props.strategies.length === 0) {
    return null;
  }
  return (
    <div style={{ marginTop: 16 }}>
      <h3 style={{ margin: "0 0 8px", fontSize: 14, color: "var(--text-dim)" }}>
        Registered strategies ({props.strategies.length})
      </h3>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gap: 10,
        }}
      >
        {props.strategies.map((s) => (
          <div
            key={s.name}
            style={{
              padding: "10px 12px",
              border: "1px solid var(--border)",
              borderRadius: 8,
              background: "var(--bg-elev)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <code
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: "var(--text)",
                }}
              >
                {s.name}
              </code>
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                {Object.keys(s.default_params).length} params
              </span>
            </div>
            <div
              style={{
                marginTop: 6,
                fontSize: 11,
                color: "var(--text-dim)",
                lineHeight: 1.4,
              }}
              title={s.summary}
            >
              {s.summary.length > 200 ? s.summary.slice(0, 200) + "…" : s.summary}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReportList(props: {
  reports: ReportSummary[] | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { reports, selectedId, onSelect } = props;
  if (reports === null) return <div style={{ color: "var(--text-muted)" }}>Loading reports…</div>;
  if (reports.length === 0) {
    return (
      <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
        No reports yet. Run a backtest from the Mac:
        <pre
          style={{
            marginTop: 8,
            padding: 8,
            background: "var(--bg-elev)",
            borderRadius: 6,
            fontSize: 12,
            overflowX: "auto",
          }}
        >
{`uv run tradepro-paper-compare --symbol AAPL \\
  --from 2026-04-01 --to 2026-04-30 \\
  --entry "ORB-15::orb?range_minutes=15" \\
  --entry "ORB-30::orb?range_minutes=30" \\
  --push`}
        </pre>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {reports.map((r) => {
        const active = r.reportId === selectedId;
        return (
          <button
            key={r.reportId}
            onClick={() => onSelect(r.reportId)}
            style={{
              textAlign: "left",
              padding: "10px 12px",
              border: `1px solid ${active ? "var(--up)" : "var(--border)"}`,
              background: active ? "var(--bg-hover)" : "transparent",
              borderRadius: 8,
              cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <strong style={{ fontSize: 13 }}>{r.symbol}</strong>
              <span style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase" }}>{r.kind}</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
              {r.start && r.end ? `${r.start} → ${r.end}` : "single session"}
              {" · "}
              {r.entryCount} {r.entryCount === 1 ? "entry" : "entries"}
            </div>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
              received {new Date(r.receivedAtUtc).toLocaleString()}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function ReportDetail(props: { loading: boolean; payload: ComparatorPayload | null }) {
  if (props.loading) return <div style={{ color: "var(--text-muted)" }}>Loading details…</div>;
  if (!props.payload) return <div style={{ color: "var(--text-muted)" }}>Pick a report on the left.</div>;
  const p = props.payload;
  const winnerId = p.rankings.by_total_pnl[0];
  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <strong style={{ fontSize: 14 }}>{p.symbol}</strong>
        <span style={{ color: "var(--text-dim)", fontSize: 12, marginLeft: 8 }}>
          {p.start} → {p.end}
        </span>
      </div>
      <table
        className="num"
        style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}
      >
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-dim)" }}>
            <th style={{ textAlign: "left", padding: "6px 8px" }}>Strategy</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Total P&amp;L</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Win %</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Sharpe/sess</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Max DD</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Sessions</th>
            <th style={{ textAlign: "right", padding: "6px 8px" }}>Fills</th>
          </tr>
        </thead>
        <tbody>
          {p.entries.map((e) => {
            const isWinner = e.strategy_id === winnerId;
            return (
              <tr
                key={e.strategy_id}
                style={{
                  borderBottom: "1px solid var(--border-soft)",
                  background: isWinner ? "var(--up-soft)" : "transparent",
                }}
              >
                <td style={{ padding: "6px 8px" }}>
                  {isWinner && <span style={{ marginRight: 6, color: "var(--up)" }}>★</span>}
                  {e.label}
                </td>
                <td style={{ textAlign: "right", padding: "6px 8px", color: e.total_realised_pnl >= 0 ? "var(--up)" : "var(--down)" }}>
                  {e.total_realised_pnl.toFixed(2)}
                </td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{(e.win_session_pct * 100).toFixed(1)}%</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{e.sharpe_per_session.toFixed(2)}</td>
                <td style={{ textAlign: "right", padding: "6px 8px", color: "var(--down)" }}>{e.max_drawdown.toFixed(2)}</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{e.session_count}</td>
                <td style={{ textAlign: "right", padding: "6px 8px" }}>{e.total_fills}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ marginTop: 24 }}>
        <h4 style={{ margin: "0 0 8px" }}>Equity curves</h4>
        <EquityChart entries={p.entries} />
      </div>
    </div>
  );
}

function EquityChart(props: { entries: ComparatorEntry[] }) {
  // Inline SVG sparkline-style chart. Sized to the parent column.
  // No charting library required — keeps the bundle thin and the
  // first-paint instant. Switch to a real chart lib if/when overlays
  // (drawdown shading, trade markers) become needed.
  const width = 640;
  const height = 220;
  const pad = { top: 8, right: 12, bottom: 24, left: 48 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  if (props.entries.length === 0) return null;

  // Build a unified date axis across all entries (typically identical
  // since they were run on the same range, but guard for partial data).
  const dates = Array.from(
    new Set(props.entries.flatMap((e) => e.equity_curve.map(([d]) => d))),
  ).sort();
  if (dates.length < 2) {
    return <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Need ≥2 sessions to draw a curve.</div>;
  }
  const dateIndex = new Map(dates.map((d, i) => [d, i] as const));

  const allY = props.entries.flatMap((e) => e.equity_curve.map(([, v]) => v)).concat([0]);
  const yMin = Math.min(...allY);
  const yMax = Math.max(...allY);
  const ySpan = yMax - yMin || 1;

  const xFor = (d: string) => pad.left + (dateIndex.get(d)! / (dates.length - 1)) * innerW;
  const yFor = (v: number) => pad.top + innerH - ((v - yMin) / ySpan) * innerH;

  const colours = ["#4f8cff", "#1fc16b", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4"];

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ background: "var(--bg-elev)", borderRadius: 8 }}>
      {/* Y zero line */}
      <line
        x1={pad.left}
        x2={width - pad.right}
        y1={yFor(0)}
        y2={yFor(0)}
        stroke="var(--border)"
        strokeDasharray="3 3"
      />
      {/* Y axis labels: min / 0 / max */}
      {[yMin, 0, yMax].map((v) => (
        <text
          key={v}
          x={pad.left - 6}
          y={yFor(v)}
          textAnchor="end"
          alignmentBaseline="middle"
          fontSize="10"
          fill="var(--text-muted)"
        >
          {v.toFixed(0)}
        </text>
      ))}
      {/* X axis labels: first / last */}
      <text x={pad.left} y={height - 6} fontSize="10" fill="var(--text-muted)">{dates[0]}</text>
      <text x={width - pad.right} y={height - 6} fontSize="10" textAnchor="end" fill="var(--text-muted)">
        {dates[dates.length - 1]}
      </text>
      {/* One polyline per entry */}
      {props.entries.map((e, idx) => {
        const colour = colours[idx % colours.length];
        const points = e.equity_curve
          .map(([d, v]) => `${xFor(d).toFixed(1)},${yFor(v).toFixed(1)}`)
          .join(" ");
        return (
          <g key={e.strategy_id}>
            <polyline
              fill="none"
              stroke={colour}
              strokeWidth="2"
              points={points}
            />
            {/* Label at the end of the line */}
            <text
              x={width - pad.right + 2}
              y={yFor(e.equity_curve[e.equity_curve.length - 1]?.[1] ?? 0)}
              fontSize="10"
              fill={colour}
              alignmentBaseline="middle"
            >
              {e.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
