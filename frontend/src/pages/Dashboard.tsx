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
  const [provider, setProvider] = useState<string>(config.defaultProvider);
  const [providers, setProviders] = useState<string[]>([]);
  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.ukWatchlist().then(setWatchlist).catch(() => {});
    api.providers().then((r) => setProviders(r.providers)).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .candles({ symbol, provider })
      .then(setSeries)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [symbol, provider]);

  const chartData = useMemo(
    () =>
      (series?.candles ?? []).map((c) => ({
        t: c.timestamp.slice(0, 10),
        close: Number(c.close),
      })),
    [series],
  );

  const latest = series?.candles.at(-1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2 style={{ margin: 0 }}>Dashboard</h2>

      <section style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        {watchlist && (
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {watchlist.items.map((i) => (
              <option key={i.symbol} value={i.symbol}>
                {i.label} ({i.symbol})
              </option>
            ))}
          </select>
        )}
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Or enter a symbol (e.g. BARC.L, AAPL, BTCUSDT)"
          style={{ flex: 1, minWidth: 260, padding: 6 }}
        />
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {providers.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </section>

      {error && <div style={{ color: "#b00020" }}>Error: {error}</div>}
      {loading && <div>Loading…</div>}

      {latest && (
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          <Stat label="Last close" value={latest.close.toFixed(2)} />
          <Stat label="As of" value={latest.timestamp.slice(0, 10)} />
          <Stat label="Points" value={String(series?.candles.length ?? 0)} />
          <Stat label="Provider" value={series?.provider ?? ""} />
        </div>
      )}

      <div style={{ height: 400, background: "#fafafa", borderRadius: 8, padding: 8 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData}>
            <CartesianGrid stroke="#eee" />
            <XAxis dataKey="t" minTickGap={40} />
            <YAxis domain={["auto", "auto"]} />
            <Tooltip />
            <Line type="monotone" dataKey="close" stroke="#0b3d91" dot={false} strokeWidth={1.5} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: "#888" }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600 }}>{value}</div>
    </div>
  );
}
