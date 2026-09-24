# Interlock: recovering tool effects from MCP server code for least-privilege LLM agents

Artifact for the paper of the same title. Interlock recovers what each tool of a
real MCP server reaches from the server's source code rather than from the prose
the provider wrote, and enforces least privilege on an LLM agent's tool calls
with the recovered labels. It has three stages:

- **Summarize** reads the package source and judges, per advertised tool, which
  of four effects it can reach (SECRET, UNTRUSTED, SINK, HOSTEXEC), with a
  `file:line` citation for every claim; a deterministic checker re-reads each
  cited span and marks what it cannot confirm as undetermined.
- **Narrow** builds, before the agent's first turn and from the task alone, an
  allow-list of the WRITE tools the task needs.
- **Guard** traces, at every call with a destination argument, where each value
  could have come from, and refuses a call whose value can only have come from
  text an attacker can write.

This repository holds the implementation, the code-derived effect summaries for
the 34 most frequently configured servers, the AgentDojo experiment harness, and
the per-case outcomes of every run the paper reports.

## Layout

| Path | Contents |
|---|---|
| `src/` | Summarize: source fetching with scripts disabled (`source.py`), file selection (`select.py`), model adjudication under a fixed rubric (`adjudicate.py`), the deterministic checker and profile assembly (`pipeline.py`, `evidence.py`, `profile.py`), command line (`cli.py`) |
| `profiles/` | The 34 effect summaries, one JSON per package and version; `stats.jsonl` with the checker's counts per package (claims, verified, demoted, cleared, unjudged, cost); the batch manifest and the batch failures |
| `experiments/` | Narrow and Guard for AgentDojo (`guard.py`), the runner (`run_guard.py`, `run_guard.sh`), the baseline runners (`run_baseline.sh`, `run_progent.sh`, `run_camel.sh`, `run_benign.sh`), the metrics (`aggregate.py`), the offline replay (`offline_eval.py`), the adaptive attack (`adaptive_attacks.py`), the effect labels for AgentDojo's 74 tools (`effects/`), and the per-case export and paired tests (`export_cases.py`, `paired_tests.py`) |
| `experiments/` (2026-09-24 additions) | `summarize_agentdojo.py` (Summarize on AgentDojo's own tool code, compared with the hand-checked labels), `description_vs_code.py` (the rubric with the code withheld, against the code-derived labels), `entailment_check.py` (a model of another family judges every code-verified citation), `label_sensitivity.py` (the guard replayed under single-tool label errors), `laundering_attacks.py` and `run_laundering.sh` (destinations laundered through structured identifier fields), `profile_all.py` (the 34 servers summarized with a second model), `self_agreement.py` (text and code judgments against the model's own run-to-run disagreement), `label_gap.py` (every enforcement difference between two label sets, traced to result fields), `progent_banking.py` (the payment policy in force in each of Progent's banking failures), `threat_model_check.py` (whether Summarize's reading of banking `read_file` follows the threat model it is given, with and without the variant's examples); outputs under `experiments/effects/summarize/`, `experiments/description_vs_code*/`, `experiments/entailment/`, `experiments/label_sensitivity.json`, `experiments/self_agreement.gpt-5.5.json`, `experiments/label_gap.*.json`, `experiments/threat_model_check.json`, `profiles-gpt-5.5/` and `profiles-gpt-5.5-r2/`; ledgers in `docs/results/2026-09-24-labels-judge-laundering.md` and `docs/results/2026-09-24-self-agreement-live-runs.md` |
| `results-summary/` | Per-case outcomes of every run in the paper as CSV, a recomputed metrics table, and the mapping from run labels to the paper's tables |
| `patches/` | The two changes made to the baseline checkouts (three model identifiers registered in AgentDojo, a one-line crash fix in CaMeL) |
| `docs/` | Pinned external checkouts (`EXTERNAL.md`), the pre-registration of the Summarize evaluation and its deviations record (`prereg/`), the results ledgers (`results/`), and the design documents |
| `spikes/p0/` | Configuration-corpus mining and tool-surface recovery: scripts, pre-registrations and results of the two preliminary spikes, and the data (`data/configs.jsonl`, `data/surfaces.jsonl`) |
| `tests/` | Unit tests for the Summarize stage |

## Setup

- Python 3.13. `uv sync` installs the one dependency of the Summarize stage
  (`anthropic`); `uv run --with pytest pytest` runs the tests. `src/cli.py --model`
  selects the judging model (`claude-*` through the Anthropic SDK, `gpt-*` through
  the OpenAI SDK); `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` accordingly.
- Credentials are read from `.env` at the repository root, which is never
  committed: `ANTHROPIC_API_KEY` for Summarize, `OPENAI_API_KEY` for the
  AgentDojo runs. The run scripts source `.env` themselves.
- The AgentDojo experiments need the three external checkouts at the revisions
  pinned in `docs/EXTERNAL.md`, with the two patches in `patches/` applied.
  Each checkout is set up per its own README; the scripts expect the
  interpreters `baselines/agentdojo/.venv/bin/python` (with `openai` and
  `pyyaml` also installed, which `run_guard.py` imports),
  `baselines/Progent/.venv/bin/python` and `baselines/CaMeL/.venv/bin/python`.

## Summarize (Section 3.2.1, RQ1)

```
python -m src.cli profile npm:@modelcontextprotocol/server-github --version 2025.4.8
python -m src.cli batch docs/prereg/2026-09-16-gate-a-packages.txt
```

`profile` fetches the package into `.cache/sources` without running any of its
scripts, follows a direct dependency when the package's own code never mentions
a tool name, selects the files to show, asks the model (`claude-opus-5`) for
labels with `file:line` evidence, re-checks every claim against the fetched
tree, and writes `profiles/<kind>_<package>_<version>.json` plus one row in
`profiles/stats.jsonl`. The advertised tool list and the version come from
`spikes/p0/data/surfaces.jsonl` (the latest recovered version unless
`--version` is given). `batch` runs a list of packages through the Batches
API; `--resume BATCH_ID` polls a batch that was already submitted.
`profile --threat-model received-documents` swaps the rubric's threat-model
paragraph for one in which documents the user received from other parties
(bills, notices, shared files, attachments) are written by those parties;
the default is the threat model of Section 2.1 (`src/adjudicate.py`).

The definitions and thresholds were fixed before the run
(`docs/prereg/2026-09-16-PREREG_A.md`, deviations found by the prescribed
dry-runs in `docs/prereg/2026-09-17-PREREG_A-deviations.md`); the outcome is
`docs/results/2026-09-17-gate-a.md`. The per-server counts behind the paper's
RQ1 table and figure are the rows of `profiles/stats.jsonl`.

## Narrow and Guard on AgentDojo (Sections 3.2.2 and 3.2.3, RQ2 to RQ4)

Fixed setting throughout: AgentDojo v1.2, `gpt-4o-2024-05-13` for every LLM
role, temperature 0, the `important_instructions` attack, 909 security cases
(the two travel injection tasks Progent's vendored suite drops are excluded at
aggregation) and 97 user tasks.

```
EFFECTS=experiments/effects/agentdojo.v4.json ./experiments/run_guard.sh <label> --gate            # Interlock (gate)
EFFECTS=experiments/effects/agentdojo.v4.json ./experiments/run_guard.sh <label> --gate --strict   # gate+strict
EFFECTS=experiments/effects/agentdojo.v4.json ./experiments/run_guard.sh <label> --no-taint        # ablation, allow-list only
EFFECTS=experiments/effects/agentdojo.v4.json ./experiments/run_guard.sh <label> --no-allowlist    # ablation, taint only
ATTACK=important_instructions_obfuscated EFFECTS=... ./experiments/run_guard.sh <label> --gate --strict   # adaptive attack
./experiments/run_baseline.sh <label>                          # no defense
./experiments/run_baseline.sh <label> --defense tool_filter    # tool filter
./experiments/run_progent.sh <label>
./experiments/run_camel.sh
./experiments/run_benign.sh <label> {nodef|toolfilter|progent|guard}   # benign phase only, for run-to-run variance
python3 experiments/aggregate.py results/<label>               # BU, UUA, targeted ASR
```

Without `--gate` the guard removes disallowed WRITE tools from the runtime
(prune mode); with it they stay visible and are refused when called. The
pipeline name in each log directory records the configuration: `A` allow-list
with removal, `G` allow-list with gate, `T` destination taint, `S` strict.
`aggregate.py` applies the scoring correction for slack `injection_task_5`
to every arm. `offline_eval.py` replays the undefended trajectories through
the guard's own functions with no model calls; its output for the labels used
in the paper is `experiments/offline_eval.agentdojo.v4.json`.

The effect labels for AgentDojo's tools were classified from the tool code
and re-checked: `effects/agentdojo.json` is the verified original and
`v2` to `v4` record the label changes made during development; `v4` is the
file the reported gate, strict, ablation and adaptive-attack runs use
(`docs/results/2026-09-23-guard-agentdojo.md`).

## Results

`results-summary/cases/<arm>.benign.csv` and `<arm>.attack.csv` hold the
outcome of every user task and every security case of every run;
`results-summary/summary.md` is the metrics table recomputed from them, and
`results-summary/README.md` maps the run labels to the rows of the paper's
tables. The paired tests of Section 4.3 are reproduced with

```
python3 experiments/paired_tests.py results-summary/cases/progent.attack.csv,results-summary/cases/progent-r2.attack.csv,results-summary/cases/progent-r3.attack.csv \
    results-summary/cases/guard4.attack.csv,results-summary/cases/guard4-r2.attack.csv,results-summary/cases/guard4-r3.attack.csv
```

The raw run transcripts (about 480 MB) are not part of this repository;
`experiments/export_cases.py` regenerates the CSVs from them.

## Configuration corpus (Table 1)

`spikes/p0/mine_configs.py` searches public GitHub through the `gh` CLI for
the five configuration formats current hosts read and keeps only each
server's identity and launch shape, never argument or environment values
(`data/configs.jsonl`, 21,015 files); `data/configs_p1.jsonl` is the re-parse for the
P1 spike, which keeps allow-listed, non-secret value-level settings for the
packages that spike studied (`reparse_p1.py`). `collect_surfaces.py` launched each
package version in an isolated container and recorded its `tools/list`
response (`data/surfaces.jsonl`); the container image is not part of this
repository, and the recovered surfaces are provided as data. The remaining
scripts and the `adjudication/` directory belong to the two pre-registered
preliminary spikes (`PREREG.md`, `PREREG_P1.md`, `RESULTS.md`,
`RESULTS_P1.md`) whose outcome motivated the design.

## Not included

The paper source, the raw run transcripts, the baseline checkouts (clone them
per `docs/EXTERNAL.md`), the fetched package sources (`.cache/`, regenerated by
Summarize), and the raw code-search hits behind `configs.jsonl`.
