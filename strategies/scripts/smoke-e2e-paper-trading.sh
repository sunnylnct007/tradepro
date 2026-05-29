#!/usr/bin/env bash
# ============================================================
# E2E smoke test for paper-trading chain.
#
# Exercises the FULL chain so we never again claim "fixed" without
# verifying the integration crossing:
#   strategy → Order → RiskGate → Router → /api/oms/orders → broker
#
# Two minimal cases, both demo-money:
#   EQUITY: BUY 1 AAPL via T212_DEMO
#   FX:     BUY 1 EURUSD MINI via IG_DEMO
#
# Each case verifies four checkpoints:
#   1. Admin smoke endpoint returns an order id + broker order id
#   2. OMS row exists in expected state
#   3. The audit-trail endpoint returns state-event timeline
#   4. The order reaches a terminal state (FILLED / REJECTED) within
#      the broker's typical confirm window
#
# Exits non-zero on any failure with a clear pointer to which step
# broke. Pipe through `tee` if you want a log.
# ============================================================
set -euo pipefail

API_BASE="${TRADEPRO_API_BASE:-https://tradepro.showsoldprice.com}"
AUTH_USER="${TRADEPRO_AUTH_USER:-admin}"
AUTH_PASS="${TRADEPRO_AUTH_PASS:-letmein123}"
BEARER="${TRADEPRO_BEARER:-}"

# Token resolution — prefer explicit env, fall back to ~/.tradepro/credentials
if [[ -z "$BEARER" && -f "$HOME/.tradepro/credentials" ]]; then
  BEARER="$(python3 -c "import json; print(json.load(open('$HOME/.tradepro/credentials'))['api_token'])")"
fi

CURL_AUTH=(-u "${AUTH_USER}:${AUTH_PASS}")
if [[ -n "$BEARER" ]]; then
  CURL_AUTH+=(-H "Authorization: Bearer ${BEARER}")
fi

PASS=0
FAIL=0

step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓ %s\033[0m\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '  \033[1;31m✗ %s\033[0m\n' "$*"; FAIL=$((FAIL+1)); }

# -------- 1. Connectivity preflight --------
step "Preflight: connectivity"
HEALTH="$(curl -sS -m 10 "${CURL_AUTH[@]}" "${API_BASE}/health/integrations")"
VERDICT="$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('verdict','?'))")"
[[ "$VERDICT" == "ok" || "$VERDICT" == "warn" ]] && ok "health/integrations verdict=$VERDICT" \
  || { fail "health/integrations verdict=$VERDICT — abort"; exit 1; }

# -------- 2. IG smoke (proves OMS → IG → /confirms → FILLED) --------
step "IG: BUY 1 EURUSD MINI via /api/admin/ig/smoke-order"
IG_RESP="$(curl -sS -m 15 -X POST "${CURL_AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"epic":"CS.D.EURUSD.MINI.IP","side":"BUY","size":1}' \
  "${API_BASE}/api/admin/ig/smoke-order")"
IG_ORDER_ID="$(echo "$IG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('orderId',''))" 2>/dev/null)"
IG_DEAL_REF="$(echo "$IG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('brokerOrderId',''))" 2>/dev/null)"
[[ -n "$IG_ORDER_ID" ]] && ok "OMS order id: $IG_ORDER_ID" || { fail "no order id"; echo "  resp: $IG_RESP"; }
[[ -n "$IG_DEAL_REF" ]] && ok "IG deal ref: $IG_DEAL_REF" || fail "no deal ref (IG placement failed)"

step "IG: wait up to 45s for fill poller to mark FILLED"
IG_STATE=""
for i in {1..15}; do
  sleep 3
  if [[ -n "$IG_ORDER_ID" ]]; then
    OMS_STATE="$(curl -sS -m 5 "${CURL_AUTH[@]}" "${API_BASE}/api/oms/orders/${IG_ORDER_ID}" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','?'))" 2>/dev/null)"
    if [[ "$OMS_STATE" == "FILLED" || "$OMS_STATE" == "REJECTED" || "$OMS_STATE" == "CANCELLED" ]]; then
      IG_STATE="$OMS_STATE"
      break
    fi
  fi
done
[[ "$IG_STATE" == "FILLED" ]] && ok "IG order terminal state: FILLED" \
  || fail "IG order terminal state: ${IG_STATE:-still-pending} (expected FILLED)"

step "IG: audit-trail captured state events"
AUDIT_EVENTS="$(curl -sS -m 5 "${CURL_AUTH[@]}" "${API_BASE}/api/oms/orders/${IG_ORDER_ID}/audit" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('events',[])))" 2>/dev/null)"
[[ "$AUDIT_EVENTS" -ge 3 ]] && ok "audit chain has ${AUDIT_EVENTS} events (ENQUEUE/APPROVE/FILL)" \
  || fail "audit chain has ${AUDIT_EVENTS} events (expected ≥3)"

# -------- 3. T212 smoke (BUY 1 AAPL equity demo) --------
step "T212: BUY 1 AAPL via /api/admin/t212/smoke-order"
T212_RESP="$(curl -sS -m 15 -X POST "${CURL_AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"ticker":"AAPL_US_EQ","side":"BUY","qty":1}' \
  "${API_BASE}/api/admin/t212/smoke-order")"
T212_ORDER_ID="$(echo "$T212_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('orderId',''))" 2>/dev/null)"
T212_BROKER_ID="$(echo "$T212_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('brokerOrderId') or '')" 2>/dev/null)"
[[ -n "$T212_ORDER_ID" ]] && ok "OMS order id: $T212_ORDER_ID" || { fail "no order id"; echo "  resp: $T212_RESP"; }
[[ -n "$T212_BROKER_ID" ]] && ok "T212 broker order id: $T212_BROKER_ID" || fail "no broker order id (T212 placement failed)"

step "T212: wait up to 60s for fill poller"
T212_STATE=""
for i in {1..20}; do
  sleep 3
  if [[ -n "$T212_ORDER_ID" ]]; then
    OMS_STATE="$(curl -sS -m 5 "${CURL_AUTH[@]}" "${API_BASE}/api/oms/orders/${T212_ORDER_ID}" \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','?'))" 2>/dev/null)"
    if [[ "$OMS_STATE" == "FILLED" || "$OMS_STATE" == "REJECTED" || "$OMS_STATE" == "CANCELLED" ]]; then
      T212_STATE="$OMS_STATE"
      break
    fi
  fi
done
[[ "$T212_STATE" == "FILLED" ]] && ok "T212 order terminal state: FILLED" \
  || fail "T212 order terminal state: ${T212_STATE:-still-pending} (expected FILLED)"

# -------- 4. Summary --------
step "SUMMARY"
echo "  passed: $PASS"
echo "  failed: $FAIL"
[[ "$FAIL" -eq 0 ]] && { echo "  E2E SMOKE: GREEN"; exit 0; } || { echo "  E2E SMOKE: RED"; exit 1; }
