Feature: Combined verdict — technical + catalyst + analyst fusion
  Phase 17.5 of the catalyst sprint. Fuses the existing technical
  bucket with the new catalyst overlay and analyst recommendations
  into a single annotated recommendation. NEVER replaces the
  technical bucket — annotates it.

  Pin the rule table against real-trade-shaped fixtures so any future
  tweak surfaces here. Each new trade sample the user provides
  becomes a scenario; the file grows with the user's actual decisions.

  # ----- The motivating case: Ecopetrol (EC) 2026-05-21 -----
  Scenario: EC — technical WAIT with bullish near-term election catalyst → BUY with tight stop
    Given a row with bucket "WAIT" and reason "89th percentile of 52w range — extended, no fresh entry edge"
    And the news headlines:
      | title                                        | sentiment | days_offset |
      | Colombia heads to runoff election in 10 days |  0.4      | 0           |
      | Oil price surge as OPEC+ holds output        |  0.6      | 0           |
      | Ecopetrol earnings beat estimates Q1         |  0.7      | -6          |
    And analyst counts strong_buy=0 buy=1 hold=0 sell=3 strong_sell=1
    When I derive the combined verdict
    Then the technical signal is "WAIT"
    And the catalyst signal is "STRONG_BUY"
    And the analyst signal is "STRONG_AVOID"
    And the combined_kind is "BUY_WITH_RISK"
    And the confidence is "Medium-High"
    And the reasoning mentions "catalyst window is real and dated"

  # ----- Aligned bullish — everything agrees -----
  Scenario: Technical BUY + bullish catalyst + analyst BUY → STRONG BUY
    Given a row with bucket "BUY" and reason "above SMA200, RSI 55, fresh entry"
    And the news headlines:
      | title                              | sentiment | days_offset |
      | Apple Q3 earnings beat consensus   |  0.7      | -2          |
    And analyst counts strong_buy=5 buy=8 hold=2 sell=0 strong_sell=0
    When I derive the combined verdict
    Then the combined_kind is "STRONG_BUY"
    And the confidence is "High"

  # ----- Technical BUY but bearish catalyst — wait -----
  Scenario: Technical BUY but bearish near-term catalyst → WAIT
    Given a row with bucket "BUY" and reason "above SMA200 fresh entry"
    And the news headlines:
      | title                                                | sentiment | days_offset |
      | FDA rejects new drug application — biotech crashes   | -0.7      | 0           |
    And analyst counts strong_buy=2 buy=3 hold=1 sell=0 strong_sell=0
    When I derive the combined verdict
    Then the technical signal is "BUY"
    And the catalyst signal is "AVOID"
    And the combined_kind is "WAIT"

  # ----- Technical AVOID despite bullish catalyst — still AVOID -----
  Scenario: Technical AVOID + bullish catalyst → AVOID DESPITE CATALYST
    Given a row with bucket "AVOID" and reason "below SMA200, confirmed downtrend"
    And the news headlines:
      | title                                  | sentiment | days_offset |
      | Apple Q3 earnings beat consensus       |  0.8      | 0           |
    And analyst counts strong_buy=0 buy=0 hold=0 sell=0 strong_sell=0
    When I derive the combined verdict
    Then the combined_kind is "AVOID_DESPITE_CATALYST"

  # ----- Quiet day — no catalysts, no fresh edge -----
  Scenario: Technical WAIT + no catalysts → WAIT (no fresh edge)
    Given a row with bucket "WAIT" and reason "mid-range, no catalyst"
    And the news headlines:
      | title                                | sentiment | days_offset |
      | Stock price rises on broad gains     |  0.1      | -1          |
    And analyst counts strong_buy=0 buy=0 hold=0 sell=0 strong_sell=0
    When I derive the combined verdict
    Then the catalyst signal is "NONE"
    And the combined_kind is "WAIT"

  # ----- Aligned bearish — technical AVOID + bearish catalyst -----
  Scenario: Technical AVOID + bearish catalyst → AVOID
    Given a row with bucket "AVOID" and reason "broken trend"
    And the news headlines:
      | title                                          | sentiment | days_offset |
      | SEC investigation widens — sources             | -0.8      | 0           |
    And analyst counts strong_buy=0 buy=1 hold=2 sell=4 strong_sell=2
    When I derive the combined verdict
    Then the combined_kind is "AVOID"
