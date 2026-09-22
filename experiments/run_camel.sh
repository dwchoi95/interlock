#!/usr/bin/env bash
# CaMeL under the fixed setting. main.py pins get_suite("v1.2", ...) and the
# important_instructions attack, so only the model is supplied.
#
# CaMeL's artifact is fragile -- its authors warn it may contain bugs, and an
# uncaught exception in the interpreter aborts a whole suite. main.py passes
# force_rerun=False, so a crashed suite resumes where it stopped. We therefore
# run each suite separately and retry it a few times, which loses only the case
# that crashed rather than the run. CaMeL itself is not modified beyond the
# one-line fix recorded in docs/EXTERNAL.md.
#
# The policy-enforcing rows come from replaying a logged base run, so each base
# run is a prerequisite for its replay.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/baselines/CaMeL"
set -a; . "$ROOT/.env"; set +a
MODEL="openai:gpt-4o-2024-05-13"
RETRIES=${RETRIES:-6}
phase() {  # phase <label> [extra args...]
  local label="$1"; shift
  echo "### phase: $label"
  for suite in workspace slack travel banking; do
    for try in $(seq 1 "$RETRIES"); do
      .venv/bin/python main.py "$MODEL" --suites "$suite" "$@" \
        >> "$ROOT/results/camel.$label.log" 2>&1 && { echo "  [$label/$suite] ok (try $try)"; break; }
      echo "  [$label/$suite] crashed, retry $try"
    done
  done
}
phase benign
phase attack        --run-attack
phase benign-policy --replay-with-policies
phase attack-policy --run-attack --replay-with-policies
echo "### done"
