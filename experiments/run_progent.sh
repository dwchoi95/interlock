#!/usr/bin/env bash
# Progent under the same fixed setting as the other arms: AgentDojo v1.2,
# gpt-4o-2024-05-13 for both the agent and the policy generator (their run.sh
# defaults to gpt-4o-2024-08-06, which we override so every arm shares a model),
# important_instructions, one run per case at temperature 0.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/baselines/Progent/agentdojo"
set -a; . "$ROOT/.env"; set +a
LOGDIR="$ROOT/experiments/runs/progent"; mkdir -p "$LOGDIR"
MODEL="gpt-4o-2024-05-13"
export SECAGENT_POLICY_MODEL="$MODEL"
export SECAGENT_UPDATE="True"              # policy updates on: the "Progent" row of their table
export SECAGENT_IGNORE_UPDATE_ERROR="True"
export COLUMNS=300
run() {
  local suite="$1"; local phase="$2"; shift 2
  SECAGENT_SUITE="$suite" ../.venv/bin/python -m agentdojo.scripts.benchmark \
    -s "$suite" --model "$MODEL" --benchmark-version v1.2 --logdir "$LOGDIR" "$@" \
    > "$LOGDIR/$suite.$phase.log" 2>&1
  echo "  [$suite/$phase] exit=$?"
}
for phase in benign attack; do
  echo "### phase: $phase"
  for suite in workspace slack travel banking; do
    if [ "$phase" = attack ]; then run "$suite" attack --attack important_instructions &
    else run "$suite" benign & fi
  done
  wait
done
echo "### done"
