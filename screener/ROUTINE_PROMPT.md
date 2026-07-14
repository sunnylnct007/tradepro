# TradePro Daily Screener — Routine Prompt

Run the TradePro daily wheel + swing screener using IBKR MCP data.

## Steps

### 1 — Fetch watchlist
Use `get_watchlists`, then `get_watchlist(id=101)` to get TradePro-Screen tickers and their conids.

### 2 — Fetch SPY data
`search_contracts(query="SPY")` → get SPY conid.
`get_price_history(contract_id=<spy_conid>, security_type="STK", step="ONE_DAY", period="ONE_YEAR", outside_rth=false)`

### 3 — For each ticker: snapshot + history (parallel)
Use `get_price_snapshot` with ALL relevant fields:
```
market_data_names: [
  "last", "misc_statistics", "avg_90d_usd_volume",
  "implied_volatility_percentile", "implied_vol_underlying",
  "dividend_yield", "historical_vol",
  "underlying_avg_option_volume", "underlying_today_option_volume",
  "volume", "change"
]
```
Use `get_price_history(contract_id=..., security_type="STK", step="ONE_DAY", period="ONE_YEAR", outside_rth=false)`

### 4 — For each ticker: fetch option chain data
This enriches the wheel screener with real premium, IV, and open interest.

For each ticker:
1. `get_option_parameters(contract_id=<stock_conid>, security_type="STK")` → get list of expiries
2. Find the expiry closest to 30 days from today (use `expirations[].date` field, pick nearest ≥ 21 days out)
3. `get_option_data(expiration_id=<id>, min_strike=<price*0.85>, max_strike=<price*1.05>)` → get strikes ± 15% around spot
4. Identify the ATM put (strike closest to current price, below spot)
5. For the ATM put and 3 strikes below it (OTM puts), call `get_price_snapshot(contract_id=<put_contract_id>, market_data_names=["last","bid_ask","implied_vol","option_open_interest","option_volume"])` — use the top-level `exchange` field from get_option_data response
6. Collect: strike, bid, ask, mid, IV, open_interest, volume for each put

If option data is unavailable for a ticker, set `"options": null` in the JSON.

### 5 — Write input JSON
Write to `/tmp/screener_data.json`:
```json
{
  "run_date": "YYYY-MM-DD",
  "stocks": {
    "TICKER": {
      "conid": 12345,
      "snapshot": { <full MCP snapshot response> },
      "history":  { <full MCP history response> },
      "earnings_date": null,
      "options": {
        "expiry": "2026-08-15",
        "exchange": "SMART",
        "atm_put": {
          "strike": 70.0,
          "bid": 1.45,
          "ask": 1.55,
          "mid": 1.50,
          "iv_pct": 28.5,
          "open_interest": 2500,
          "volume": 350
        },
        "chain": [
          {"strike": 65.0, "bid": 0.80, "ask": 0.90, "iv_pct": 31.2, "oi": 1200, "volume": 120},
          {"strike": 67.5, "bid": 1.05, "ask": 1.15, "iv_pct": 29.4, "oi": 1800, "volume": 210},
          {"strike": 70.0, "bid": 1.45, "ask": 1.55, "iv_pct": 28.5, "oi": 2500, "volume": 350},
          {"strike": 72.5, "bid": 1.90, "ask": 2.05, "iv_pct": 27.8, "oi": 1900, "volume": 280}
        ]
      }
    }
  },
  "spy_history": { <full MCP history response for SPY> }
}
```

### 6 — Run the screener
```bash
cd /home/user/tradepro
pip install anthropic boto3 matplotlib -q
python screener/daily_run.py --input-file /tmp/screener_data.json
```

### 7 — Report results
Log the JSON output: tickers screened, wheel/swing top 5, dual candidates, emails sent status.

## Notes
- AWS credentials must be exported before running daily_run.py.
- SES: `info@coreconsultingit.com` is verified as both FROM and TO in eu-west-2.
- If get_option_data returns no rows for a ticker, skip and set `"options": null`.
- Option snapshot calls: use `security_type="OPT"` and pass the `exchange` from get_option_data.
