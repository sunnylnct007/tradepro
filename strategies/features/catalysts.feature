Feature: Catalyst extraction — dated events surfaced from headlines
  Pin the catalyst extractor's behaviour so future tweaks can't
  silently regress the load-bearing patterns. Phase 17.2 of the
  catalyst sprint (DATA_ROADMAP §13.5). The Ecopetrol (EC) trade
  on 2026-05-21 is the motivating example: pure-technical signals
  said WAIT, but Colombia election + oil surge made it BUY.

  # ---- Election catalyst (the EC case) ----
  Scenario: Colombia election headline 10 days out → election catalyst with dated occurs_on
    Given a headline "Colombia heads to runoff election in 10 days" surfaced on 2026-05-21
    When I extract catalysts
    Then exactly 1 catalyst is returned
    And catalyst 0 has kind "election"
    And catalyst 0 has occurs_on "2026-05-31"
    And catalyst 0 has confidence at least 0.6

  # ---- Earnings with explicit date ----
  Scenario: Earnings headline with "May 28" → earnings catalyst with full confidence
    Given a headline "Apple to report Q3 earnings on May 28" surfaced on 2026-05-20
    When I extract catalysts
    Then exactly 1 catalyst is returned
    And catalyst 0 has kind "earnings"
    And catalyst 0 has occurs_on "2026-05-28"
    And catalyst 0 has confidence at least 0.9

  # ---- Central bank / FOMC ----
  Scenario: FOMC meeting headline → central_bank catalyst
    Given a headline "FOMC meeting next week — rate decision in focus" surfaced on 2026-05-19
    When I extract catalysts
    Then exactly 1 catalyst is returned
    And catalyst 0 has kind "central_bank"

  # ---- Commodity price move ----
  Scenario: Oil price surge → commodity catalyst
    Given a headline "Oil price surge as OPEC+ holds output" surfaced on 2026-05-21
    When I extract catalysts
    Then exactly 1 catalyst is returned
    And catalyst 0 has kind "commodity"

  # ---- Regulatory (FDA) ----
  Scenario: FDA approval headline → regulatory catalyst with relative date
    Given a headline "FDA approval expected for new drug in 30 days" surfaced on 2026-05-18
    When I extract catalysts
    Then exactly 1 catalyst is returned
    And catalyst 0 has kind "regulatory"
    And catalyst 0 has occurs_on "2026-06-17"

  # ---- Negative case: stale word matches shouldn't fire ----
  Scenario: Headline with no catalyst keyword returns nothing
    Given a headline "Stock price rises on broad market gains" surfaced on 2026-05-21
    When I extract catalysts
    Then exactly 0 catalysts are returned

  # ---- Dedup: same catalyst surfaced twice keeps only one ----
  Scenario: Same earnings reported twice deduplicates to one catalyst
    Given the headlines:
      | title                                       | published_at         |
      | Tesla Q1 earnings beat estimates            | 2026-04-20T22:00:00Z |
      | Tesla reports Q1 results above consensus    | 2026-04-21T08:00:00Z |
    When I extract catalysts
    Then exactly 1 catalyst is returned
    And catalyst 0 has kind "earnings"

  # ---- Sort: dated catalysts before undated ----
  Scenario: Mixed dated + undated catalysts are sorted with dated first
    Given the headlines:
      | title                                         | published_at         |
      | Apple to report earnings on May 28            | 2026-05-20T08:00:00Z |
      | Oil price surge as OPEC+ holds output         | 2026-05-21T10:00:00Z |
    When I extract catalysts
    Then exactly 2 catalysts are returned
    And catalyst 0 has kind "earnings"
    And catalyst 1 has kind "commodity"
