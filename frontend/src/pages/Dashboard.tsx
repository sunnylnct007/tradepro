import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { CandleSeries, Watchlist } from "../api/types";
import { config } from "../config";

export function Dashboard() {
  const [watchlist, setWatchlist] = useState<Watchlist | null>(null);
  const [symbol, setSymbol] = useState<string>("^FTSE");
  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.ukWatchlist().then(setWatchlist).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .candles({ symbol, provider: config.defaultProvider })
      .then(setSeries)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [symbol]);

  const chartData = useMemo(
    () =>
      (series?.candles ?? []).map((c) => ({
        t: c.timestamp.slice(0, 10),
        close: Number(c.close),
      })),
    [series],
  );

  const latest = series?.candles.at(-1);
  const first = series?.candles[0];
  const changePct =
    latest && first && first.close > 0 ? ((latest.close - first.close) / first.close) * 100 : null;
  const changeTone = changePct != null ? (changePct >= 0 ? "up" : "down") : undefined;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24 }}>Charts</h1>
        <p style={{ color: "var(--text-dim)", margin: "6px 0 0 0" }}>
          Quick price exploration. Powered by Yahoo Finance.
        </p>
      </div>

      <section
        className="card"
        style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}
      >
        {watchlist && (
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {watchlist.items.map((i) => (
              <option key={i.symbol} value={i.symbol}>{i.label} · {i.symbol}</option>
            ))}
          </select>
        )}
        <input
          className="num"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Or type a symbol (BARC.L, AAPL, ^FTSE…)"
          style={{ flex: 1, minWidth: 260 }}
        />
      </section>

      {error && (
        <div
          className="card"
          style={{ borderColor: "var(--down)", color: "var(--down)", background: "var(--down-soft)" }}
        >
          {error}
        </div>
      )}
      {loading && <div style={{ color: "var(--text-dim)" }}>Loading…</div>}

      {latest && (
        <section
          className="card"
          style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 14 }}
        >
          <Stat label="Last close" value={latest.close.toFixed(2)} />
          {changePct != null && (
            <Stat
              label="Period return"
              value={`${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`}
              tone={changeTone}
            />
          )}
          <Stat label="As of" value={latest.timestamp.slice(0, 10)} />
          <Stat label="Bars" value={String(series?.candles.length ?? 0)} />
        </section>
      )}

      <section className="card" style={{ padding: 8 }}>
        <div style={{ height: 420 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="t" minTickGap={40} />
              <YAxis domain={["auto", "auto"]} />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="close"
                stroke="var(--accent)"
                dot={false}
                strokeWidth={2}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  const colour = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : "var(--text)";
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color: colour }}>{value}</div>
    </div>
  );
}
