#!/usr/bin/env bash
# Long-running TradePro worker — a persistent process the OS keeps
# alive (via launchd KeepAlive: true) so the Mac stays "online" to
# the API between scheduled compare runs. Replaces the cron-style
# pattern of fire-once-and-exit.
#
# Loop body:
#   1. Send a heartbeat (worker is alive).
#   2. Run the daily compare across every ETF universe.
#   3. Send a final heartbeat (run finished).
#   4. Sleep for $WORKER_INTERVAL_SECONDS (default 30 min).
#
# Heartbeats fire every $HEARTBEAT_INTERVAL_SECONDS (default 5 min)
# in a background subshell so the UI shows "alive" even mid-sleep.
#
# Logs:
#   ~/.tradepro/logs/worker-YYYY-MM-DD.log
#
# Triggered by:
#   ~/Library/LaunchAgents/com.tradepro.worker.plist (KeepAlive=true)
#
# To stop:
#   launchctl bootout gui/$UID ~/Library/LaunchAgents/com.tradepro.worker.plist
# To temporarily pause without uninstall:
#   touch ~/.tradepro/worker.pause
# To resume:
#   rm ~/.tradepro/worker.pause

set -uo pipefail

PROJECT_DIR="${TRADEPRO_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="$HOME/.tradepro/logs"
PAUSE_FILE="$HOME/.tradepro/worker.pause"
WORKER_INTERVAL_SECONDS="${WORKER_INTERVAL_SECONDS:-1800}"   # 30 min between full refresh cycles
HEARTBEAT_INTERVAL_SECONDS="${HEARTBEAT_INTERVAL_SECONDS:-300}"  # 5 min liveness ping

mkdir -p "$LOG_DIR"

# Resolve uv robustly — launchd supplies a minimal PATH. This script was the
# only one in scripts/ still guessing: it fell through to /opt/homebrew/bin/uv
# without checking the file exists, and uv actually lives in /opt/anaconda3/bin
# here. Every heartbeat since at least 17 Aug died on "No such file or
# directory" — 250 of them on 22 Aug alone — and nobody saw it because the call
# site ends in `|| true`. Same candidate search as bar-cache-harvest.sh.
export PATH="/opt/homebrew/bin:/opt/anaconda3/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [[ ! -x "$UV" ]]; then
  for cand in /opt/anaconda3/bin/uv /opt/homebrew/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv; do
    if [[ -x "$cand" ]]; then UV="$cand"; break; fi
  done
fi
if [[ ! -x "$UV" ]]; then
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] FATAL: uv not found on PATH or known locations" \
    >>"$LOG_DIR/worker-$(date -u +%Y-%m-%d).log"
  exit 1
fi

log() {
  local ts="[$(date -u '+%Y-%m-%dT%H:%M:%SZ')]"
  echo "$ts $*" >>"$LOG_DIR/worker-$(date -u +%Y-%m-%d).log"
}

# Background heartbeat loop — keeps the UI showing "alive" without
# waiting for the next scheduled refresh fire. Inherits SIGTERM
# from the parent so launchd's bootout cleanly kills both.
heartbeat_loop() {
  while true; do
    if [[ -f "$PAUSE_FILE" ]]; then
      sleep "$HEARTBEAT_INTERVAL_SECONDS"
      continue
    fi
    # `|| true` keeps the loop alive across a transient failure, but on its own
    # it also hid a heartbeat that had not run once in six days. Log the failure
    # to the worker log so a dead heartbeat is visible where someone looks.
    if ! "$UV" run tradepro-heartbeat \
         >>"$LOG_DIR/worker-heartbeat-$(date -u +%Y-%m-%d).log" 2>&1; then
      log "heartbeat FAILED (exit $?) — see worker-heartbeat-$(date -u +%Y-%m-%d).log"
    fi
    sleep "$HEARTBEAT_INTERVAL_SECONDS"
  done
}

# cd BEFORE forking the heartbeat. `uv run` resolves the project from the
# CURRENT working directory, and a forked child keeps the cwd it had AT FORK
# TIME — a later `cd` in the parent never reaches it. launchd starts this from
# `/`, so the heartbeat was one environment change away from silent
# ModuleNotFoundError. The same drift killed the nightly bar harvest on
# 24 Aug 2026; fixing it there and not here would just leave the next one.
cd "$PROJECT_DIR" || exit 1

heartbeat_loop &
HB_PID=$!
trap 'kill $HB_PID 2>/dev/null || true' EXIT
log "worker started (pid=$$, hb_pid=$HB_PID, project=$PROJECT_DIR)"

while true; do
  if [[ -f "$PAUSE_FILE" ]]; then
    log "paused (touch removed: $PAUSE_FILE) — sleeping"
    sleep "$WORKER_INTERVAL_SECONDS"
    continue
  fi

  cycle_start="$(date -u +%s)"
  log "compare-cycle starting"
  if "$PROJECT_DIR/scripts/refresh.sh" >>"$LOG_DIR/worker-$(date -u +%Y-%m-%d).log" 2>&1; then
    log "compare-cycle ok (took $(($(date -u +%s) - cycle_start))s)"
  else
    log "compare-cycle FAILED (took $(($(date -u +%s) - cycle_start))s) — see refresh-$(date -u +%Y-%m-%d).log"
  fi

  log "sleeping ${WORKER_INTERVAL_SECONDS}s before next cycle"
  sleep "$WORKER_INTERVAL_SECONDS"
done
