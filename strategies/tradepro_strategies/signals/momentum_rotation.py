"""The momentum rotation's SIGNALS adapter for swing's execution chassis.

The RULE is not here: it is `momentum_rotation.select()`, shared with the
backtest. This adapter answers the chassis's per-symbol questions (enter? exit?
how good?) from the monthly decision that rule produced, so the chassis's
broker checks and fill handling apply unchanged.
"""
from __future__ import annotations


class RotationSignals:
    """The chassis's SIGNALS interface, answered from the monthly decision.

    The interface passes only (closes, i); the strategy sets the symbol and
    day it is evaluating before delegating to the chassis, and these methods
    read them back from it."""

    MIN_BARS = 30
    MAX_HOLD = 9999          # no time exit: a holding leaves at a rebalance or on the stop
    TAG = "momentum rotation entry"

    def __init__(self, strategy):  # the MomentumRotationStrategy it answers for
        self.s = strategy

    def entry_signal(self, closes, i) -> bool:
        return self.s._wants_entry()

    def exit_decision(self, closes, i, *, fill_price, bars_held):
        return self.s._wants_exit(closes, i)

    def target_price(self, closes, i):
        return None          # trails; no target

    def stop_price(self, price: float) -> float:
        return price * (1 - self.s.TRAIL)

    def reward_risk(self, closes, i) -> float:
        return float((self.s._decision() or {}).get("scores", {}).get(self.s._cur, 0.0))

    def entry_reason(self, closes, i) -> str:
        d = self.s._decision() or {}
        picks = d.get("picks", [])
        rank = picks.index(self.s._cur) + 1 if self.s._cur in picks else None
        return (f"rank {rank} of {len(picks)} on the {d.get('signal_date')} close "
                f"(score {d.get('scores', {}).get(self.s._cur)}); allocation "
                f"{d.get('allocation')} of capital, {d.get('weight_each')} a name, "
                f"portfolio vol {d.get('portfolio_vol')}; {d.get('reason')}")
