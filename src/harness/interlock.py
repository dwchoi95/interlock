"""Run Interlock (Narrow + Guard) on one AgentDojo suite, benign or under attack.

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
from agentdojo.agent_pipeline import AgentPipeline, AnthropicLLM, InitQuery, OpenAILLM, SystemMessage, ToolsExecutionLoop
from agentdojo.agent_pipeline.agent_pipeline import get_llm, load_system_message
from agentdojo.models import MODEL_PROVIDERS
from agentdojo.task_suite import get_suite

from src.guard import Effects, GuardedToolsExecutor
from src.harness import adaptive_attacks  # noqa: F401  registers important_instructions_obfuscated
from src.narrow import WriteAllowList


# These agents take no temperature 0 and run at their default; every other LLM runs at 0.
DEFAULT_TEMPERATURE = {"gpt-6-sol", "claude-sonnet-5-5"}
REASONING_EFFORT = {"gpt-6-sol": "none"}


def agent_llm(model: str, client: openai.OpenAI):
    """The agent's LLM: temperature 0 as AgentDojo sets it, or the default for an agent that takes none."""
    # the installed anthropic SDK takes no temperature, so a Claude agent always runs at its default
    temperature = None if model in DEFAULT_TEMPERATURE or model.startswith("claude") else 0.0
    if model.startswith("claude"):
        import anthropic
        return AnthropicLLM(anthropic.Anthropic(), model, temperature=temperature)
    if MODEL_PROVIDERS.get(model) == "ollama":  # an open model, built exactly as AgentDojo's CLI builds it for No Defense
        return get_llm("ollama", model, None, "tool")
    return OpenAILLM(client, model, REASONING_EFFORT.get(model), temperature)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--suite", required=True)
    p.add_argument("--attack", default=None)
    p.add_argument("--model", default="gpt-4o-2024-08-06", help="the agent's LLM")
    p.add_argument("--narrow-model", default="gpt-4o-2024-08-06", help="Narrow's LLM, fixed while --model changes")
    p.add_argument("--logdir", required=True)
    p.add_argument("--effects", required=True)
    p.add_argument("--no-allowlist", action="store_true", help="ablation: skip the WRITE allow-list")
    p.add_argument("--no-taint", action="store_true", help="ablation: skip the destination taint check")
    p.add_argument("--strict", action="store_true",
                   help="also refuse a destination with no provenance at all (resists obfuscated injections)")
    p.add_argument("--gate", action="store_true",
                   help="keep disallowed WRITE tools visible and refuse calls to them, instead of removing them")
    p.add_argument("--named-strict", action="store_true",
                   help="an identifier an outside party spelled does not clear a destination by itself")
    p.add_argument("--delegated", action="store_true",
                   help="text from a source the user named may supply the destination of a READ call")
    p.add_argument("--no-delegated-read", action="store_true",
                   help="a READ destination is delegated only when another argument is user-typed, like a WRITE")
    p.add_argument("--laundering", action="store_true",
                   help="register the laundering injection tasks of src/harness/laundering_attacks.py")
    p.add_argument("--user-tasks", nargs="*", default=None)
    p.add_argument("--injection-tasks", nargs="*", default=None)
    a = p.parse_args()
    if a.laundering:
        from src.harness import laundering_attacks  # noqa: F401  registers injection_task_14 / _6 / _9

    effects = Effects(a.effects, a.suite)
    client = openai.OpenAI()
    llm = agent_llm(a.model, client)
    elements = [SystemMessage(load_system_message(None)), InitQuery()]
    if not a.no_allowlist:
        elements.append(WriteAllowList(effects, client, a.narrow_model, prune=not a.gate))
    elements += [llm, ToolsExecutionLoop([GuardedToolsExecutor(effects, taint=not a.no_taint, strict=a.strict,
                                                               gate=a.gate, named_strict=a.named_strict,
                                                               delegated=a.delegated,
                                                               delegated_read=not a.no_delegated_read), llm])]
    pipeline = AgentPipeline(elements)
    pipeline.name = (f"{a.model}-guard" + ("" if a.no_allowlist else ("-G" if a.gate else "-A"))
                     + ("" if a.no_taint else "-T") + ("-S" if a.strict else "")
                     + ("-N" if a.named_strict else "") + ("-D" + ("" if not a.no_delegated_read else "w") if a.delegated else ""))

    suite = get_suite("v1.2", a.suite)
    logdir = Path(a.logdir)
    with logging.OutputLogger(str(logdir)):
        if a.attack:
            attack = attacks.load_attack(a.attack, suite, pipeline)
            r = benchmark.benchmark_suite_with_injections(pipeline, suite, attack, logdir, force_rerun=False,
                                                          user_tasks=a.user_tasks, injection_tasks=a.injection_tasks)
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
