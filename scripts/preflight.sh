#!/usr/bin/env bash
# Refuse to run live board operations in states that are known to destroy work.
#
#   preflight.sh [BOARD.kicad_pcb]
#
# Exit 0 = safe. Exit 1 = abort, with the reason on stderr.
set -uo pipefail
BOARD="${1:-}"
fail() { echo "PREFLIGHT ABORT: $*" >&2; exit 1; }
warn() { echo "PREFLIGHT WARN:  $*" >&2; }

# 1. KiCad 10.0.5 segfaults when a *board* commit is dispatched while eeschema
#    is loaded in the same process. Cost: the whole app, plus unsaved work.
#    See docs/gotchas.md for the backtrace.
PIDS=$(pgrep -x pcbnew 2>/dev/null; pgrep -x kicad 2>/dev/null)
FOUND_BOARD_EDITOR=0
for pid in $PIDS; do
  MAPS="/proc/$pid/maps"
  [ -r "$MAPS" ] || continue
  if grep -q '_eeschema\.kiface' "$MAPS" 2>/dev/null; then
    fail "PID $pid has _eeschema.kiface loaded. A live board commit will SIGSEGV KiCad.
       Close the schematic editor and run the PCB editor standalone:  pcbnew ${BOARD:-board.kicad_pcb}"
  fi
  grep -q '_pcbnew\.kiface' "$MAPS" 2>/dev/null && FOUND_BOARD_EDITOR=1
done

# 2. An open pcbnew holding a stale buffer will overwrite file-based edits on
#    its next save. This has silently destroyed a full routing pass.
if [ -n "$BOARD" ] && [ -f "$BOARD" ] && [ "$FOUND_BOARD_EDITOR" = "1" ]; then
  for pid in $PIDS; do
    STARTED=$(stat -c %Y "/proc/$pid" 2>/dev/null) || continue
    MODIFIED=$(stat -c %Y "$BOARD" 2>/dev/null) || continue
    if [ "$MODIFIED" -gt "$STARTED" ]; then
      warn "$BOARD changed on disk after pcbnew (PID $pid) opened it.
       That window holds a stale buffer - saving from it will discard those changes.
       Close it, or File > Revert, before continuing."
    fi
  done
fi

# 3. Leftover lock files from a crashed KiCad confuse the next open.
if [ -n "$BOARD" ]; then
  DIR=$(dirname "$BOARD")
  if compgen -G "$DIR/~*.lck" > /dev/null 2>&1 && [ -z "$PIDS" ]; then
    warn "stale ~*.lck files in $DIR with no KiCad running - safe to delete"
  fi
fi

echo "preflight ok${BOARD:+ ($BOARD)}"
