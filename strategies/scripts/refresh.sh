#!/usr/bin/env bash
# Daily refresh hook — re-runs tradepro-compare on every ETF universe and
# pushes results to the API. Designed to be invoked by launchd
# (~/Library/LaunchAgents/com.tradepro.refresh.plist) or any other cron-
# style scheduler. Exits 0 only if every push succeeds.
#
# Logs go to:
#   ~/.tradepro/logs/refresh-YYYY-MM-DD.log
#
# Credentials come from ~/.tradepro/credentials (api_base_url + api_token).
# The script does NOT modify the credentials file — make sure it points at
# the API you want pushes to land on (production or local).

set -uo pipefail

# etf_all fans out across ~50 symbols, each doing a Yahoo fetch + a
# news fetch + multiple Ollama HTTP calls. macOS default ulimit -n is
# usually 256, which gets exhausted partway through and the run fails
# with `OSError: [Errno 24] Too many open files`. Bump to 4096 — well
# above worst-case concurrent socket + log-handle count.
ulimit -n 4096 2>/dev/null || true

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/refresh-$(date -u +%Y-%m-%d).log"

# Universes to refresh, in order. Each line: <watchlist> <currency>
# <stamp_duty>. Stamp duty 0 across the board because all of these are
# ETFs (LSE main-market shares pay 0.5%, but the LSE-listed ETFs in
# etf_uk_core are exempt).
read -r -d '' UNIVERSES <<'EOF' || true
etf_all      USD 0
etf_us_core  USD 0
etf_uk_core  GBP 0
etf_us_sector USD 0
etf_factor   USD 0
us_megacap_sample USD 0
us_sp100_sample   USD 0
us_semis          USD 0
us_growth_tech    USD 0
asia_majors       USD 0
europe_majors     USD 0
crypto_majors     USD 0
commodities_broad USD 0
EOF

cd "$PROJECT_DIR" || exit 1

# Resolve uv. launchd starts with a minimal PATH; finding `uv` reliably
# means trying every common install location until one is executable.
# Order: PATH first (covers a custom location the user added), then
# the three most common installers' default paths.
UV=""
for candidate in \
    "$(command -v uv 2>/dev/null)" \
    /opt/homebrew/bin/uv \
    /usr/local/bin/uv \
    /opt/anaconda3/bin/uv \
    "$HOME/.local/bin/uv" \
    "$HOME/.cargo/bin/uv"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    UV="$candidate"
    break
  fi
done
if [[ -z "$UV" ]]; then
  echo "tradepro-refresh: no uv binary found on disk — install via brew or pip" >&2
  exit 127
fi

run_id="$(date -u +%Y%m%dT%H%M%SZ)"
{
  echo "================================================================"
  echo "[$run_id] tradepro-refresh starting"
  echo "[$run_id] cwd: $PROJECT_DIR"
  echo "[$run_id] uv:  $UV"
  echo "================================================================"
} >>"$LOG_FILE"

failures=0
while read -r watchlist currency stamp_duty; do
  [[ -z "$watchlist" ]] && continue
  echo "[$run_id] >>> $watchlist ($currency, stamp_duty=$stamp_duty)" >>"$LOG_FILE"
  if ! "$UV" run tradepro-compare \
      --watchlist "$watchlist" \
      --currency "$currency" \
      --stamp-duty "$stamp_duty" \
      --push >>"$LOG_FILE" 2>&1; then
    echo "[$run_id] FAILED: $watchlist" >>"$LOG_FILE"
    failures=$((failures + 1))
  else
    echo "[$run_id] ok: $watchlist" >>"$LOG_FILE"
  fi
done <<< "$UNIVERSES"

echo "[$run_id] done — $failures failure(s)" >>"$LOG_FILE"

# Final heartbeat so the UI sees the refresh's last_refresh stats land
# without waiting for the next 15-min periodic ping. Best-effort.
"$UV" run tradepro-heartbeat >>"$LOG_FILE" 2>&1 || true

exit "$failures"
