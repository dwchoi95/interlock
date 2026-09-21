#!/usr/bin/env bash
# Fixed setting: AgentDojo v1.2.2, gpt-4o-2024-05-13, important_instructions,
# one run per case at temperature 0. The upstream --max-workers path is broken
# (it passes suite names positionally into a signature expecting suite objects),
# so the four suites are run as four processes instead.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/benchmark/agentdojo"
set -a; . "$ROOT/.env"; set +a
LABEL="$1"; shift
LOGDIR="$ROOT/experiments/runs/$LABEL"
mkdir -p "$LOGDIR"
run() {  # run <suite> <phase> [extra args...]
  local suite="$1"; local phase="$2"; shift 2
  .venv/bin/python -m agentdojo.scripts.benchmark \
    --model GPT_4O_2024_05_13 --benchmark-version v1.2.2 \
    --logdir "$LOGDIR" --suite "$suite" "$@" \
    > "$LOGDIR/$suite.$phase.log" 2>&1
  echo "  [$suite/$phase] exit=$?"
}
for phase in benign attack; do
  echo "### phase: $phase"
  for suite in workspace slack travel banking; do
    if [ "$phase" = attack ]; then
      run "$suite" attack --attack important_instructions "$@" &
    else
      run "$suite" benign "$@" &
    fi
  done
  wait
done
echo "### done"
