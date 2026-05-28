import { useEffect, useMemo, useState } from "react";
import { config } from "../config";
import type {
  CompareLatestResponse,
  CompareUniverseSummary,
  CompareRow,
} from "../api/types";
import { api } from "../api/client";
import { TrustDot, TrustLegend } from "../components/TrustDot";

interface T212Position {
  ticker: string | null;
  yahooSymbol: string | null;
  instrumentName: string | null;
  currency: string | null;
  isin: string | null;
  quantity: number;
  averagePricePaid: number | null;
  currentPrice: number | null;
  unrealisedPct: number | null;
  unrealisedAbs: number | null;
  createdAt: string | null;
}

interface T212PositionsResponse {
  enabled: boolean;
  mode?: string;
  message?: string;
  fetchedAtUtc?: string;
  positionCount?: number;
  positions: T212Position[];
  /** When the T212 fetch failed (auth, 404, network), this carries
   * the underlying detail so the UI can stop pretending the user
   * has 0 positions. Null on a clean fetch. */
  error?: string | null;
  httpStatus?: number | null;
}

/**
 * "What you actually own" page. Shows every Trading 212 position
 * cross-referenced against today's compare verdict (bucket + swing
 * score + action hint per holding). The data is the same the email
 * digest renders; this is the in-browser surface for it.
 *
 * Empty state when T212 isn't configured walks the user through
 * setup so the page never just shows a void.
 */
export function Portfolio() {
  const [resp, setResp] = useState<T212PositionsResponse | null>(null);
  const [universes, setUniverses] = useState<CompareUniverseSummary[]>([]);
  const [universeRows, setUniverseRows] = useState<Record<string, CompareRow[]>>({});
  const [error, setError] = useState<string | null>(null);
  // Default to demo because that's where the trader's strategy is
  // booking right now. Live requires explicit opt-in.
  const [account, setAccount] = useState<"demo" | "live">("demo");

  // Pull T212 positions whenever the account toggle flips.
  useEffect(() => {
    let cancelled = false;
    setResp(null);
    setError(null);
    fetch(`${config.apiBaseUrl}/api/integrations/trading212/positions?account=${account}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((d) => { if (!cancelled) setResp(d as T212PositionsResponse); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [account]);

  // Pull every cached universe so we can cross-reference symbols → verdict.
  useEffect(() => {
    let cancelled = false;
    api.compareUniverses()
      .then((r) => { if (!cancelled) setUniverses(r.universes); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all(
      universes.map((u) =>
        api.compareLatest(u.universe).then(
          (r: CompareLatestResponse) => [u.universe, (r.payload?.rows ?? []) as CompareRow[]] as [string, CompareRow[]],
        ).catch(() => [u.universe, [] as CompareRow[]] as [string, CompareRow[]]),
      ),
    ).then((pairs) => {
      if (cancelled) return;
      const next: Record<string, CompareRow[]> = {};
      for (const [name, rows] of pairs) next[name] = rows;
      setUniverseRows(next);
    });
    return () => { cancelled = true; };
  }, [universes]);

  // Best-rank row per symbol across all universes — same logic the
  // email digest uses; keeps the verdict consistent across surfaces.
  const verdictBySymbol = useMemo(() => {
    const m = new Map<string, { row: CompareRow; universe: string }>();
    for (const [name, rows] of Object.entries(universeRows)) {
      for (const r of rows) {
        const sym = (r.symbol ?? "").toUpperCase();
        if (!sym) continue;
        const existing = m.get(sym);
        if (!existing || (r.rank ?? 1e9) < (existing.row.rank ?? 1e9)) {
          m.set(sym, { row: r, universe: name });
        }
      }
    }
    return m;
  }, [universeRows]);

  if (error) {
    return <Frame><div style={emptyStyle}>Couldn't load portfolio: {error}</div></Frame>;
  }
  const accountToggle = (
    <AccountToggle account={account} onChange={setAccount} />
  );

  if (!resp) {
    return (
      <Frame>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
          {accountToggle}
        </div>
        <div style={emptyStyle}>Loading {account} positions…</div>
      </Frame>
    );
  }
  if (!resp.enabled) {
    return (
      <Frame>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
          {accountToggle}
        </div>
        <div style={emptyStyle}>
          <h2 style={{ margin: 0, color: "var(--text)" }}>Trading 212 not connected</h2>
          <p style={{ marginTop: 8 }}>
            {resp.message ?? "Set Trading212 mode + API credentials to see your positions here."}
          </p>
          <ol style={{ marginTop: 12, paddingLeft: 20, lineHeight: 1.7, fontSize: 13 }}>
            <li>T212 app → <b>Settings → API (Beta)</b> → generate a key (read-only is fine).</li>
            <li>Add to <code>.env</code> at repo root:
              <pre style={preStyle}>{`TRADEPRO_T212_MODE=demo
TRADEPRO_T212_API_KEY=...
TRADEPRO_T212_API_SECRET=...`}</pre>
            </li>
            <li>Restart the api: <code>docker compose up -d --force-recreate api</code></li>
          </ol>
        </div>
      </Frame>
    );
  }
  if (!resp.positions.length) {
    // T212 returned an error — auth or endpoint failure. Surface
    // the underlying reason instead of silently claiming "no positions",
    // which had been gaslighting users with funded demo accounts.
    if (resp.error) {
      return (
        <Frame>
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
            {accountToggle}
          </div>
          <div style={{ ...emptyStyle, borderColor: "var(--down)", color: "var(--down)" }}>
            <h2 style={{ margin: 0, color: "var(--down)" }}>
              Couldn't reach Trading 212 ({resp.mode})
            </h2>
            <p style={{ marginTop: 8, color: "var(--text)" }}>{resp.error}</p>
            {resp.httpStatus === 401 && (
              <p style={{ marginTop: 12, fontSize: 13, color: "var(--text-dim)" }}>
                401 means the API key was rejected. T212 uses a single key
                in the <code>Authorization</code> header — there is no secret.
                Generate a fresh key in the T212 app
                (<b>Settings → API (Beta) → Generate API key</b>),
                set <code>TRADEPRO_T212_API_KEY</code> in your <code>.env</code>,
                and restart the api: <code>docker compose up -d --force-recreate api</code>.
              </p>
            )}
            {resp.httpStatus === 403 && (
              <p style={{ marginTop: 12, fontSize: 13, color: "var(--text-dim)" }}>
                403 usually means the key is valid but lacks the scope for
                this endpoint. When generating the key in T212, tick
                <b> "View portfolio"</b> (read-only is enough).
              </p>
            )}
          </div>
        </Frame>
      );
    }
    return (
      <Frame>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
          {accountToggle}
        </div>
        <div style={emptyStyle}>
          No open positions in your T212 {resp.mode} account.
        </div>
      </Frame>
    );
  }

  const totalUnrealised = resp.positions.reduce(
    (acc, p) => acc + (p.unrealisedAbs ?? 0), 0,
  );
  const ccy = resp.positions[0]?.currency ?? "";

  return (
    <Frame>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <h1 style={{ margin: 0, fontSize: 22 }}>Your portfolio</h1>
          {accountToggle}
        </div>
        <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
          T212 <TrustDot id="portfolio.t212_chip" /> · <ModeChip mode={resp.mode} /> · {resp.positionCount} position{resp.positionCount === 1 ? "" : "s"} ·
          {" "}<span style={{ color: totalUnrealised >= 0 ? "var(--up)" : "var(--down)", fontWeight: 600 }}>
            {totalUnrealised >= 0 ? "+" : ""}{totalUnrealised.toFixed(2)} {ccy}
          </span> unrealised<TrustDot id="portfolio.total_unrealised" />
        </div>
      </div>
      <div style={{ marginBottom: 8 }}>
        <TrustLegend />
      </div>

      <CashPanel account={account} />
      <PositionDriftPanel account={account} />

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", color: "var(--text-dim)", textAlign: "left" }}>
              <Th>Instrument</Th>
              <Th align="right">Qty</Th>
              <Th align="right">Avg cost</Th>
              <Th align="right">Now</Th>
              <Th align="right">P&amp;L %<TrustDot id="portfolio.pnl_cells" /></Th>
              <Th align="right">P&amp;L abs</Th>
              <Th align="center">Today<TrustDot id="portfolio.today_verdict" /></Th>
              <Th align="center">Swing<TrustDot id="portfolio.swing_mini" /></Th>
            </tr>
          </thead>
          <tbody>
            {resp.positions.map((p) => {
              const sym = (p.yahooSymbol ?? p.ticker ?? "").toUpperCase();
              const match = sym ? verdictBySymbol.get(sym) : undefined;
              const upct = p.unrealisedPct ?? 0;
              const colour = upct > 0 ? "var(--up)" : upct < 0 ? "var(--down)" : "var(--text-dim)";
              return (
                <tr key={p.ticker ?? p.isin ?? Math.random()} style={{ borderTop: "1px solid var(--border)" }}>
                  <Td>
                    <div style={{ fontWeight: 600 }}>{p.instrumentName ?? sym}</div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {p.ticker} · {p.currency}
                    </div>
                  </Td>
                  <Td align="right" className="num">{p.quantity?.toFixed(4)}</Td>
                  <Td align="right" className="num">{p.averagePricePaid?.toFixed(2)}</Td>
                  <Td align="right" className="num">{p.currentPrice?.toFixed(2)}</Td>
                  <Td align="right" className="num" style={{ color: colour, fontWeight: 600 }}>
                    {upct >= 0 ? "+" : ""}{upct.toFixed(2)}%
                  </Td>
                  <Td align="right" className="num" style={{ color: colour }}>
                    {(p.unrealisedAbs ?? 0) >= 0 ? "+" : ""}
                    {(p.unrealisedAbs ?? 0).toFixed(2)}
                  </Td>
                  <Td align="center">
                    {match ? (
                      <span title={match.row.bucket_reason ?? ""} style={{
                        color: bucketColour(match.row.bucket),
                        fontWeight: 700,
                      }}>
                        {match.row.bucket ?? "—"}
                      </span>
                    ) : <span style={{ color: "var(--text-muted)" }}>—</span>}
                  </Td>
                  <Td align="center">
                    <SwingMini swing={match?.row.swing_score ?? null} />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 16, fontSize: 11, color: "var(--text-muted)" }}>
        Today / Swing columns show the system's verdict for the same symbol from
        whichever cached universe ranks it highest. "—" means the symbol isn't
        in any tracked universe — try <code>@tradepro evaluate_symbols(...)</code> in
        Claude Desktop or add it to a watchlist.
      </div>
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return <div style={{ maxWidth: 980, margin: "0 auto" }}>{children}</div>;
}

function ModeChip({ mode }: { mode?: string }) {
  if (!mode) return <span>{mode}</span>;
  const colour = mode === "live" ? "var(--down)" : mode === "demo" ? "var(--neutral)" : "var(--text-muted)";
  return <span style={{ color: colour, fontWeight: 700, textTransform: "uppercase" }}>{mode}</span>;
}

function bucketColour(bucket?: string) {
  if (bucket === "BUY") return "var(--up)";
  if (bucket === "AVOID") return "var(--down)";
  if (bucket === "WAIT") return "var(--neutral)";
  return "var(--text-dim)";
}

function SwingMini({ swing }: { swing: import("../api/types").SwingScore | null }) {
  if (!swing || swing.total === null || swing.total === undefined) {
    return <span style={{ color: "var(--text-muted)" }}>—</span>;
  }
  const colour =
    swing.verdict === "STRONG_BUY" ? "var(--up)"
    : swing.verdict === "BUY" ? "#4f8cff"
    : swing.verdict === "AVOID" ? "var(--down)"
    : "var(--neutral)";
  const layers = swing.layers;
  const title =
    `Swing ${swing.total}/8 → ${swing.verdict}\n` +
    `  Q${layers.quality} · V${layers.valuation} · E${layers.event} · P${layers.price}`;
  return (
    <span title={title} style={{ color: colour, fontWeight: 700 }}>
      {swing.total}/8
    </span>
  );
}

const emptyStyle: React.CSSProperties = {
  padding: "32px 16px",
  textAlign: "center",
  color: "var(--text-dim)",
  border: "1px dashed var(--border)",
  borderRadius: 8,
  background: "rgba(0,0,0,0.12)",
};

const preStyle: React.CSSProperties = {
  marginTop: 6,
  padding: 8,
  background: "rgba(0,0,0,0.25)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  fontSize: 11,
};

function Th({ children, align = "left" }: { children: React.ReactNode; align?: "left" | "right" | "center" }) {
  return (
    <th style={{ padding: "8px 10px", textAlign: align, fontWeight: 600, fontSize: 11, letterSpacing: "0.04em", textTransform: "uppercase" }}>
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
  style,
  className,
}: {
  children: React.ReactNode;
  align?: "left" | "right" | "center";
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <td className={className} style={{ padding: "8px 10px", textAlign: align, ...style }}>
      {children}
    </td>
  );
}

/**
 * Cash panel — T212 account balance. Free is what's available to
 * trade; Invested is already deployed in positions. Refuses to be
 * silent on T212 errors because "I thought I had cash" is the worst
 * failure mode before placing an order. Auto-refresh on account flip.
 *
 * CFD cash is separate (T212 splits Invest from CFD into different
 * products); this panel shows Invest only today. Follow-up: add a
 * CFD cash row beneath when /cfd/* client wiring lands.
 */
function CashPanel({ account }: { account: "demo" | "live" }) {
  const [cash, setCash] = useState<Awaited<ReturnType<typeof api.t212Cash>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCash(null);
    setError(null);
    api
      .t212Cash(account)
      .then((d) => { if (!cancelled) setCash(d); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [account]);

  if (error) {
    return (
      <div style={cashBoxStyle("rgba(239,68,68,0.06)", "rgba(239,68,68,0.3)")}>
        <span style={{ fontSize: 12, color: "var(--down)" }}>
          Cash fetch failed: {error}
        </span>
      </div>
    );
  }
  if (!cash) {
    return (
      <div style={cashBoxStyle()}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading cash…</span>
      </div>
    );
  }
  if (!cash.enabled) {
    return (
      <div style={cashBoxStyle()}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          {cash.message ?? "T212 cash not available."}
        </span>
      </div>
    );
  }
  if (cash.error) {
    return (
      <div style={cashBoxStyle("rgba(239,68,68,0.06)", "rgba(239,68,68,0.3)")}>
        <span style={{ fontSize: 12, color: "var(--down)" }}>
          T212 cash error: {cash.error}
        </span>
      </div>
    );
  }
  const ccy = cash.currency || "";
  const fmt = (n: number | null | undefined) =>
    n == null ? "—" : `${ccy} ${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return (
    <div style={cashBoxStyle("rgba(31,193,107,0.05)", "rgba(31,193,107,0.25)")}>
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "center" }}>
        <CashStat label="Free" value={fmt(cash.free)} big tone="ok" />
        <CashStat label="Invested" value={fmt(cash.invested)} />
        <CashStat label="Total" value={fmt(cash.total)} />
        {cash.blocked != null && cash.blocked !== 0 && (
          <CashStat label="Blocked" value={fmt(cash.blocked)} tone="muted" />
        )}
        {cash.ppl != null && (
          <CashStat
            label="Open P&L"
            value={fmt(cash.ppl)}
            tone={cash.ppl >= 0 ? "ok" : "down"}
          />
        )}
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}>
          T212 {cash.mode.toUpperCase()} · Invest
        </span>
      </div>
    </div>
  );
}

function CashStat({
  label,
  value,
  big,
  tone,
}: {
  label: string;
  value: string;
  big?: boolean;
  tone?: "ok" | "down" | "muted";
}) {
  const fg =
    tone === "ok"
      ? "#1fc16b"
      : tone === "down"
      ? "#ef4444"
      : tone === "muted"
      ? "var(--text-muted)"
      : "var(--text)";
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: "0.04em", textTransform: "uppercase" }}>
        {label}
      </div>
      <div
        style={{
          fontSize: big ? 20 : 14,
          fontWeight: big ? 700 : 500,
          color: fg,
          fontFamily: "monospace",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function cashBoxStyle(bg?: string, border?: string): React.CSSProperties {
  return {
    padding: "10px 14px",
    marginBottom: 12,
    background: bg ?? "rgba(255,255,255,0.03)",
    border: `1px solid ${border ?? "var(--border)"}`,
    borderRadius: 6,
  };
}

/**
 * Position drift — compares OMS-derived positions against T212 broker
 * actuals via /api/oms/positions/diff. Drift = bug (T212 rejected
 * something we marked filled, or operator placed outside the OMS).
 * Collapsed by default so it doesn't shout when everything matches;
 * the header reveals the drift count so a non-zero state is visible.
 */
function PositionDriftPanel({ account }: { account: "demo" | "live" }) {
  const [diff, setDiff] = useState<Awaited<ReturnType<typeof api.omsPositionsDiff>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const refresh = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = await api.omsPositionsDiff(account);
      setDiff(d);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // Auto-load once; account flip refreshes.
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account]);

  if (error) {
    return (
      <div style={driftBoxStyle("rgba(239,68,68,0.06)", "rgba(239,68,68,0.3)")}>
        <span style={{ fontSize: 12, color: "var(--down)" }}>
          Position drift check failed: {error}
        </span>
      </div>
    );
  }
  if (!diff) {
    return (
      <div style={driftBoxStyle()}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading drift check…</span>
      </div>
    );
  }

  const drifted = diff.drifted;
  const tone = drifted === 0 ? "ok" : "warn";
  const bg = tone === "ok" ? "rgba(31,193,107,0.06)" : "rgba(217,119,6,0.08)";
  const border = tone === "ok" ? "rgba(31,193,107,0.25)" : "rgba(217,119,6,0.3)";
  const fg = tone === "ok" ? "#1fc16b" : "#d97706";

  return (
    <div style={driftBoxStyle(bg, border)}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: fg, fontWeight: 600 }}>
          {drifted === 0
            ? `✓ OMS matches T212 ${account} across ${diff.totalSymbols} symbol${diff.totalSymbols === 1 ? "" : "s"}`
            : `⚠ ${drifted} symbol${drifted === 1 ? "" : "s"} drifted between OMS and T212 ${account}`}
        </span>
        {drifted > 0 && (
          <button
            onClick={() => setExpanded((x) => !x)}
            style={{
              padding: "2px 8px", fontSize: 11,
              border: "1px solid var(--border)", borderRadius: 4,
              background: "transparent", color: "var(--text-dim)",
              cursor: "pointer",
            }}
          >
            {expanded ? "hide" : "show"} rows
          </button>
        )}
        <button
          onClick={refresh}
          disabled={busy}
          style={{
            padding: "2px 8px", fontSize: 11,
            border: "1px solid var(--border)", borderRadius: 4,
            background: "transparent", color: "var(--text-muted)",
            cursor: busy ? "wait" : "pointer",
            marginLeft: "auto",
          }}
        >
          {busy ? "checking…" : "recheck"}
        </button>
      </div>
      {expanded && drifted > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 8, fontSize: 12 }}>
          <thead>
            <tr style={{ color: "var(--text-dim)" }}>
              <th style={{ textAlign: "left", padding: "4px 8px" }}>Symbol</th>
              <th style={{ textAlign: "right", padding: "4px 8px" }}>OMS qty</th>
              <th style={{ textAlign: "right", padding: "4px 8px" }}>T212 qty</th>
              <th style={{ textAlign: "right", padding: "4px 8px" }}>Diff</th>
            </tr>
          </thead>
          <tbody>
            {diff.rows.filter((r) => r.diff !== 0).map((r) => (
              <tr key={r.symbol} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={{ padding: "4px 8px" }}>{r.symbol}</td>
                <td style={{ padding: "4px 8px", textAlign: "right", fontFamily: "monospace" }}>{r.omsQty}</td>
                <td style={{ padding: "4px 8px", textAlign: "right", fontFamily: "monospace" }}>{r.t212Qty}</td>
                <td
                  style={{
                    padding: "4px 8px",
                    textAlign: "right",
                    fontFamily: "monospace",
                    color: r.diff > 0 ? "#1fc16b" : "#ef4444",
                    fontWeight: 600,
                  }}
                >
                  {r.diff > 0 ? "+" : ""}{r.diff}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {diff.t212Error && (
        <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
          T212 fetch warning: {diff.t212Error}
        </div>
      )}
    </div>
  );
}

function driftBoxStyle(bg?: string, border?: string): React.CSSProperties {
  return {
    padding: "8px 12px",
    marginBottom: 12,
    background: bg ?? "rgba(255,255,255,0.03)",
    border: `1px solid ${border ?? "var(--border)"}`,
    borderRadius: 6,
  };
}

function AccountToggle({
  account,
  onChange,
}: {
  account: "demo" | "live";
  onChange: (a: "demo" | "live") => void;
}) {
  // Demo first because it's the safer default — Live needs explicit click.
  // Live is red-tinted to make sure no one accidentally trades real money
  // thinking they're on the demo screen.
  return (
    <div style={{ display: "flex", gap: 4 }}>
      {(["demo", "live"] as const).map((a) => (
        <button
          key={a}
          onClick={() => onChange(a)}
          style={{
            padding: "3px 10px",
            fontSize: 11,
            borderRadius: 999,
            border: `1px solid ${
              account === a
                ? a === "live"
                  ? "#ef4444"
                  : "#4f8cff"
                : "var(--border)"
            }`,
            background:
              account === a
                ? a === "live"
                  ? "rgba(239,68,68,0.10)"
                  : "rgba(79,140,255,0.10)"
                : "transparent",
            color:
              account === a
                ? a === "live"
                  ? "#ef4444"
                  : "#4f8cff"
                : "var(--text-dim)",
            cursor: "pointer",
            letterSpacing: "0.04em",
            textTransform: "uppercase",
            fontWeight: 600,
          }}
          title={
            a === "demo"
              ? "Trading 212 demo account (paper money)"
              : "Trading 212 LIVE account (real money)"
          }
        >
          {a}
        </button>
      ))}
    </div>
  );
}
