#!/usr/bin/env bash
# The effect-typed guard under the fixed setting: AgentDojo v1.2, gpt-4o-2024-05-13,
# important_instructions, one run per case at temperature 0. Four suites run as
# four processes per phase, as run_baseline.sh does.
#   [EFFECTS=experiments/effects/agentdojo.v4.json] ./experiments/run_guard.sh <label> [--no-allowlist] [--no-taint]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a; . "$ROOT/.env"; set +a
LABEL="$1"; shift
LOGDIR="$ROOT/results/$LABEL"; mkdir -p "$LOGDIR"
EFFECTS="${EFFECTS:-$ROOT/experiments/effects/agentdojo.json}"
[ "${EFFECTS#/}" = "$EFFECTS" ] && EFFECTS="$ROOT/$EFFECTS"   # relative paths are relative to the repo
echo "### effects: $EFFECTS"
PY="$ROOT/baselines/agentdojo/.venv/bin/python"
cd "$ROOT/experiments"
run() {  # run <suite> <phase> [extra args...]
  local suite="$1"; local phase="$2"; shift 2
  "$PY" run_guard.py --suite "$suite" --logdir "$LOGDIR" --effects "$EFFECTS" "$@" \
    > "$LOGDIR/$suite.$phase.log" 2>&1
  echo "  [$suite/$phase] exit=$?"
}
for phase in benign attack; do
  echo "### phase: $phase"
  for suite in workspace slack travel banking; do
    if [ "$phase" = attack ]; then run "$suite" attack --attack important_instructions "$@" &
    else run "$suite" benign "$@" & fi
  done
  wait
done
echo "### done"
