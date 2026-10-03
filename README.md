# Interlock: Enforcing Least Privilege on MCP Tool Calls with Effects Recovered from Server Code

Interlock enforces least privilege on the tool calls of Model Context Protocol (MCP) agents based on the effects
each tool actually has, which it reads from the server's source code before the agent runs. It works in three stages:

- **Summarize**: a language model reads the server's code and writes a per-tool summary of its effects, citing the
  `file:line` behind every judgment; a deterministic checker re-reads the cited lines.
- **Narrow**: before the agent's first turn, only the WRITE tools the task needs are allowed.
- **Guard**: at every call, a destination found only in text an attacker can write is refused.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- `npm`, `pip` and `git`, with which Summarize fetches package sources
- Docker, for the real-server experiments
- An OpenAI API key in `.env` (`OPENAI_API_KEY=...`); every LLM role is `gpt-4o-2024-08-06` at temperature 0

Our experiments ran on a macOS machine with an Apple M1 Ultra 20-core processor and 64 GB of memory.

## Installation

```bash
$ uv sync
$ git clone https://github.com/ethz-spylab/agentdojo.git baselines/agentdojo && git -C baselines/agentdojo checkout 089ed468cf3ed0322acc66b0211f26d9d90dbf60
$ git clone https://github.com/sunblaze-ucb/progent.git baselines/Progent && git -C baselines/Progent checkout 8a8eb894b9d568a32b4e58252fea85b47f789c77
$ git clone https://github.com/google-research/camel-prompt-injection.git baselines/CaMeL && git -C baselines/CaMeL checkout f083b6b396399d3b3c7f2ddaf613a5945eaf32d8
```

Set up each checkout per its own README in `baselines/<name>/.venv`, and install `openai` and `pyyaml` into
AgentDojo's environment. The real-server experiments use three Docker images:

```bash
$ docker build -t interlock-rq2:0806 -f benchmark/mcpwild/realserver/Dockerfile benchmark/mcpwild/realserver
$ docker build -t interlock-rq2-chrome:0806 -f benchmark/mcpwild/realserver/Dockerfile.chrome benchmark/mcpwild/realserver
$ bash benchmark/mcpwild/realserver/grafana/standup.sh
```

## Benchmarks

- **AgentDojo** v1.2 (from the checkout): four suites, 97 user tasks and 949 security cases.
- **MCPWild** (`benchmark/mcpwild/`): 63 widely used MCP servers with 1,353 tools, each pinned to a version whose
  source and tool definitions both exist; `benchmark/mcpwild/README.md` describes the selection, and
  `benchmark/mcpwild/realserver/` holds the real-server scenarios.

## How to Run

### Quick test

```bash
$ python experiments/tables.py                       # the paper's tables from the frozen results, no model call
$ python run.py realserver chrome interlock inj2     # one real-server episode, a few minutes
chrome   interlock   inj2       utility=True attack=False reached=[] refused=['new_page']
```

### Summarize

```bash
$ python run.py summarize profile npm:chrome-devtools-mcp --version 1.10.1 \
      --model gpt-4o-2024-08-06 --temperature 0 --out results/summarize/mcpwild/my-run
```
This writes the per-tool summary of the package and one row of `stats.jsonl` (verified and undetermined tools).

### AgentDojo

```bash
$ python run.py agentdojo interlock enforcement/main/interlock/r1 --gate --strict --delegated
$ python run.py agentdojo no-defense enforcement/main/no-defense/r1     # also: tool-filter, progent
$ python run.py agentdojo camel r1
$ python run.py metrics export enforcement/main/interlock/r1           # benign.csv and attack.csv
```
Interlock reads `experiments/summarize/agentdojo/labels/agentdojo.summarize-gpt-4o-2024-08-06-v67.json` by default.

### Real servers

```bash
$ python run.py realserver all <config>              # chrome, browser, grafana; injections inj1-inj3
$ python run.py realserver all <config> benign       # three benign runs per server
```
`<config>` is `no-defense`, `tool-filter` or `interlock`. Episodes are written to
`results/enforcement/realserver-v67/<server>/<config>/`.

### Ablations

```bash
$ python run.py agentdojo interlock enforcement/ablation/no-narrow/r1 --strict --no-allowlist --delegated
$ python run.py agentdojo interlock enforcement/ablation/no-guard/r1 --gate --no-taint
$ python run.py realserver all definition            # w/o Summarize; also no-narrow, no-guard
```

## Source Code Structure

```
.
├── run.py                 Entry point of every run
├── src
│   ├── summarize          Summarize: fetching sources, the model's judgment, Derive and the citation checker
│   ├── narrow.py          Narrow: the WRITE allow-list built from the task
│   ├── guard.py           Guard: result fields, provenance and the refusal rule
│   ├── harness            Interlock in AgentDojo's pipeline, the baselines' commands, the real-server scenarios
│   ├── metrics.py         Per-case CSV export
│   └── analysis           Analyses, including the hidden-effect audit
├── benchmark              MCPWild and the AgentDojo effect labels
└── experiments            Per-case results of the paper and the summaries they use (see experiments/README.md)
```
