Feature: Macro regime gate

  The macro regime gate converts three market-stress signals — VIX,
  HYG credit-spread drawdown, and 10Y yield trend — into a single
  integer: 1=GREEN (full size), 2=AMBER (reduced), 3=RED (stopped).

  All scenarios call _compute_risk_mode() directly so they run without
  any network calls or cached data.

  Background:
    Given the macro regime module is imported

  # ──────────────────────────────────────────────────────────────────
  # GREEN scenarios
  # ──────────────────────────────────────────────────────────────────

  Scenario: GREEN when all signals normal
    When I call compute_risk_mode with vix=14.5 hyg_dd=-1.5 tnx_change=0.10 regimes=[]
    Then the risk mode is 1
    And the label is "GREEN"
    And the size multiplier is 1.0

  Scenario: GREEN when VIX just below AMBER threshold
    When I call compute_risk_mode with vix=21.9 hyg_dd=-1.0 tnx_change=0.10 regimes=[]
    Then the risk mode is 1

  Scenario: GREEN when HYG just below AMBER threshold
    When I call compute_risk_mode with vix=16.0 hyg_dd=-3.9 tnx_change=0.10 regimes=[]
    Then the risk mode is 1

  # ──────────────────────────────────────────────────────────────────
  # AMBER scenarios
  # ──────────────────────────────────────────────────────────────────

  Scenario: AMBER when VIX elevated
    When I call compute_risk_mode with vix=24.0 hyg_dd=-2.0 tnx_change=0.10 regimes=[]
    Then the risk mode is 2
    And the label is "AMBER"
    And the size multiplier is 0.6

  Scenario: AMBER when HYG stressed
    When I call compute_risk_mode with vix=18.0 hyg_dd=-5.5 tnx_change=0.10 regimes=[]
    Then the risk mode is 2

  Scenario: AMBER when 10Y yield rising sharply
    When I call compute_risk_mode with vix=16.0 hyg_dd=-2.0 tnx_change=0.50 regimes=[]
    Then the risk mode is 2

  Scenario: AMBER from active stress regime alone
    When I call compute_risk_mode with vix=15.0 hyg_dd=-1.0 tnx_change=0.10 regimes=["covid_2020"]
    Then the risk mode is at least 2

  # ──────────────────────────────────────────────────────────────────
  # RED scenarios
  # ──────────────────────────────────────────────────────────────────

  Scenario: RED when VIX spikes
    When I call compute_risk_mode with vix=35.0 hyg_dd=-3.0 tnx_change=0.10 regimes=[]
    Then the risk mode is 3
    And the label is "RED"
    And the size multiplier is 0.0

  Scenario: RED when HYG severely stressed
    When I call compute_risk_mode with vix=18.0 hyg_dd=-9.0 tnx_change=0.10 regimes=[]
    Then the risk mode is 3

  Scenario: RED stays RED when both VIX and HYG trigger
    When I call compute_risk_mode with vix=40.0 hyg_dd=-10.0 tnx_change=0.60 regimes=[]
    Then the risk mode is 3

  # ──────────────────────────────────────────────────────────────────
  # Helper contracts
  # ──────────────────────────────────────────────────────────────────

  Scenario Outline: risk_mode_label returns the right string
    When I call risk_mode_label with <mode>
    Then the label is "<expected>"

    Examples:
      | mode | expected |
      | 1    | GREEN    |
      | 2    | AMBER    |
      | 3    | RED      |

  Scenario Outline: size_multiplier returns expected value
    When I call size_multiplier with <mode>
    Then the multiplier is <expected>

    Examples:
      | mode | expected |
      | 1    | 1.0      |
      | 2    | 0.6      |
      | 3    | 0.0      |

  Scenario: invalidate_cache clears the cached result
    Given the cache has been populated
    When I call invalidate_cache
    Then the next get_risk_mode call runs a fresh computation
