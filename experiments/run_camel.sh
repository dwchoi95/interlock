#!/usr/bin/env bash
# CaMeL under the fixed setting. main.py already pins get_suite("v1.2", ...) and
# the important_instructions attack, so only the model is supplied.
#
# Four phases. --replay-with-policies replays an already-logged run with the
# security policies enforced, so each base run is a prerequisite for its replay.
# The policy-enforcing rows are the ones CaMeL reports; the base runs correspond
# to their "CaMeL (no policies)" ablation. main.py passes force_rerun=False, so
# re-invoking this script skips cases that are already logged.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/baselines/CaMeL"
set -a; . "$ROOT/.env"; set +a
MODEL="openai:gpt-4o-2024-05-13"
phase() {  # phase <label> [args...]
  local label="$1"; shift
  echo "### phase: $label"
  .venv/bin/python main.py "$MODEL" "$@" > "$ROOT/results/camel.$label.log" 2>&1
  echo "  $label exit=$?"
}
phase benign
phase attack        --run-attack
phase benign-policy --replay-with-policies
phase attack-policy --run-attack --replay-with-policies
echo "### done"
