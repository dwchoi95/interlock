"""The AgentDojo arms of the evaluation, each run under the fixed setting: AgentDojo v1.2, gpt-4o-2024-08-06 for
every LLM role, temperature 0, one run per case. --agent-model changes the agent alone (the generality runs of
Section 5.5: open models on Ollama at AGENT_BASE_URL, temperature 0); every defense-side LLM stays on MODEL. A run writes its transcripts and per-suite logs to
results/<run>/raw; the four suites of a phase run as four processes (AgentDojo's --max-workers path is broken).

  no-defense, tool-filter   the AgentDojo checkout's own CLI
  progent                   Progent ported to AgentDojo 0.1.35 (baselines/Progent/agentdojo-0135), policy model MODEL
                            and the agent's model, updates on
  progent-off               the same port with Progent switched off: no policy, no update,
                            unwrapped tools; isolates the harness from the defense
  camel                     CaMeL's main.py; see run_camel()
  interlock                 src.harness.interlock (Narrow + Guard) with the labels of --effects

Only the standard library is imported here, so run.py can build and launch the commands from any interpreter.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "baselines"
SUITES = ["workspace", "slack", "travel", "banking"]
MODEL = "gpt-4o-2024-08-06"
# The laundering attack adds one injection task per suite (src/harness/laundering_attacks.py); travel has none.
LAUNDERING = {"workspace": "injection_task_14", "slack": "injection_task_6", "banking": "injection_task_9"}
PROGENT_ENV = {"SECAGENT_POLICY_MODEL": MODEL, "SECAGENT_UPDATE": "True",  # policy updates on: their "Progent" row
               "SECAGENT_IGNORE_UPDATE_ERROR": "True", "COLUMNS": "300"}


def enum_name(model: str) -> str:
    """AgentDojo's ModelsEnum member for a model id: gpt-4o-2024-08-06 -> GPT_4O_2024_08_06, gemma4:31b -> GEMMA4_31B."""
    return model.upper().replace("-", "_").replace(".", "_").replace(":", "_")


def command(arm: str, suite: str, phase: str, logdir: Path, attack: str, laundering: bool, effects: str,
            flags: list[str], agent_model: str = MODEL) -> tuple[list[str], Path, dict]:
    """The command, working directory and extra environment of one suite of one phase."""
    attacked = phase == "attack"
    extra = ["-it", LAUNDERING[suite]] if laundering else []
    if arm in ("no-defense", "tool-filter"):
        cmd = [str(BASE / "agentdojo/.venv/bin/python"), "-m", "agentdojo.scripts.benchmark", "--model", enum_name(agent_model),
               "--benchmark-version", "v1.2", "--logdir", str(logdir), "--suite", suite]
        if arm == "tool-filter":
            cmd += ["--defense", "tool_filter"]
        if attacked:
            cmd += ["--attack", attack]
            module = "laundering_attacks" if laundering else "adaptive_attacks" if attack != "important_instructions" else None
            cmd += ["-ml", f"src.harness.{module}"] if module else []
        return cmd + extra + flags, BASE / "agentdojo", {}
    if arm in ("progent", "progent-off"):
        cmd = [str(BASE / "Progent/.venv/bin/python"), "-m", "agentdojo.scripts.benchmark", "-s", suite, "--model", enum_name(agent_model),
               "--benchmark-version", "v1.2", "--logdir", str(logdir)]
        if attacked:
            cmd += ["--attack", attack] + (["-ml", "src.harness.laundering_attacks"] if laundering else [])
        env = ({"SECAGENT_GENERATE": "False", "SECAGENT_UPDATE": "False", "COLUMNS": "300"} if arm == "progent-off"
               else {**PROGENT_ENV, "SECAGENT_SUITE": suite})
        return cmd + extra + flags, BASE / "Progent/agentdojo-0135", env
    if arm == "interlock":
        cmd = [str(BASE / "agentdojo/.venv/bin/python"), "-m", "src.harness.interlock", "--suite", suite,
               "--logdir", str(logdir), "--effects", str(ROOT / effects),
               "--model", agent_model, "--narrow-model", MODEL]
        if attacked:
            cmd += ["--attack", attack] + (["--laundering", "--injection-tasks", LAUNDERING[suite]] if laundering else [])
        return cmd + flags, ROOT, {}
    raise ValueError(f"unknown arm {arm}")


def launch(cmd: list[str], cwd: Path, env: dict, log: Path, dry: bool, append: bool = False) -> subprocess.Popen | None:
    print(f"  {' '.join(f'{k}={v}' for k, v in env.items())}{' ' if env else ''}(cd {cwd.relative_to(ROOT)}) {' '.join(cmd)}"
          f" {'>>' if append else '>'} {log.relative_to(ROOT)}", flush=True)
    if dry:
        return None
    return subprocess.Popen(cmd, cwd=cwd, env={**os.environ, "PYTHONPATH": str(ROOT), **env},
                            stdout=open(log, "a" if append else "w"), stderr=subprocess.STDOUT)


def run(arm: str, run_dir: str, phases: list[str], attack: str, laundering: bool, effects: str, flags: list[str],
        dry: bool = False, agent_model: str = MODEL) -> None:
    logdir = ROOT / "results" / run_dir / "raw"
    if not dry:
        logdir.mkdir(parents=True, exist_ok=True)
    suites = list(LAUNDERING) if laundering else SUITES
    for phase in phases:
        print(f"### {arm} {run_dir}: {phase}", flush=True)
        procs = [(s, launch(*command(arm, s, phase, logdir, attack, laundering, effects, flags, agent_model), logdir / f"{s}.{phase}.log", dry))
                 for s in suites]
        for s, p in procs:
            if p:
                print(f"  [{s}/{phase}] exit={p.wait()}", flush=True)


def run_camel(rep: str, retries: int = 6, dry: bool = False) -> None:
    """CaMeL: without policies to results/enforcement/main/camel-interpreter/<rep>, with them to .../camel/<rep>.

    main.py pins get_suite("v1.2", ...) and important_instructions, so only the model is supplied. Its artifact is
    fragile (an uncaught interpreter exception aborts a whole suite) and main.py resumes with force_rerun=False, so each
    suite runs alone and is retried, which loses only the case that crashed. The policy rows replay the logged base
    run, so the base phases come first; main.py writes to baselines/CaMeL/logs, and each pipeline moves to its run
    directory once all four phases are done."""
    base = ROOT / f"results/enforcement/main/camel-interpreter/{rep}/raw"
    pol = ROOT / f"results/enforcement/main/camel/{rep}/raw"
    phases = [("benign", base, []), ("attack", base, ["--run-attack"]),
              ("benign-policy", pol, ["--replay-with-policies"]), ("attack-policy", pol, ["--run-attack", "--replay-with-policies"])]
    for label, d, args in phases:
        print(f"### camel {rep}: {label}", flush=True)
        if not dry:
            d.mkdir(parents=True, exist_ok=True)
        for suite in SUITES:
            cmd = [str(BASE / "CaMeL/.venv/bin/python"), "main.py", f"openai:{MODEL}", "--suites", suite, *args]
            for attempt in range(1, retries + 1):
                p = launch(cmd, BASE / "CaMeL", {}, d / f"camel.{label}.log", dry, append=True)
                if p is None or p.wait() == 0:
                    print(f"  [{label}/{suite}] ok (try {attempt})", flush=True)
                    break
                print(f"  [{label}/{suite}] crashed, retry {attempt}", flush=True)
    for pipe, d in ((f"{MODEL}+camel", base), (f"{MODEL}+camel+secpol", pol)):
        print(f"  mv baselines/CaMeL/logs/{pipe} {d.relative_to(ROOT)}/", flush=True)
        if not dry:
            (BASE / "CaMeL/logs" / pipe).rename(d / pipe)
