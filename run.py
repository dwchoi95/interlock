#!/usr/bin/env python3
"""Single entry point: every experiment, analysis and check of the repository runs through here.

Each command runs in the interpreter it needs (the project's uv environment for Summarize, the AgentDojo, Progent
or CaMeL checkout's venv for the AgentDojo work), from the repository root, with .env loaded. Results go to results/.

  python run.py summarize profile npm:chrome-devtools-mcp [--version V] [--out DIR]    Summarize (src/summarize/cli.py)
  python run.py summarize batch <package list> | --resume BATCH_ID
  python run.py agentdojo <arm> <run> [options] [-- extra args]                       an AgentDojo run (src/harness/arms.py)
      arm: no-defense | tool-filter | progent | interlock | camel
      run: its directory under results/, e.g. enforcement/main/interlock-gate/r4 (for camel, the repeat name: r2)
  python run.py realserver --check | [chrome|browser|grafana|all] [config] [scenario]   Section 5.3 (src/harness/realserver.py)
  python run.py metrics aggregate <run ...> | export [run ...] | paired <A runs> <B runs>
  python run.py analyze <analysis> [args]                                                src/analysis/<analysis>.py
  python run.py benchmark registry --refresh | collect [server ...] | report            benchmark/mcpwild/
  python run.py test                                                                     unit tests and the guard check

Examples:
  python run.py agentdojo interlock enforcement/main/interlock-gate-strict/r2 --gate --strict
  python run.py agentdojo no-defense enforcement/obfuscation/no-defense/r2 --attack important_instructions_obfuscated
  python run.py agentdojo interlock enforcement/laundering/interlock-gate-strict/r4 --laundering --gate --strict \\
      --effects benchmark/agentdojo/labels/agentdojo.v5.json
  python run.py agentdojo progent enforcement/main/progent/b5 --phases benign
  python run.py metrics paired enforcement/main/progent/r1,enforcement/main/progent/r2 \\
      enforcement/main/interlock-gate/r1,enforcement/main/interlock-gate/r2
  python run.py analyze offline-eval benchmark/agentdojo/labels/agentdojo.v4.json
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENTDOJO_PY = ROOT / "baselines/agentdojo/.venv/bin/python"
ANALYSES = sorted(p.stem.replace("_", "-") for p in (ROOT / "src/analysis").glob("*.py") if p.stem != "__init__")


def load_env() -> None:
    """KEY=VALUE lines of .env, which is never committed; variables already set win."""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip().removeprefix("export "), v.strip().strip("'\""))


def call(cmd: list, cwd: Path = ROOT) -> int:
    return subprocess.call([str(c) for c in cmd], cwd=cwd, env={**os.environ, "PYTHONPATH": str(ROOT)})


def agentdojo(argv: list[str]) -> int:
    from src.harness import arms
    p = argparse.ArgumentParser(prog="run.py agentdojo")
    p.add_argument("arm", choices=["no-defense", "tool-filter", "progent", "progent-off", "interlock", "camel"])
    p.add_argument("run")
    p.add_argument("--phases", default=None, help="benign,attack (default); attack alone for --laundering")
    p.add_argument("--attack", default="important_instructions")
    p.add_argument("--laundering", action="store_true", help="the three laundering injection tasks (attack phase)")
    p.add_argument("--effects", default="experiments/summarize/agentdojo/labels/agentdojo.summarize-gpt-4o-2024-08-06-v67.json",
                   help="interlock: label file (default: the summaries Summarize v6.7 made from AgentDojo's code, used in Section 5)")
    p.add_argument("--agent-model", default=None,
                   help="the agent's LLM for the generality runs; defense-side LLMs stay on arms.MODEL")
    for flag in ("--gate", "--strict", "--named-strict", "--delegated", "--no-delegated-read", "--no-allowlist", "--no-taint"):
        p.add_argument(flag, action="store_true", help="interlock: see src/harness/interlock.py")
    p.add_argument("--retries", type=int, default=6, help="camel: attempts per suite")
    p.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    own, extra = (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, [])
    a = p.parse_args(own)
    if a.agent_model and a.arm in ("camel", "tool-filter"):
        p.error("--agent-model: the generality runs leave out camel and tool-filter")
    if a.arm == "camel":
        arms.run_camel(a.run, a.retries, a.dry_run)
        return 0
    flags = [f"--{n.replace('_', '-')}" for n in ("gate", "strict", "named_strict", "delegated", "no_delegated_read", "no_allowlist", "no_taint")
             if getattr(a, n)]
    if flags and a.arm != "interlock":
        p.error(f"{' '.join(flags)} apply to interlock only")
    phases = (a.phases or ("attack" if a.laundering else "benign,attack")).split(",")
    arms.run(a.arm, a.run, phases, a.attack, a.laundering, a.effects, flags + extra, a.dry_run, a.agent_model or arms.MODEL)
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__ + "\nanalyses: " + ", ".join(ANALYSES))
        return 0
    load_env()
    cmd, rest = argv[0], argv[1:]
    if cmd == "summarize":
        return call(["uv", "run", "python", "-m", "src.summarize.cli", *rest])
    if cmd == "agentdojo":
        return agentdojo(rest)
    if cmd == "realserver":
        return call([AGENTDOJO_PY, "-m", "src.harness.realserver", *rest])
    if cmd == "metrics":
        return call([sys.executable, "-m", "src.metrics", *rest])
    if cmd == "analyze":
        if not rest or rest[0] not in ANALYSES:
            sys.exit("analyses: " + ", ".join(ANALYSES))
        return call([AGENTDOJO_PY, "-m", f"src.analysis.{rest[0].replace('-', '_')}", *rest[1:]])
    if cmd == "benchmark":
        scripts = {"registry": "registry.py", "collect": "collect.py", "report": "report.py"}
        if not rest or rest[0] not in scripts:
            sys.exit("benchmark: " + " | ".join(scripts))
        return call([sys.executable, scripts[rest[0]], *rest[1:]], cwd=ROOT / "benchmark/mcpwild")
    if cmd == "test":
        # The Summarize tests run in the project env; the real-server test and the guard check import agentdojo, so they
        # run with the AgentDojo interpreter.
        return (call(["uv", "run", "--with", "pytest", "pytest", "-q", "tests", "--ignore=tests/test_realserver_configs.py"])
                or call([AGENTDOJO_PY, "-m", "pytest", "-q", "tests/test_realserver_configs.py"])
                or call([AGENTDOJO_PY, "tests/guard_check.py"]))
    sys.exit(f"unknown command {cmd!r}; python run.py --help")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
