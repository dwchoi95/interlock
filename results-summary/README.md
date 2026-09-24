# Run labels and the paper's tables

Every arm of the evaluation was run under the fixed setting (AgentDojo v1.2,
`gpt-4o-2024-05-13`, temperature 0, `important_instructions` unless noted).
`cases/<arm>.attack.csv` holds one row per security case (949 rows; the paper
uses the 909 with `in_909` true) and `cases/<arm>.benign.csv` one row per user
task (97). `summary.md` recomputes the three metrics per arm. Where the paper
reports a mean and standard deviation, it is over the per-run values of the
runs listed below.

Pipeline letters: `A` allow-list with removal (prune mode), `G` allow-list
with gate, `T` destination taint, `S` strict.

## Main comparison (Table 6 in Section 4.4)

| Row | Attack runs | Additional benign-only runs |
|---|---|---|
| No defense | `no-defense` | `no-defense-b2`, `no-defense-b3`, and the benign phase of `no-defense-obf` |
| Tool filter | `tool-filter` | `tool-filter-b2`, `tool-filter-b3`, `tool-filter-b4` |
| Progent | `progent`, `progent-r2`, `progent-r3` | `progent-b2`, `progent-b3`, `progent-b4` |
| CaMeL (with policies) | `camel:gpt-4o-2024-05-13+camel+secpol` | |
| CaMeL interpreter without policies (quoted in Section 2.2) | `camel:gpt-4o-2024-05-13+camel` | |
| Interlock (gate) | `guard4`, `guard4-r2`, `guard4-r3` | |
| Interlock (gate+strict) | `guard4-S` | |

The overlap of surviving attacks in Figure 4 is between the first runs,
`progent` and `guard4`.

## Ablation (Table 7 in Section 4.5, prune mode)

| Row | Run |
|---|---|
| Allow-list only | `guard2-A` |
| Taint only | `guard3-T` |
| Both | `guard3` (repeats: `guard3-r2`, `guard3-r3`; benign-only: `guard3-b2`, `guard3-b3`) |

## Obfuscation attack (Table 8, left; `important_instructions_obfuscated`)

| Row | Run |
|---|---|
| No defense | `no-defense-obf` |
| Interlock (prune, default taint) | `guard3-obf` |
| Interlock (prune, strict) | `guard3-S-obf` |
| Interlock (gate+strict) | `guard4-S-obf` |

## Laundering attack (Table 8, right; three added injection tasks, 77 cases)

| Row | Run |
|---|---|
| No defense | `launder-nodef` |
| Progent | `launder-progent` |
| Interlock (gate+strict) | `launder-gate` |
| Interlock (gate+strict+named) | `launder-named` (benign-only run: `launder-named-b`) |

Each arm was run three times: `launder-<arm>`, `launder-<arm>-r2`, `launder-<arm>-r3`.

## Live run on the labels Summarize produced (Table 5 and Table 6)

| Row | Run |
|---|---|
| Interlock (gate+strict, Summarize labels) | `guard-summ` (labels: `experiments/effects/agentdojo.summarize-gpt-5.5.json`) |
| Interlock (gate+strict, Summarize labels, the benchmark's threat model) | `guard-summ-rd` (labels: `experiments/effects/agentdojo.summarize-gpt-5.5-received.json`, made with `experiments/summarize_agentdojo.py --threat-model received-documents`; `experiments/threat_model_check.py` records the banking-only check of Section 4.3) |

The three tasks are registered by `experiments/laundering_attacks.py` (`injection_task_14` workspace, `injection_task_6` slack, `injection_task_9` banking); `in_909` is true for all of their cases.

## Earlier revisions

`guard`, `guard-A`, `guard2` are runs of earlier revisions of the guard and of
its labels (see the history of `experiments/guard.py` and
`experiments/effects/`), kept for completeness. The CaMeL arms come from the
log directories CaMeL's own `main.py` writes; all other arms from
`results/<label>/`.
