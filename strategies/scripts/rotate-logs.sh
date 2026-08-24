#!/usr/bin/env bash
# Keep the daemon logs readable.
#
# The equity log reached 2.4 GB and the FX log 224 MB. Neither rotated, and it
# mattered: on 24 Aug, diagnosing the ICH churn meant grepping a 2.4 GB file,
# and reading its tail was the only practical option. A log you cannot open is
# not a log.
#
# Deliberately simple — truncate in place, keeping the tail. NOT logrotate:
# these files are held open by a running daemon, so unlinking them leaves the
# daemon writing to a deleted inode and the new file stays empty. Truncating
# keeps the same inode and the daemon writes on.
set -uo pipefail
KEEP_MB="${TRADEPRO_LOG_KEEP_MB:-20}"
KEEP=$(( KEEP_MB * 1024 * 1024 ))
rotated=0
for f in /tmp/tradepro-*.log "$HOME"/.tradepro/logs/*.log "$HOME"/.tradepro/logs/*.out "$HOME"/.tradepro/logs/*.err; do
  [[ -f "$f" ]] || continue
  size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
  (( size > KEEP )) || continue
  tail -c "$KEEP" "$f" > "$f.rot" 2>/dev/null || continue
  cat "$f.rot" > "$f"          # truncate in place; keeps the inode
  rm -f "$f.rot"
  printf '  rotated %-52s %5.0f MB -> %s MB\n' "$(basename "$f")" \
         "$(echo "$size/1048576" | bc -l)" "$KEEP_MB"
  rotated=$((rotated+1))
done
echo "log rotation: $rotated file(s) trimmed to ${KEEP_MB}MB"
