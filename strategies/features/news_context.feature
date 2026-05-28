Feature: News context block on every signal card
  Implements the news_context schema from SIGNAL_CARD_SPEC_v1.md §2.3
  + §3. Pure transformation over existing fields: takes the row's
  sentiment_summary + news items + earnings days_until and produces
  the shape the UI / IBKR card reads.

  sentiment_score is the LLM mean_sentiment ([-1, +1]) remapped to
  [0, 1] where higher = more positive. sentiment_trend defaults to
  STABLE until we store historic per-ticker sentiment windows.
  suppress_signal mirrors the earnings suppressor's 7-day threshold
  so UI and engine never disagree about whether a card is hot.

  Scenario: positive mean_sentiment maps to a high score
    Given a sentiment_summary with mean_sentiment 0.6
    And no news items
    And news-context earnings_days is None
    When I compute news context
    Then the sentiment_score is approximately 0.8
    And the sentiment_trend is "STABLE"
    And the suppress_signal flag is False

  Scenario: negative mean_sentiment maps to a low score
    Given a sentiment_summary with mean_sentiment -0.4
    And no news items
    And news-context earnings_days is None
    When I compute news context
    Then the sentiment_score is approximately 0.3

  Scenario: missing sentiment_summary produces a null score
    Given no sentiment_summary
    And no news items
    And news-context earnings_days is None
    When I compute news context
    Then the sentiment_score is null

  Scenario: earnings within 7 days flips suppress_signal on
    Given a sentiment_summary with mean_sentiment 0.5
    And no news items
    And news-context earnings_days is 5
    When I compute news context
    Then the suppress_signal flag is True
    And the suppress_reason mentions "5d"

  Scenario: earnings at the 7-day threshold still suppresses
    Given a sentiment_summary with mean_sentiment 0.5
    And no news items
    And news-context earnings_days is 7
    When I compute news context
    Then the suppress_signal flag is True

  Scenario: earnings at 8 days does not suppress
    Given a sentiment_summary with mean_sentiment 0.5
    And no news items
    And news-context earnings_days is 8
    When I compute news context
    Then the suppress_signal flag is False
    And the suppress_reason is null

  Scenario: top headlines are surfaced
    Given a sentiment_summary with mean_sentiment 0.5
    And news items with titles "Beat on Q1" and "Analyst raises target" and "Guidance lifted" and "Should not appear"
    And news-context earnings_days is None
    When I compute news context
    Then the key_headlines list has 3 entries
    And the first key_headline is "Beat on Q1"

  Scenario: negative days_until (post-earnings) does NOT suppress
    Given a sentiment_summary with mean_sentiment 0.5
    And no news items
    And news-context earnings_days is -2
    When I compute news context
    Then the suppress_signal flag is False
