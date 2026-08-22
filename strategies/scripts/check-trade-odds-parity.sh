#!/usr/bin/env bash
# Parity check: the barrier maths exists TWICE — Python (the reference, used by
# studies) and TypeScript (runs in the browser, because the API is C# and a
# Python service just for this would be a lot of moving parts for a loop over
# bars). Two implementations of the same maths WILL drift unless something
# checks. This is that something.
#
# Runs both over an identical deterministic fixture and compares every field.
# Run it after touching either implementation.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PY="$ROOT/strategies/.venv/bin/python"
ESB="$ROOT/frontend/node_modules/.bin/esbuild"
[[ -x "$PY"  ]] || { echo "no venv python at $PY"; exit 1; }
[[ -x "$ESB" ]] || { echo "no esbuild — run npm install in frontend/"; exit 1; }

"$PY" - > "$TMP/ref.json" <<'PYEOF'
import json, math, sys, os
sys.path.insert(0, os.path.join(os.getcwd(), "strategies"))
from tradepro_strategies.cli.trade_odds import barrier_scan, sweep_targets, excursion_table
c = [100.0]
for i in range(1, 900):
    step = math.sin(i/7.3)*1.8 + math.cos(i/3.1)*1.1 + (2.5 if i % 97 == 0 else 0) - (3.0 if i % 131 == 0 else 0)
    c.append(max(5.0, c[-1]*(1+step/100)))
h = [x*(1+abs(math.sin(i/5.0))*0.012) for i, x in enumerate(c)]
l = [x*(1-abs(math.cos(i/4.0))*0.012) for i, x in enumerate(c)]
d = [f"2020-01-{i%28+1:02d}" for i in range(len(c))]
o = dict(limit_pct=-0.048, stop_pct=-0.08, fill_window=10, trade_window=20)
print(json.dumps({
    "bars": [{"ts": d[i], "open": c[i], "high": h[i], "low": l[i], "close": c[i]} for i in range(len(c))],
    "scan": barrier_scan(c, h, l, d, target_pct=0.0326, **o),
    "sweep": sweep_targets(c, h, l, d, **o),
    "exc": excursion_table(c, h, l, window=20)}))
PYEOF

cat > "$TMP/check.mjs" <<JSEOF
import { barrierScan, sweepTargets, excursion } from "$ROOT/frontend/src/lib/tradeOdds.ts";
import fs from "fs";
const ref = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const o = { limitPct: -0.048, stopPct: -0.08, fillWindow: 10, tradeWindow: 20 };
console.log(JSON.stringify({
  scan: barrierScan(ref.bars, { ...o, targetPct: 0.0326 }),
  sweep: sweepTargets(ref.bars, o),
  exc: excursion(ref.bars, 20) }));
JSEOF

"$ESB" "$TMP/check.mjs" --bundle --platform=node --format=esm --outfile="$TMP/check.js" --log-level=error || exit 1
node "$TMP/check.js" "$TMP/ref.json" > "$TMP/ts.json" || exit 1

TMP="$TMP" "$PY" - <<'PYEOF'
import json, os, sys
T = os.environ["TMP"]
p = json.load(open(f"{T}/ref.json")); t = json.load(open(f"{T}/ts.json"))
ok = True
def cmp(name, a, b, tol=1e-6):
    global ok
    if a is None and b is None: return
    if a is None or b is None or abs(a-b) > tol:
        ok = False; print(f"  MISMATCH {name}: python={a} ts={b}")
ps, ts_ = p["scan"], t["scan"]
for kp, kt in [("attempts","attempts"),("filled","filled"),("won","won"),("lost","lost"),
               ("p_fill","pFill"),("p_target_given_fill","pTargetGivenFill"),
               ("p_both","pBoth"),("mean_outcome_pct","meanOutcomePct")]:
    cmp(kp, ps[kp], ts_[kt])
for a, b in zip(p["sweep"], t["sweep"]):
    cmp(f"sweep+{a['target_pct']}%p", a["p_target_given_fill"], b["pTargetGivenFill"])
    cmp(f"sweep+{a['target_pct']}%E", a["expectancy_pct"], b["expectancyPct"])
for k in ("median","p75","p90"):
    cmp(f"exc.{k}", p["exc"][k], t["exc"][k])
print(("PASS — Python and TypeScript agree on every field" if ok else "FAIL — implementations have DRIFTED")
      + f"   (n={ps['attempts']} attempts, {ps['filled']} fills, {ps['won']} wins)")
sys.exit(0 if ok else 1)
PYEOF
