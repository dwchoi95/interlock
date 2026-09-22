"""Run the effect-typed guard on one AgentDojo suite, benign or under attack.

Built as its own runner (like CaMeL's main.py) because agentdojo's --defense is a
closed click.Choice; the vendored benchmark is not modified. Each rule can be
switched off for ablation, and the pipeline name carries which are on so the
logs of every variant land in separate directories.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import openai
from agentdojo import attacks, benchmark, logging
from agentdojo.agent_pipeline import AgentPipeline, InitQuery, OpenAILLM, SystemMessage, ToolsExecutionLoop
from agentdojo.agent_pipeline.agent_pipeline import load_system_message
from agentdojo.task_suite import get_suite

from guard import Effects, GuardedToolsExecutor, WriteAllowList


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--suite", required=True)
    p.add_argument("--attack", default=None)
    p.add_argument("--model", default="gpt-4o-2024-05-13")
    p.add_argument("--logdir", required=True)
    p.add_argument("--effects", required=True)
    p.add_argument("--no-allowlist", action="store_true", help="ablation: skip the WRITE allow-list")
    p.add_argument("--no-taint", action="store_true", help="ablation: skip the destination taint check")
    p.add_argument("--user-tasks", nargs="*", default=None)
    a = p.parse_args()

    effects = Effects(a.effects, a.suite)
    client = openai.OpenAI()
    llm = OpenAILLM(client, a.model, None)
    elements = [SystemMessage(load_system_message(None)), InitQuery()]
    if not a.no_allowlist:
        elements.append(WriteAllowList(effects, client, a.model))
    elements += [llm, ToolsExecutionLoop([GuardedToolsExecutor(effects, taint=not a.no_taint), llm])]
    pipeline = AgentPipeline(elements)
    pipeline.name = f"{a.model}-guard" + ("" if a.no_allowlist else "-A") + ("" if a.no_taint else "-T")

    suite = get_suite("v1.2", a.suite)
    logdir = Path(a.logdir)
    with logging.OutputLogger(str(logdir)):
        if a.attack:
            attack = attacks.load_attack(a.attack, suite, pipeline)
            r = benchmark.benchmark_suite_with_injections(pipeline, suite, attack, logdir, force_rerun=False,
                                                          user_tasks=a.user_tasks)
        else:
            r = benchmark.benchmark_suite_without_injections(pipeline, suite, logdir, force_rerun=False,
                                                             user_tasks=a.user_tasks)
    u = r["utility_results"]
    print(f"{a.suite} - utility: {sum(u.values()) / len(u):.4f}")
    if a.attack:
        s = r["security_results"]
        print(f"{a.suite} - security: {sum(s.values()) / len(s):.4f}")


if __name__ == "__main__":
    main()
