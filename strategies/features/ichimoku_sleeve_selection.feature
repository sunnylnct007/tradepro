Feature: Ichimoku equity trader-faithful sleeve selection (no top-N cut)
  # The trader's docs/portfolio 1.py holds EVERY signalling name in a sleeve and
  # sizes each at weight = 1/max(sleeve_size, n_signal), scaling the sleeve to
  # ≤100% invested. There is NO top-N-by-conviction cap — that was an invented
  # divergence. As more names signal, each gets a thinner slice; capital can't
  # exceed the budget because the weights sum to ≤1, not because names are cut.
  # (Whole-share rounding may later skip a name whose slice is <1 share, but
  # that's a sizing-time decision in on_bar, not an entry-selection cut.)

  Scenario: every signalling name in a sleeve is selected to enter (no cut)
    Given three above-cloud names STRONG, MED, WEAK with decreasing conviction
    And a sleeve "core" containing STRONG, MED, WEAK with 2 slots
    When the equity strategy starts the session
    Then the selected entries are exactly STRONG, MED and WEAK
    And each is weighted 1/max(2,3) of sleeve capital

  Scenario: a name already held is not re-entered but still occupies its weight
    Given three above-cloud names STRONG, MED, WEAK with decreasing conviction
    And a sleeve "core" containing STRONG, MED, WEAK with 2 slots
    And WEAK is already held from the broker seed
    When the equity strategy starts the session
    Then the selected entries are exactly STRONG and MED
    And WEAK is held (signalling) but not re-entered
