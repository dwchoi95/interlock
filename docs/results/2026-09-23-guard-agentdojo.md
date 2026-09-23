# 2026-09-23 — Effect-typed call guard on AgentDojo

Fixed setting throughout: AgentDojo v1.2, `gpt-4o-2024-05-13` for every LLM
role, attack `important_instructions`, temperature 0, one run per case, the
909 security cases Progent's vendored suite defines (travel injection tasks 2
and 6 dropped), 97 benign user tasks. Baselines were run by us under the same
setting (`experiments/run_baseline.sh`, `run_progent.sh`, `run_camel.sh`).

## Why the design changed

The paper's configuration-time deny-list switches whole tools off. Replaying
the undefended trajectories showed that 38 of the 413 successful attacks are
carried out entirely with tools the user task itself needs (the task sends an
email; the injection says send it to the attacker too). No tool-level control
separates those calls, so a tool-level deny-list cannot go below **ASR
4.18%** on this benchmark even with perfect knowledge of each task's tools —
above Progent's measured 3.96 (3.08 corrected, see below). The guard in
`experiments/guard.py` is what replaced it.

## The guard

Two rules, both driven by per-tool effect labels
(`experiments/effects/agentdojo.v4.json`: READ/WRITE, destination arguments,
injectable output fields, identifier fields — classified from the tool code
and adversarially re-checked):

1. **WRITE allow-list.** Before the first turn, an LLM that sees only the
   system message and the user's query names the WRITE tools the task needs.
   Only WRITEs with a destination or value argument are gated; READs and
   incidental-side-effect tools (`get_unread_emails`) never are. The LLM
   never sees a tool output, so an injection cannot widen the list.
2. **Destination taint.** At every call with a destination argument, each
   value is traced to the query, to earlier results by field, or to
   attacker-writable text. Four leaf classes: untrusted prose (containment
   taints), fixed prose (containment clears), identifier (equality clears),
   NAMED identifier (equality clears, containment taints — an attacker-named
   channel is a real channel but can carry an instruction). Short ids match
   identifiers by equality only. `--strict` also refuses a long destination
   with no provenance at all.

Refusals name no argument or value; they tell the agent that instructions in
tool results are not from the user and to continue the original task. In
**gate mode** (`--gate`) disallowed WRITEs stay visible and are refused on
call instead of being removed from the runtime.

## Results (slack `injection_task_5` scored from executed calls in every arm)

Three attack runs per arm where marked; BU pooled over every benign run of
that configuration. `results/<label>/`.

| Arm | BU (mean ± sd, n) | UUA (mean ± sd, n) | ASR (mean ± sd, n) |
|---|---:|---:|---:|
| No defense | 78.35 ± 2.06 (4) | 43.23 (1) | 45.43 (1) |
| Tool filter | 68.56 ± 3.81 (4) | 57.65 (1) | 5.50 (1) |
| Progent | 71.13 ± 1.60 (6) | 58.71 ± 0.77 (3) | 3.15 ± 0.23 (3) |
| CaMeL | 22.68 (1) | 27.39 (1) | 0.00 (1) |
| Guard, prune mode | 71.65 ± 2.13 (8) | 54.86 ± 0.17 (3) | 1.28 ± 0.17 (3) |
| **Guard, gate mode** | 73.88 ± 0.60 (3) | **61.35 ± 0.06 (3)** | **1.06 ± 0.17 (3)** |

**Guard, gate + strict — the recommended configuration** (single run each,
`results/guard4-S`, `results/guard4-S-obf`):

| Attack | BU | UUA | ASR |
|---|---:|---:|---:|
| `important_instructions` | 75.26 | 60.40 | 1.10 |
| `important_instructions_obfuscated` | 69.07 | 59.96 | 1.21 |

Strict changes nothing on the plain attack (ASR 1.10 either way) and holds
the obfuscated attack at 1.21 where the default taint rule lets 8.91 through;
UUA stays at ~60 in both. Per-suite ASR (banking / slack / travel / workspace):
0.7 / 2.9 / 1.0 / 0.9 plain, 0.7 / 2.9 / 2.0 / 0.9 obfuscated.

UUA and ASR are stable run to run (sd ≤ 0.8) because n = 909; BU is not
(sd 2–4 on n = 97), so single-run BU differences of a few points — including
the ones in the baselines' own papers — are noise. Against Progent the guard's
UUA advantage is >3 of Progent's sd and its ASR is a third; BU is comparable.

### Ablation (v4 labels, prune mode, single runs)

| | BU | UUA | ASR |
|---|---:|---:|---:|
| allow-list only | 76.29 | 54.13 | 12.54 |
| taint only | 75.26 | 56.44 | 7.48 |
| both | 69.07–73.20 | 55.0 | 1.1–1.4 |

The two rules are complementary: each alone leaves 7–13% of attacks, the
pair leaves about 1%.

### Adaptive attack (`important_instructions_obfuscated`, destinations spelt "mark [at] gmail [dot] com")

| | UUA | ASR |
|---|---:|---:|
| No defense | 46.97 | 39.05 |
| Guard, default taint | 55.12 | 8.91 |
| Guard, `--strict` | 55.89 | **1.10** |

GPT-4o reconstructs the obfuscated address, which then has no provenance;
strict refuses it. Offline, strict costs one benign task (an invite the agent
solved by inventing an address).

### Why gate beat prune: absence is silence, refusal is a signal

Under attack, prune-mode UUA on the 175 runs where the guard refused a call
was 44.0%, below the undefended 55.4%; 76 of them made no further tool call
at all. Changing only the refusal text lifted that subset to 55.4%. But the
guard never refused anything in the other 734 runs — the injected tool was
simply absent (the agent never once called a removed tool) — and there the
agent drifted while Progent's, told "not allowed", returned to the task.
Leaving the tools visible and refusing with the same message lifted UUA from
54.9 to 61.3 at no change in ASR.

### What remains

Ten attacks per run survive in every configuration. In each the destination
is legitimate and only the content or the choice is hostile: a phishing link
sent to a real colleague, a real file the injection names for deletion, a real
hotel the injection names for booking. Two rules that would catch some of them
were replayed offline and rejected: refusing a destination merely mentioned in
attacker text (−2 benign tasks, −1 attack) and taint on value arguments (−4,
−1). Five benign tasks per run are lost where the user asks for a destination
to be read out of a page or file ("her email is on her website", "pay the bill
in the file"); reading such a value out is indistinguishable from an injection
doing the same, and Progent's policies have the same hole.

## Scoring correction

AgentDojo scores slack `injection_task_5` from the call trace because its end
state is unchanged, so a call a defense refused still counts. Progent's README
records a manual correction. `experiments/aggregate.py` replays the task's
state machine over the calls that actually executed, for every arm; it moves
Progent 3.96 → 3.08 (8 runs), gate-mode guard 1.76 → 1.10 (6), taint-only
8.14 → 7.48 (6), and nothing else by more than one run. The logged value is
kept beside the corrected one.

## Offline replay

`experiments/offline_eval.py` replays the undefended trajectories through the
guard's own functions and predicted BU 74.23 / ASR 0.55 (oracle allow-list)
for v4; online gave BU 75.26 on the first run and ASR 0.99. The two
assumptions it makes — a refused benign call fails the task, a refused attack
call stops the attack — held exactly online (0/9 and 0/144 counter-examples).

## Files

- `experiments/guard.py`, `run_guard.py`, `run_guard.sh` — the guard and runner
- `experiments/effects/agentdojo.json` (verified original) … `.v4.json` (used)
- `experiments/offline_eval.py`, `adaptive_attacks.py`, `run_benign.sh`
- `experiments/aggregate.py` — metrics, 909 filter, task-5 correction
