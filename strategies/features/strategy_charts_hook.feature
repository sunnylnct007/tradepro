Feature: Strategy.recent_charts + Engine.attach_charts — surface viz on the snapshot

  Strategies opt into per-session charts by overriding the
  ``recent_charts`` hook. The engine calls it at session end and
  attaches the returned ``{name → plotly_figure_json}`` map onto
  ``snapshot.strategies[].charts``. The Session Detail Charts tab
  on the frontend picks these up unchanged.

  Scenario: Base Strategy.recent_charts is an empty dict by default
    Given a base Strategy instance
    When I call recent_charts()
    Then the result is an empty dict

  Scenario: A strategy returning charts has them attached to the snapshot
    Given an engine running a replay session with a fake-chart strategy
    When the session completes
    Then the snapshot's strategy entry has a "charts" key
    And the charts entry contains the figure name the strategy emitted

  Scenario: A strategy whose recent_charts raises does not crash the snapshot
    Given an engine running a replay session with a buggy-chart strategy
    When the session completes
    Then the snapshot's strategy entry has a "charts" key
    And the charts dict is empty
