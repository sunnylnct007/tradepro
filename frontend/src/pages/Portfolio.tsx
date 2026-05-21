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

  // Pull T212 positions on mount.
  useEffect(() => {
    let cancelled = false;
    fetch(`${config.apiBaseUrl}/api/integrations/trading212/positions`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((d) => { if (!cancelled) setResp(d as T212PositionsResponse); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, []);

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
  if (!resp) {
    return <Frame><div style={emptyStyle}>Loading positions…</div></Frame>;
  }
  if (!resp.enabled) {
    return (
      <Frame>
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
        <h1 style={{ margin: 0, fontSize: 22 }}>Your portfolio</h1>
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
