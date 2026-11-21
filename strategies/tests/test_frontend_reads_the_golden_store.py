"""The production frontend must not pin a candles provider (21 Nov 2026).

`frontend/.env.production` carried `VITE_DEFAULT_PROVIDER=yahoo`, so every
deployed chart read candles from Yahoo while the 11 Nov ruling (ONE golden bar
store, one endpoint) and #386 said the store. Yahoo serves 1m only 7 days back
and 5m only 60, so the rail chart failed on any intraday range beyond 1D. A
grep test is the right shape here: the fault IS a line in a config file.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_production_env_does_not_pin_a_candles_provider():
    text = (ROOT / "frontend" / ".env.production").read_text()
    live = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    pinned = [ln for ln in live if re.match(r"\s*VITE_DEFAULT_PROVIDER\s*=\s*\S", ln)]
    assert not pinned, (
        f"frontend/.env.production pins the candles provider: {pinned} — production charts must "
        "read the golden bar store through the API's default, not Yahoo")
