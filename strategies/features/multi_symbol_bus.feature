Feature: MultiSymbolSourceBackedBus — concurrent fetch + timestamp-ordered replay
  The paper engine needs one bus that handles N symbols so paper sessions
  don't fan out into N subprocesses. The bus calls BarSource.fetch once
  per symbol concurrently and merges the per-symbol bar lists into a
  single timestamp-ordered stream before handing them to the engine.

  Scenario: bus calls the source once per symbol
    Given a stub bar source serving 3 symbols with staggered minute bars
    When I run the multi-symbol bus for those symbols
    Then the source fetch is invoked once per symbol

  Scenario: bus emits bars in timestamp order
    Given a stub bar source serving 3 symbols with staggered minute bars
    When I run the multi-symbol bus for those symbols
    Then every emitted bar's timestamp is greater than or equal to the previous one
    And every requested symbol appears in the emitted stream

  Scenario: bus terminates with a ShutdownEvent
    Given a stub bar source serving 3 symbols with staggered minute bars
    When I run the multi-symbol bus for those symbols
    Then the final queued event is a ShutdownEvent

  Scenario: bus tolerates a source that returns no bars for one symbol
    Given a stub bar source where one of 3 symbols returns no bars
    When I run the multi-symbol bus for those symbols
    Then the bus completes without error
    And only the symbols with bars appear in the emitted stream
