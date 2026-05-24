Feature: Long-term fundamental analysis engine
  As a trader / analyst using TradePro
  I want multi-year trend metrics computed from annual financials
  So I can assess whether a stock has durable quality for long-term holding

  # ── compute_cagr ─────────────────────────────────────────────────────────

  Scenario: CAGR over 3 years with clean data
    When I compute_cagr with values=[150, 130, 115, 100] and years=3
    Then cagr_result is approximately 0.1453

  Scenario: CAGR returns None when insufficient data
    When I compute_cagr with values=[200, 180] and years=3
    Then cagr_result is None

  Scenario: CAGR returns None when start value is zero
    When I compute_cagr with values=[200, 100, 50, 0] and years=3
    Then cagr_result is None

  Scenario: CAGR over 5 years with exact doubling
    # 100 → 200 in 5 years ≈ 14.87% CAGR
    When I compute_cagr with values=[200, 180, 160, 130, 110, 100] and years=5
    Then cagr_result is approximately 0.1487

  # ── compute_margin_series ────────────────────────────────────────────────

  Scenario: Margin series computed from numerator and denominator
    # gross profit 40, 36, 32 on revenue 100, 90, 80 → 40%, 40%, 40%
    When I call compute_margin_series with numerator=[40, 36, 32] denominator=[100, 90, 80] max_years=3
    Then margin_series is [40.0, 40.0, 40.0]

  Scenario: Margin series returns None for zero denominator
    When I call compute_margin_series with numerator=[50, 40] denominator=[0, 100] max_years=2
    Then margin_series has None at index 0

  # ── margin_trend ─────────────────────────────────────────────────────────

  Scenario: Expanding margin is detected
    When I call margin_trend with series=[35.0, 32.0, 30.0]
    Then trend_result is "EXPANDING"

  Scenario: Compressing margin is detected
    When I call margin_trend with series=[28.0, 31.0, 35.0]
    Then trend_result is "COMPRESSING"

  Scenario: Stable margin is detected
    When I call margin_trend with series=[30.5, 30.0, 30.2]
    Then trend_result is "STABLE"

  Scenario: Insufficient data returns INSUFFICIENT_DATA
    When I call margin_trend with series=[30.0]
    Then trend_result is "INSUFFICIENT_DATA"

  # ── compute_fcf ──────────────────────────────────────────────────────────

  Scenario: FCF derived from operating cash flow and capex
    # capex is negative in yfinance convention
    When I call compute_fcf with op_cashflow=[500, 450] capex=[-100, -80] max_years=2
    Then fcf_series first value is 400.0

  Scenario: FCF derived when capex is given as positive (normalised)
    When I call compute_fcf with op_cashflow=[500] capex=[100] max_years=1
    Then fcf_series first value is 400.0

  # ── compute_fcf_conversion ───────────────────────────────────────────────

  Scenario: FCF conversion calculated correctly
    When I call compute_fcf_conversion with fcf=[400, 360] net_income=[500, 450] max_years=2
    Then fcf_conversion first value is 80.0

  Scenario: FCF conversion returns None for zero net income
    When I call compute_fcf_conversion with fcf=[400] net_income=[0] max_years=1
    Then fcf_conversion first value is None

  # ── compute_financial_trends ─────────────────────────────────────────────

  Scenario: Trends computed from stub DataFrames
    Given stub financials for a tech company with 4 years of growth
    When I call compute_financial_trends
    Then revenue_cagr_3y is positive
    And op_margin_pct_latest is positive
    And fcf_conversion_latest is not None
    And debt_equity_latest is not None

  Scenario: Trends return None gracefully when data is missing
    Given empty stub financials
    When I call compute_financial_trends
    Then revenue_cagr_3y is None
    And op_margin_pct_latest is None

  # ── _template_key ────────────────────────────────────────────────────────

  Scenario Outline: Sector template key resolved correctly
    When I call _template_key with sector="<sector>" industry="<industry>"
    Then template_key_result is "<expected>"

    Examples:
      | sector               | industry              | expected          |
      | Financial Services   | Banking               | banking           |
      | Technology           | Software—Application  | technology        |
      | Healthcare           | Drug Manufacturers    | pharma            |
      | Energy               | Oil & Gas E&P         | energy            |
      | Consumer Cyclical    | Retail—Apparel        | consumer_cyclical |
      | Industrials          | Aerospace & Defense   | default           |
      | (empty)              | (empty)               | default           |

  # ── analyse_long_term (fully stubbed — no network) ───────────────────────

  Scenario: analyse_long_term returns structured result for a known ticker
    Given a ticker stub for "MSFT" with 4 years of income stmt, balance sheet, and cashflow
    When I call analyse_long_term for "MSFT"
    Then the result has ok=True
    And the result contains key "trends"
    And the result contains key "quality"
    And the result contains key "info_snapshot"
    And the result contains key "template"
    And the result contains key "peers"

  Scenario: Quality grade is A for a strongly growing company
    Given a ticker stub with revenue_cagr=0.20 roe=25.0 fcf_conversion=90.0 de=0.3
    When I call analyse_long_term for "STRONG"
    Then quality grade is "A"
    And positives list is non-empty

  Scenario: Quality grade is D or F for a declining company
    Given a ticker stub with revenue_cagr=-0.05 roe=3.0 fcf_conversion=15.0 de=2.5
    When I call analyse_long_term for "WEAK"
    Then quality grade is one of ["D", "F"]
    And negatives list is non-empty

  Scenario: analyse_long_term handles ticker with missing financials gracefully
    Given a ticker stub for "NODATA" with empty financials
    When I call analyse_long_term for "NODATA"
    Then the result has ok=True
    And warnings list is non-empty

  Scenario: Peer comparison included when peers are known
    Given a ticker stub for "JPM" with 4 years of income stmt, balance sheet, and cashflow
    And peer stubs for ["BAC", "GS", "MS"] each with 4 years of data
    When I call analyse_long_term for "JPM"
    Then peers list has at least 1 entry

  Scenario: Peer comparison skipped when include_peers=False
    Given a ticker stub for "MSFT" with 4 years of income stmt, balance sheet, and cashflow
    When I call analyse_long_term for "MSFT" with include_peers=False
    Then peers list is empty

  Scenario: KNOWN_PEERS contains NSE Indian tickers
    Then KNOWN_PEERS contains key "HDFCBANK.NS"
    And KNOWN_PEERS["HDFCBANK.NS"] includes "ICICIBANK.NS"

  Scenario: SECTOR_TEMPLATES banking entry has yfinance_gaps listed
    Then SECTOR_TEMPLATES["banking"] has non-empty yfinance_gaps
    And SECTOR_TEMPLATES["banking"]["yfinance_gaps"] mentions "NIM"
