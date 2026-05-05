Feature: Family-4 earnings BEAT_AND_RETREAT signal
  The setup: a stock beats earnings → rallies → pulls back 5-15%
  within ~60 days. The pullback is the entry signal. Time-decaying:
  day-5-of-60 vs day-55-of-60 matters. Pin the verdict mapping so
  a future refactor can't silently flip "STRONG" into "MODERATE".

  Scenario: beat + 8% retreat inside the window → STRONG
    Given an earnings beat 10 days ago with surprise 5%
    And post-earnings prices that retreated 8% from peak
    When I evaluate the beat-and-retreat signal
    Then the verdict is "STRONG"
    And fired is True
    And days_remaining_in_window is at least 1

  Scenario: beat but only 2% retreat → MODERATE
    Given an earnings beat 5 days ago with surprise 6%
    And post-earnings prices that retreated 2% from peak
    When I evaluate the beat-and-retreat signal
    Then the verdict is "MODERATE"
    And fired is False

  Scenario: beat with 20% retreat (thesis breaking) → MODERATE
    Given an earnings beat 12 days ago with surprise 7%
    And post-earnings prices that retreated 20% from peak
    When I evaluate the beat-and-retreat signal
    Then the verdict is "MODERATE"
    And fired is False

  Scenario: beat with no retreat (still rallying) → MODERATE
    Given an earnings beat 8 days ago with surprise 10%
    And post-earnings prices that kept rallying
    When I evaluate the beat-and-retreat signal
    Then the verdict is "MODERATE"

  Scenario: missed earnings → NO_BEAT regardless of retreat
    Given an earnings miss 7 days ago with surprise -3%
    And post-earnings prices that retreated 8% from peak
    When I evaluate the beat-and-retreat signal
    Then the verdict is "NO_BEAT"
    And fired is False

  Scenario: window expired (90 days post-beat) → EXPIRED
    Given an earnings beat 90 days ago with surprise 5%
    And post-earnings prices that retreated 8% from peak
    When I evaluate the beat-and-retreat signal
    Then the verdict is "EXPIRED"
    And fired is False

  Scenario: no recent earnings → NO_RECENT
    Given no earnings within the last 90 days
    When I evaluate the beat-and-retreat signal
    Then the verdict is "NO_RECENT"

  Scenario: trace row formatting — STRONG verdict produces a pass row
    Given a STRONG beat-and-retreat signal envelope
    When I build the earnings trace row
    Then the trace status is "pass"
    And the trace detail mentions "beat"
    And the trace detail mentions "retreat"
