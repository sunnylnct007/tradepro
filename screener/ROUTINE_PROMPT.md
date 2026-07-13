# TradePro Daily Screener — Routine Prompt

Run the TradePro daily wheel + swing screener using IBKR MCP data.

## Steps

1. **Fetch watchlist** — use IBKR MCP `get_watchlists`, then `get_watchlist(id=101)` to get TradePro-Screen tickers and their contract IDs.

2. **Fetch SPY data** — use `search_contracts(query="SPY")` to get SPY contract ID, then `get_price_history(contract_id=..., security_type="STK", step="ONE_DAY", period="ONE_YEAR", outside_rth=false)`.

3. **For each ticker** in the watchlist, use the contract_id_ex as the integer contract_id:
   - `get_price_snapshot(contract_id=..., market_data_names=["last","misc_statistics","avg_90d_usd_volume","implied_volatility_percentile","dividend_yield","historical_vol"])`
   - `get_price_history(contract_id=..., security_type="STK", step="ONE_DAY", period="ONE_YEAR", outside_rth=false)`
   - Note: earnings_date — set to null (not available via MCP; screener handles null correctly)

4. **Write input JSON** to `/tmp/screener_data.json` in the format:
   ```json
   {
     "run_date": "YYYY-MM-DD",
     "stocks": {
       "TICKER": {
         "conid": 12345,
         "snapshot": { <full MCP snapshot response> },
         "history": { <full MCP history response> },
         "earnings_date": null
       }
     },
     "spy_history": { <full MCP history response for SPY> }
   }
   ```

5. **Run the screener**:
   ```bash
   cd /home/user/tradepro
   pip install anthropic boto3 requests -q
   python screener/daily_run.py --input-file /tmp/screener_data.json
   ```

6. **Report results** — log the JSON output (tickers screened, wheel/swing top 5, dual candidates, emails sent status).

## Notes
- SES sandbox: both `info@coreconsultingit.com` (To) and `tradepro@coreconsultingit.com` (From) must be verified.
- AWS credentials must be set in environment before running daily_run.py.
- The screener logs to stdout; errors go to stderr.
