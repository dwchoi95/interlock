#!/usr/bin/env bash
# Benign phase only, for measuring run-to-run variance of benign utility: GPT-4o at
# temperature 0 is not deterministic, and two runs of an identical guard configuration
# differed by six points of BU on 97 tasks. Four suites run as four processes.
#   ./experiments/run_benign.sh <label> nodef
#   ./experiments/run_benign.sh <label> toolfilter
#   ./experiments/run_benign.sh <label> progent          (their vendored agentdojo, gpt-4o-2024-05-13 overridden as in run_progent.sh)
#   EFFECTS=... ./experiments/run_benign.sh <label> guard [--no-allowlist] [--no-taint] [--strict]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a; . "$ROOT/.env"; set +a
LABEL="$1"; KIND="$2"; shift 2
LOGDIR="$ROOT/results/$LABEL"; mkdir -p "$LOGDIR"
PY="$ROOT/baselines/agentdojo/.venv/bin/python"
EFFECTS="${EFFECTS:-$ROOT/experiments/effects/agentdojo.v4.json}"
[ "${EFFECTS#/}" = "$EFFECTS" ] && EFFECTS="$ROOT/$EFFECTS"
echo "### benign-only run: $LABEL ($KIND)"
for suite in workspace slack travel banking; do
  if [ "$KIND" = guard ]; then
    (cd "$ROOT/experiments" && "$PY" run_guard.py --suite "$suite" --logdir "$LOGDIR" --effects "$EFFECTS" "$@" \
      > "$LOGDIR/$suite.benign.log" 2>&1; echo "  [$suite] exit=$?") &
  elif [ "$KIND" = toolfilter ]; then
    (cd "$ROOT/baselines/agentdojo" && "$PY" -m agentdojo.scripts.benchmark --model GPT_4O_2024_05_13 \
      --benchmark-version v1.2 --logdir "$LOGDIR" --suite "$suite" --defense tool_filter "$@" \
      > "$LOGDIR/$suite.benign.log" 2>&1; echo "  [$suite] exit=$?") &
  elif [ "$KIND" = progent ]; then
    (cd "$ROOT/baselines/Progent/agentdojo" && SECAGENT_SUITE="$suite" SECAGENT_POLICY_MODEL=gpt-4o-2024-05-13 \
      SECAGENT_UPDATE=True SECAGENT_IGNORE_UPDATE_ERROR=True COLUMNS=300 ../.venv/bin/python -m agentdojo.scripts.benchmark \
      -s "$suite" --model gpt-4o-2024-05-13 --benchmark-version v1.2 --logdir "$LOGDIR" "$@" \
      > "$LOGDIR/$suite.benign.log" 2>&1; echo "  [$suite] exit=$?") &
  else
    (cd "$ROOT/baselines/agentdojo" && "$PY" -m agentdojo.scripts.benchmark --model GPT_4O_2024_05_13 \
      --benchmark-version v1.2 --logdir "$LOGDIR" --suite "$suite" "$@" \
      > "$LOGDIR/$suite.benign.log" 2>&1; echo "  [$suite] exit=$?") &
  fi
done
wait
echo "### done"
