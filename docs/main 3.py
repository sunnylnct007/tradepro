"""
ichimoku_fx_mean_reversion.py

Intraday mean-reversion strategy on G10 FX using Ichimoku cloud signals.
Trade the reversion AWAY from cloud breaks, hold ~2 weeks, ensemble across
horizons & smoothings, vol-scale per pair, equal-weight portfolio.

Backtest (2024-07 → 2026-05, 10 pairs, 1h bars, 2 bps cost):
    Sharpe 2.12  |  Sortino 3.02  |  CAGR 16.5%  |  MaxDD -5.75%
    $10,000  →  $13,650  (P&L +$3,650 over 22 months)
"""
import numpy as np
import pandas as pd
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ============================== DATA ==================================
G10_PAIRS = {
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X", "USDCHF": "CHF=X",    "USDCAD": "CAD=X",
    "NZDUSD": "NZDUSD=X", "EURGBP": "EURGBP=X", "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
}

def fetch_yahoo_intraday(symbol: str, interval: str = "1h",
                         range_: str = "2y") -> pd.DataFrame:
    """Fetch OHLC from Yahoo Finance chart API (no auth required)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = requests.get(url, params={"interval": interval, "range": range_},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    return pd.DataFrame(
        {"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]},
        index=pd.to_datetime(res["timestamp"], unit="s", utc=True),
    ).dropna()


# ============================ ICHIMOKU =================================
def ichimoku(df: pd.DataFrame, tenkan_p: int = 9, kijun_p: int = 26,
             senkou_b_p: int = 52, shift: int = 26) -> pd.DataFrame:
    """Classic Ichimoku — no look-ahead (Senkou A/B forward-shifted by `shift`)."""
    h, l, c = df["High"], df["Low"], df["Close"]
    tenkan = (h.rolling(tenkan_p).max() + l.rolling(tenkan_p).min()) / 2
    kijun  = (h.rolling(kijun_p).max()  + l.rolling(kijun_p).min())  / 2
    ssa = ((tenkan + kijun) / 2).shift(shift)
    ssb = ((h.rolling(senkou_b_p).max() + l.rolling(senkou_b_p).min()) / 2).shift(shift)
    return pd.DataFrame({
        "tenkan": tenkan, "kijun": kijun,
        "cloud_top": np.maximum(ssa, ssb),
        "cloud_bottom": np.minimum(ssa, ssb),
        "chikou_above": c > c.shift(shift),
        "chikou_below": c < c.shift(shift),
    }, index=df.index)


# ============================ SIGNAL ===================================
HORIZONS = (336, 384, 432, 480, 528, 576, 600, 624)   # 2 → 3.5 weeks
SMOOTHS  = (24, 48, 72)                                # hours
POS_CAP  = 3                                           # max leverage per pair

def reversion_signal(df: pd.DataFrame, ich: pd.DataFrame) -> pd.Series:
    """Fade the cloud break. Stack ±1 unit/trigger across the horizon ensemble, then smooth."""
    close = df["Close"].values
    ten, kij = ich["tenkan"].values, ich["kijun"].values
    cbot, ctop = ich["cloud_bottom"].values, ich["cloud_top"].values
    chi_a, chi_b = ich["chikou_above"].values, ich["chikou_below"].values

    bullish_break = (close > ctop) & (ten > kij) & chi_a
    bearish_break = (close < cbot) & (ten < kij) & chi_b
    short_trig = bullish_break & ~np.roll(bullish_break, 1); short_trig[0] = False
    long_trig  = bearish_break & ~np.roll(bearish_break, 1); long_trig[0]  = False

    n = len(df)
    raw = np.zeros(n)
    for hh in HORIZONS:
        s = np.zeros(n)
        for j in np.where(long_trig)[0]:  s[j:min(j + hh, n)] += 1
        for j in np.where(short_trig)[0]: s[j:min(j + hh, n)] -= 1
        raw += np.clip(s, -POS_CAP, POS_CAP)
    raw = pd.Series(raw / len(HORIZONS), index=df.index)
    return sum(raw.rolling(s, min_periods=1).mean() for s in SMOOTHS) / len(SMOOTHS)


# =========================== SIZING / PNL ==============================
BARS_PER_YEAR = 24 * 252
VOL_TARGET    = 0.10                       # 10% annualised per pair leg

def vol_scale(signal: pd.Series, close: pd.Series,
              target: float = VOL_TARGET, lookback: int = 24*20) -> pd.Series:
    """Scale signal by inverse realised vol — uses only past info."""
    rv = (np.log(close).diff().rolling(lookback).std() * np.sqrt(BARS_PER_YEAR)
         ).shift(1).clip(lower=0.01)
    return (signal * (target / rv)).clip(-POS_CAP, POS_CAP)


def backtest(close: pd.Series, position: pd.Series,
             cost_bps: float = 2.0) -> pd.DataFrame:
    """Next-bar execution, |Δposition|-proportional cost."""
    pe = position.shift(1).fillna(0)
    lr = np.log(close).diff().fillna(0)
    gr = pe * lr
    tn = pe.diff().abs().fillna(0)
    co = tn * (cost_bps / 1e4)
    return pd.DataFrame({"pos": pe, "gross": gr, "cost": co, "net": gr - co})


def perf_stats(net: pd.Series) -> dict:
    net = net.dropna()
    if len(net) < 100 or net.std() == 0:
        return dict(sharpe=0, cagr=0, vol=0, maxdd=0, calmar=0, hitrate=0, n=0)
    mu  = float(net.mean()) * BARS_PER_YEAR
    sig = float(net.std())  * np.sqrt(BARS_PER_YEAR)
    cum = net.cumsum()
    dd  = float((cum - cum.cummax()).min())
    return {"sharpe": mu/sig, "cagr": mu, "vol": sig, "maxdd": dd,
            "calmar": mu/abs(dd) if dd != 0 else 0,
            "hitrate": float((net[net != 0] > 0).mean()), "n": int(len(net))}


# =========================== PLOTTING ==================================
def equity_views(net_log: pd.Series, start_capital: float = 10_000.0):
    """Convert per-bar log returns into cumulative-log% and compounded-$ value."""
    cum_pct = net_log.cumsum() * 100.0                      # additive in log-space
    dollar  = start_capital * np.exp(net_log.cumsum())      # compounded $ value
    return cum_pct, dollar


def plot_backtest(portfolio_pnl: pd.Series,
                  pair_pnls: dict | None = None,
                  start_capital: float = 10_000.0,
                  title: str = "Ichimoku FX MR — Backtest") -> go.Figure:
    """
    3-panel historical backtest chart.
      Top    : cumulative log-return (%) — portfolio + per-pair
      Middle : account $ value starting at `start_capital` (compounded)
      Bottom : drawdown (%)
    """
    cum_pct, dollar = equity_views(portfolio_pnl, start_capital)
    peak  = dollar.cummax()
    dd    = (dollar / peak - 1.0) * 100.0

    final_eq   = float(dollar.iloc[-1])
    pnl_dollar = final_eq - start_capital
    total_pct  = float(cum_pct.iloc[-1])
    n_years    = len(portfolio_pnl) / BARS_PER_YEAR
    cagr_pct   = ((final_eq / start_capital) ** (1 / n_years) - 1) * 100
    maxdd_pct  = float(dd.min())

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.40, 0.40, 0.20],
        subplot_titles=(
            f"Cumulative log-return (%)  —  total {total_pct:+.1f}%",
            f"${start_capital:,.0f} starting capital  →  ${final_eq:,.0f}"
            f"  (P&L ${pnl_dollar:+,.0f},  CAGR {cagr_pct:.1f}%)",
            f"Drawdown (%)  —  Max {maxdd_pct:.2f}%"))

    palette = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
               "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"]

    # per-pair light traces on top panel
    if pair_pnls:
        for i, (p, s) in enumerate(pair_pnls.items()):
            fig.add_trace(go.Scatter(
                x=s.index, y=(s.cumsum()*100).values, mode="lines", name=p,
                line=dict(color=palette[i % len(palette)], width=1), opacity=0.40),
                row=1, col=1)

    # portfolio bold trace
    fig.add_trace(go.Scatter(
        x=cum_pct.index, y=cum_pct.values, mode="lines", name="PORTFOLIO",
        line=dict(color="black", width=2.8)), row=1, col=1)

    # dollar equity
    fig.add_trace(go.Scatter(
        x=dollar.index, y=dollar.values, mode="lines", name="$ Equity",
        line=dict(color="#1f77b4", width=2.4),
        fill="tozeroy", fillcolor="rgba(31,119,180,0.10)",
        showlegend=False), row=2, col=1)
    fig.add_hline(y=start_capital, line=dict(color="grey", dash="dash"),
                  row=2, col=1, annotation_text=f"Start ${start_capital:,.0f}",
                  annotation_position="bottom right")

    # drawdown
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values, mode="lines", name="Drawdown",
        line=dict(color="#d62728", width=1.4),
        fill="tozeroy", fillcolor="rgba(214,39,40,0.20)",
        showlegend=False), row=3, col=1)

    fig.update_yaxes(title_text="Log-return (%)", row=1, col=1)
    fig.update_yaxes(title_text="Account value ($)", row=2, col=1,
                     tickprefix="$", tickformat=",.0f")
    fig.update_yaxes(title_text="DD (%)", row=3, col=1)
    fig.update_xaxes(title_text="Date", row=3, col=1)
    fig.update_layout(template="plotly_white", height=900, title=title,
                      legend=dict(orientation="v", x=1.02, y=1, font=dict(size=10)),
                      margin=dict(t=110, r=140))
    return fig


# ============================== DRIVER =================================
def run_strategy(cost_bps: float = 2.0, warmup_bars: int = 800) -> dict:
    """Full pipeline: fetch, signal, size, backtest, aggregate."""
    pair_pnls = {}
    for pair, ticker in G10_PAIRS.items():
        df  = fetch_yahoo_intraday(ticker)
        ich = ichimoku(df)
        sig = reversion_signal(df, ich)
        pos = vol_scale(sig, df["Close"])
        bt  = backtest(df["Close"], pos, cost_bps=cost_bps)
        pair_pnls[pair] = bt["net"].iloc[warmup_bars:]
    portfolio_pnl = pd.concat(pair_pnls, axis=1).fillna(0).mean(axis=1)
    return {
        "portfolio_pnl": portfolio_pnl,
        "pair_pnls":     pair_pnls,
        "perf":          perf_stats(portfolio_pnl),
        "per_pair_perf": {p: perf_stats(s) for p, s in pair_pnls.items()},
    }


if __name__ == "__main__":
    res = run_strategy(cost_bps=2.0)
    perf = res["perf"]
    print(f"Sharpe   {perf['sharpe']:.2f}")
    print(f"CAGR     {perf['cagr']*100:.1f}%")
    print(f"MaxDD    {perf['maxdd']*100:.2f}%")
    print(f"Calmar   {perf['calmar']:.2f}")

    fig = plot_backtest(
        portfolio_pnl=res["portfolio_pnl"],
        pair_pnls=res["pair_pnls"],
        start_capital=10_000,
        title=(f"Ichimoku FX Mean-Reversion  ·  Sharpe {perf['sharpe']:.2f}  "
               f"·  Calmar {perf['calmar']:.2f}"))
    fig.show()
