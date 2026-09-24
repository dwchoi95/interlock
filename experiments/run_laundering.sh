#!/usr/bin/env bash
# The laundering attack (experiments/laundering_attacks.py): one injection task per
# suite whose destination equals an identifier an outside party spelled. Attack phase
# only - benign utility does not depend on the injection task. Four arms:
#   nodef    the undefended agent (agentdojo CLI, module loaded with -ml)
#   progent  Progent, its vendored benchmark loading the same module
#   gate     Interlock gate+strict with the v5 labels (the paper's recommended configuration)
#   named    the same plus --named-strict: an outside party's identifier needs the user's request to vouch for it
#   [SUFFIX=-r2] ./experiments/run_laundering.sh [arms...]      (default: nodef progent gate named; SUFFIX names a repeat)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }
PY="$ROOT/baselines/agentdojo/.venv/bin/python"
EFFECTS="$ROOT/experiments/effects/agentdojo.v5.json"
ARMS=("$@"); [ ${#ARMS[@]} -eq 0 ] && ARMS=(nodef progent gate named)
task_for() { case "$1" in workspace) echo injection_task_14;; slack) echo injection_task_6;; banking) echo injection_task_9;; esac; }
for arm in "${ARMS[@]}"; do
  LOGDIR="$ROOT/results/launder-$arm${SUFFIX:-}"; mkdir -p "$LOGDIR"
  echo "### arm: $arm"
  for suite in workspace slack banking; do
    it="$(task_for "$suite")"
    case "$arm" in
      nodef)
        (cd "$ROOT/baselines/agentdojo" && PYTHONPATH="$ROOT/experiments" .venv/bin/python -m agentdojo.scripts.benchmark \
          --model GPT_4O_2024_05_13 --benchmark-version v1.2 --logdir "$LOGDIR" --suite "$suite" \
          --attack important_instructions -ml laundering_attacks -it "$it" \
          > "$LOGDIR/$suite.attack.log" 2>&1; echo "  [$suite/$arm] exit=$?") & ;;
      progent)
        (cd "$ROOT/baselines/Progent/agentdojo" && SECAGENT_SUITE="$suite" SECAGENT_POLICY_MODEL=gpt-4o-2024-05-13 \
          SECAGENT_UPDATE=True SECAGENT_IGNORE_UPDATE_ERROR=True COLUMNS=300 PYTHONPATH="$ROOT/experiments" \
          ../.venv/bin/python -m agentdojo.scripts.benchmark -s "$suite" --model gpt-4o-2024-05-13 --benchmark-version v1.2 \
          --logdir "$LOGDIR" --attack important_instructions -ml laundering_attacks -it "$it" \
          > "$LOGDIR/$suite.attack.log" 2>&1; echo "  [$suite/$arm] exit=$?") & ;;
      gate|named)
        extra=(); [ "$arm" = named ] && extra=(--named-strict)
        (cd "$ROOT/experiments" && "$PY" run_guard.py --suite "$suite" --logdir "$LOGDIR" --effects "$EFFECTS" \
          --attack important_instructions --gate --strict --laundering --injection-tasks "$it" "${extra[@]+"${extra[@]}"}" \
          > "$LOGDIR/$suite.attack.log" 2>&1; echo "  [$suite/$arm] exit=$?") & ;;
    esac
  done
  wait
done
echo "### done"
