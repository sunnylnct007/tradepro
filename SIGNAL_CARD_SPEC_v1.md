# TradePro Complete Signal Card Spec v1.0

> Generated: 22 May 2026
> Purpose: Define the complete "actionable signal" output that tells the user
> exactly what to do — not just direction but entry, stop, target, sizing,
> pattern, and news context in one card.
> Inspired by: real EC trade today — user had to manually figure out stop/target/OCA
> That entire workflow should be pre-computed by TradePro at signal time.

---

## The problem today

TradePro currently outputs:
```
MU → WAIT (3/8)
ABBV → BUY (6/8)
```

What a trader actually needs:
```
ABBV → BUY
  Entry:      $213.50 (current ask)
  Stop loss:  $207.20 (ATR-based, -3.0%)
  Target:     $226.40 (ATR × 2, +6.0%)
  Shares:     31 (based on £500 max risk)
  Pattern:    RSI mean reversion from oversold
  News:       Q1 beat 3 weeks ago, next earnings Jul 29
  Confidence: HIGH — all 3 horizon signals agree
```

The gap between those two outputs is what caused 45 minutes of IBKR confusion today.
TradePro should pre-compute everything the user needs to place the order correctly
the first time.

---

## 1. The Oscillating Range Play — Detecting It

The user's instinct ("continuously fluctuating in and out") describes a specific
and very tradeable pattern: **mean reversion within a defined range**.

### What to detect

```
Pattern: RANGE_OSCILLATOR

Conditions (all must be true):
  1. Price has stayed within a ±20% band for 90+ days
  2. RSI has touched both >65 (overbought) and <35 (oversold) at least twice each
     in the last 90 days
  3. Each RSI extreme has been followed by a reversal of >5%
  4. No strong directional trend (200d SMA slope < 15 degrees)
  5. ATR_14 / price > 1.5%  (enough daily movement to be worth trading)
```

### Signal logic

```
If RANGE_OSCILLATOR pattern detected:
  If RSI > 65 → SELL signal (top of range)
    Entry: current price
    Target: lower band (20th percentile of 90d range)
    Stop: recent swing high + ATR_14

  If RSI < 35 → BUY signal (bottom of range)
    Entry: current price
    Target: upper band (80th percentile of 90d range)
    Stop: recent swing low - ATR_14
```

### Screening for range oscillators (new universe scan)

Run daily across all universes. Flag any ticker where:
- 90-day price range is <25% (tight range)
- RSI has crossed 65 and 35 at least twice each in 90 days
- No earnings in next 14 days (avoid event risk)

This gives a daily list of "in-range tradeable names" — exactly what the user
is looking for.

---

## 2. News Integration — What to Capture

News affects signals in three ways:

### 2.1 News as a signal trigger

| News event | Signal effect | Data source |
|---|---|---|
| Earnings beat + guidance raised | Bullish bias for 5-10 days | Alpha Vantage, Yahoo Finance |
| Earnings miss + guidance cut | Bearish bias, avoid entry | Alpha Vantage, Yahoo Finance |
| Analyst upgrade + PT raise | Short-term momentum | Finnhub (fix BUG-003 first) |
| Analyst downgrade + PT cut | Reduce conviction | Finnhub |
| CEO/CFO departure | Governance flag, reduce conviction | Yahoo Finance news |
| Regulatory fine / SEC action | SELL bias, ESG controversy flag | SEC EDGAR 8-K |
| M&A rumour / announcement | Special situation — suspend normal signal | Yahoo Finance news |
| Index inclusion / exclusion | Forced flow — BUY/SELL bias | Index provider announcements |

### 2.2 News as a signal suppressor

Before any signal fires, check:
- Earnings within 7 days → suppress swing signal, show WARNING
- Fed/BoE meeting within 2 days → suppress rate-sensitive signals
- Major macro event today → reduce conviction by one tier

### 2.3 News sentiment score

```python
# Using GDELT (free) for news volume + sentiment
# Signal: if negative article count spikes > 2 std dev above 30d avg → bearish flag
# Signal: if positive article count spikes > 2 std dev above 30d avg → confirm bull

news_sentiment = {
    "ticker": "ABBV",
    "article_count_30d_avg": 12,
    "article_count_today": 8,
    "sentiment_score": 0.65,      # 0-1, higher = more positive
    "sentiment_trend": "STABLE",   # IMPROVING / STABLE / DETERIORATING
    "key_headlines": [
        "AbbVie raises guidance after Q1 beat",
        "Skyrizi continues market share gains"
    ],
    "earnings_proximity_days": 68,
    "suppress_signal": false
}
```

---

## 3. Complete Signal Card — Full Specification

Every signal TradePro generates must output this complete card.
Nothing is optional except fields that genuinely don't apply.

```json
{
  "signal_id": "SIG-20260522-ABBV-001",
  "generated_at": "2026-05-22T17:30:00Z",
  "ticker": "ABBV",
  "company_name": "AbbVie Inc",
  "exchange": "NYSE",
  "currency": "USD",

  "signal": {
    "direction": "BUY",
    "horizon": "swing",
    "strategy_type": "mean_reversion",
    "pattern": "RSI_OVERSOLD_BOUNCE",
    "conviction": "HIGH",
    "score": "6/8",
    "coherence_check": "PASS"
  },

  "entry": {
    "price": 213.50,
    "type": "LIMIT",
    "valid_until": "GTC",
    "note": "Enter on ask or set limit just above. Do not chase if price moves >2% before fill."
  },

  "exit": {
    "stop_loss": {
      "price": 207.20,
      "distance_pct": 3.0,
      "method": "ATR_ADJUSTED",
      "atr_14": 3.21,
      "atr_multiplier": 1.5,
      "type": "STOP",
      "tif": "GTC",
      "note": "Hard stop. Do not move down."
    },
    "take_profit": {
      "price": 226.40,
      "distance_pct": 6.0,
      "method": "ATR_ADJUSTED",
      "rr_ratio": 2.0,
      "type": "LIMIT",
      "tif": "GTC",
      "note": "First target. Consider taking 50% here and trailing the rest."
    },
    "trailing_stop": {
      "available": true,
      "trail_distance": 6.42,
      "note": "Activate after price reaches 50% of target distance."
    },
    "time_exit": null
  },

  "sizing": {
    "account_size_gbp": 6446,
    "risk_per_trade_pct": 1.0,
    "max_loss_gbp": 64.46,
    "stop_distance_usd": 6.30,
    "fx_rate_gbpusd": 1.3440,
    "stop_distance_gbp": 4.69,
    "suggested_shares": 13,
    "suggested_notional_usd": 2775.50,
    "suggested_notional_gbp": 2065.10,
    "note": "Based on 1% account risk. Adjust if you want higher/lower exposure."
  },

  "pattern_detail": {
    "pattern_name": "RSI_OVERSOLD_BOUNCE",
    "description": "RSI pulled back below 35 from overbought territory, now recovering. Classic mean reversion entry.",
    "historical_occurrences": 8,
    "win_rate_pct": 87.5,
    "avg_gain_pct": 8.4,
    "avg_loss_pct": -4.2,
    "median_holding_days": 12,
    "range_oscillator": false
  },

  "news_context": {
    "sentiment_score": 0.71,
    "sentiment_trend": "STABLE",
    "earnings_proximity_days": 68,
    "last_earnings_result": "BEAT",
    "analyst_consensus": "BUY",
    "analyst_pt_avg": 252.00,
    "analyst_pt_high": 280.00,
    "analyst_pt_low": 195.00,
    "recent_headlines": [
      "AbbVie raises full-year guidance after Q1 beat",
      "Skyrizi market share gains continue"
    ],
    "suppress_signal": false,
    "suppress_reason": null
  },

  "esg_context": {
    "governance_score": 69,
    "governance_risk": false,
    "carbon_intensity": "LOW",
    "controversy_flag": null
  },

  "ibkr_order_instructions": {
    "note": "Place as bracket order via Exit Strategy button on position",
    "entry_order": {
      "action": "BUY",
      "quantity": 13,
      "order_type": "LIMIT",
      "limit_price": 213.50,
      "tif": "DAY"
    },
    "profit_taker": {
      "action": "SELL",
      "quantity": 13,
      "order_type": "LIMIT",
      "limit_price": 226.40,
      "tif": "GTC"
    },
    "stop_loss": {
      "action": "SELL",
      "quantity": 13,
      "order_type": "STOP",
      "stop_price": 207.20,
      "tif": "GTC"
    },
    "oca_required": true,
    "oca_note": "Use Exit Strategy from position panel. Both exit orders must share same OCA group."
  },

  "data_quality": {
    "price_age_minutes": 2,
    "feed_healthy": true,
    "analyst_feed_healthy": false,
    "analyst_feed_note": "BUG-003: Finnhub returning 0 events. Analyst data from Yahoo Finance fallback.",
    "cache_age_hours": 0.1
  }
}
```

---

## 4. Range Oscillator Screener — Implementation Plan

This directly addresses the "continuously fluctuating" use case.

### Step 1 — Detect range-bound stocks (daily scan)

```python
def is_range_oscillator(ticker, lookback_days=90):
    prices = get_ohlcv(ticker, lookback_days)
    rsi = compute_rsi(prices, period=14)

    range_width = (prices.high.max() - prices.low.min()) / prices.low.min()
    rsi_overbought_count = (rsi > 65).sum()
    rsi_oversold_count = (rsi < 35).sum()
    sma200_slope = compute_sma_slope(prices, 200)

    return (
        range_width < 0.25 and          # tight range
        rsi_overbought_count >= 2 and    # touched overbought twice
        rsi_oversold_count >= 2 and      # touched oversold twice
        abs(sma200_slope) < 15 and       # no strong trend
        earnings_days_away(ticker) > 14  # no imminent earnings
    )
```

### Step 2 — Generate entry signal when at extreme

```python
def range_oscillator_signal(ticker):
    if not is_range_oscillator(ticker):
        return None

    rsi = current_rsi(ticker)
    price = current_price(ticker)
    atr = compute_atr(ticker, 14)
    range_low = price_percentile(ticker, 90, pct=20)
    range_high = price_percentile(ticker, 90, pct=80)

    if rsi < 35:
        return SignalCard(
            direction="BUY",
            pattern="RANGE_OSCILLATOR_OVERSOLD",
            entry=price,
            stop=recent_swing_low(ticker) - atr,
            target=range_high,
            conviction="MEDIUM"  # range plays are lower conviction than trend
        )
    elif rsi > 65:
        return SignalCard(
            direction="SELL_SHORT",  # or AVOID if long-only account
            pattern="RANGE_OSCILLATOR_OVERBOUGHT",
            entry=price,
            stop=recent_swing_high(ticker) + atr,
            target=range_low,
            conviction="MEDIUM"
        )
```

### Step 3 — Surface in UI as new signal category

New tab in TradePro: **"Range Plays"**
- Shows current range-bound stocks at extremes
- Entry, stop, target pre-computed
- Sorted by: RSI distance from extreme (most oversold first)
- Filter: min ADV, min range_width, exclude earnings within 14 days

---

## 5. What This Means for IBKR Integration

Today's 45-minute bracket order struggle revealed a product gap.
TradePro should generate the exact IBKR order instructions:

```
Signal fires on ABBV →

TradePro displays:
┌─────────────────────────────────────────┐
│ ABBV — BUY SIGNAL                       │
│ Pattern: RSI Oversold Bounce            │
│                                         │
│ Entry:       $213.50  (Limit, DAY)      │
│ Stop loss:   $207.20  (Stop, GTC)       │
│ Take profit: $226.40  (Limit, GTC)      │
│ Shares:      13                         │
│ Max risk:    £64                        │
│                                         │
│ In IBKR: place entry order first,       │
│ then use Exit Strategy → set both       │
│ exits in one ticket. Both must be SELL. │
│                                         │
│ [Copy entry price] [Copy stop] [Copy TP]│
└─────────────────────────────────────────┘
```

Eventually: one-click order submission via IBKR TWS API (Track B/Phase 3).
For now: the card tells the user exactly what to type.

---

## 6. Updated Priority Backlog

Adding to improvements-v1.0 backlog:

| Priority | Feature | Effort | Phase |
|---|---|---|---|
| P1 | Complete signal card with entry/stop/target computed at signal time | Medium | 4A/4B |
| P1 | ATR-based stop and target (replaces fixed %) | Small | 4A/4B |
| P1 | Position sizing from account size + stop distance | Small | 4A/4B |
| P1 | IBKR order instructions on signal card | Small | 4A/4B |
| P2 | Range oscillator pattern detection | Medium | 5A |
| P2 | Range oscillator screener (daily scan → "Range Plays" tab) | Medium | 5A |
| P2 | Earnings proximity suppressor (no signal within 7 days of earnings) | Small | 5A |
| P2 | News sentiment score per ticker (GDELT) | Medium | 5A |
| P2 | News headline display on signal card | Small | 5A |
| P3 | Analyst PT on signal card (Yahoo Finance fallback while BUG-003 open) | Small | 4A |
| P3 | Trailing stop option for swing positions | Medium | 5A |
| P3 | "Range Plays" UI tab | Medium | 5A |
