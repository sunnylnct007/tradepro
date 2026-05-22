Feature: Earnings-proximity suppressor
  Per IMPROVEMENT_SUGGESTIONS_v1.md §2.2 + SIGNAL_CARD_SPEC §2.2 — when
  an earnings announcement lands within the 7-day window, refuse to
  call BUY. Post-print gap risk swallows the reward leg of a 1:2 setup;
  the suppressor doesn't try to predict the beat/miss, it just opts
  out of the trade for the event window.

  Demotion rules:
    - bucket BUY → WAIT (no entry recommendation)
    - conviction HIGH → MEDIUM (LOW / MEDIUM unchanged)
    - flag always set when within window (UI surfaces a WARNING even
      if the bucket was already WAIT)

  Scenario: BUY within 5 days of earnings is demoted to WAIT
    Given a pre-earnings bucket "BUY" with reason "majority long"
    And pre-earnings conviction "HIGH"
    And earnings in 5 days
    When I apply the earnings suppressor
    Then the post-suppressor bucket is "WAIT"
    And the post-suppressor conviction is "MEDIUM"
    And the suppressed flag is True
    And the post-suppressor reason mentions "5d"

  Scenario: BUY on the threshold (7 days) is still demoted
    Given a pre-earnings bucket "BUY" with reason "trend + consensus"
    And pre-earnings conviction "HIGH"
    And earnings in 7 days
    When I apply the earnings suppressor
    Then the post-suppressor bucket is "WAIT"
    And the suppressed flag is True

  Scenario: BUY at 8 days passes through cleanly
    Given a pre-earnings bucket "BUY" with reason "trend + consensus"
    And pre-earnings conviction "HIGH"
    And earnings in 8 days
    When I apply the earnings suppressor
    Then the post-suppressor bucket is "BUY"
    And the post-suppressor conviction is "HIGH"
    And the suppressed flag is False

  Scenario: WAIT bucket still gets the warning flag inside the window
    Given a pre-earnings bucket "WAIT" with reason "only 2 of 5 long"
    And pre-earnings conviction "MEDIUM"
    And earnings in 3 days
    When I apply the earnings suppressor
    Then the post-suppressor bucket is "WAIT"
    And the suppressed flag is True

  Scenario: missing earnings data is a no-op
    Given a pre-earnings bucket "BUY" with reason "majority long"
    And pre-earnings conviction "HIGH"
    And earnings days_until is None
    When I apply the earnings suppressor
    Then the post-suppressor bucket is "BUY"
    And the post-suppressor conviction is "HIGH"
    And the suppressed flag is False

  Scenario: LOW conviction stays LOW (no promotion)
    Given a pre-earnings bucket "WAIT" with reason "trend filters fail"
    And pre-earnings conviction "LOW"
    And earnings in 4 days
    When I apply the earnings suppressor
    Then the post-suppressor conviction is "LOW"
    And the suppressed flag is True

  Scenario: negative days_until (post-earnings) is not suppressed
    Given a pre-earnings bucket "BUY" with reason "post-earnings drift"
    And pre-earnings conviction "HIGH"
    And earnings in -2 days
    When I apply the earnings suppressor
    Then the post-suppressor bucket is "BUY"
    And the suppressed flag is False
