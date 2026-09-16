# INTERLOCK design: least-privilege tool surfaces for LLM agent configurations

Date: 2026-09-16. Status: approved for planning. Supersedes the set-level composition framing of the earlier draft.

## 1. Why this, and what changed

The earlier framing (forbidden flows across a *set* of providers, minimal remedy over disconnection) was dropped for two verified reasons.

- Set-level judgement already exists: Snyk/Invariant Toxic Flow Analysis, AgentSeal `scan-mcp`, DSCC (arXiv 2607.03423), SkillGuard (arXiv 2608.30041), ChainGuard/ColluSkill (arXiv 2608.09732).
- Our own measurement (spikes/p0) refutes the premise: in 740 of 880 real configurations that are trifecta-complete, a **single** server already bundles private-data access, attacker-controlled content and an attacker-readable sink. Composition-only cases are 0, and at most 3.3% under relaxed effect attribution.

What nothing does: turn that verdict into a persistent, least-privilege **configuration** the user keeps, under their own task requirements. Runtime systems (Progent, CaMeL, Fides, AgenTRIM, ToolGuardian, SkillGuard) decide per query or per call and leave the installed surface intact. Minimize-under-a-test-suite exists outside agents (CHISEL CCS'18, RAZOR USENIX'19, IAM-PolicyRefiner OOPSLA'24, ALPS INFOCOM'26).

Do not claim: first static analysis of MCP configs; first trifecta detection; first least-privilege for agents. Claim: first config-time synthesis of a host-level allow/deny patch under task preservation, validated by running benign tasks and attacks before and after. Note Claude Code's `/fewer-permission-prompts` explicitly: it writes `mcp__server__tool` entries from transcripts, allow-only, no threat model, no task check.

## 2. Evidence base already in hand (spikes/p0)

19,777 deduplicated public configs; 880 analysable; 84.1% trifecta-complete (bounds 84.0-85.6%); single-server sufficient in 100% of those; GitHub read-only used in 3/189 entries, Supabase read-only in 32/116; several drivers expose no mitigation at all; 91.3% of stdio entries unpinned; per-version tool surfaces for 35 packages (854 runs, 607 recovered); code-adjudicated effect summaries for 17 servers (377 tools) with file:line evidence; 5/5 spot-checks agreed.

## 3. Claim-evidence ledger

| ID | Claim | Evidence required | Status |
|---|---|---|---|
| C1 | Real configurations are trifecta-complete at a high rate, and a single server suffices | P0/P1 measurement, extended server coverage | mostly done |
| C2 | Available mitigations are unused or absent | adoption census + mechanism census | done for 17 servers |
| C3 | Effect summaries are accurate enough to act on | human-labelled gold sample, agreement; dynamic confirmation | to do (Phase B) |
| C4 | Synthesized patches remove exploitable flows while preserving tasks | before/after benign utility, utility under attack, targeted ASR | to do (Phase D) |
| C5 | Patches beat blunt alternatives (server removal, server modes, heuristic deny) and complement runtime enforcement | same harness, same policy and budget | to do (Phase D) |
| C6 | Patches survive provider updates, or re-decision catches when they do not | replay over collected version histories | to do (Phase E) |

## 4. Scope

In scope: MCP servers configured in the five common host formats; static config-time analysis; patch synthesis over host per-tool deny lists, server flags, argument narrowing, server removal; validation by execution.

Out of scope for v1: agent skills as a separate artifact type; value-level flow policies beyond the trifecta labels (path prefixes, domains) except where a server flag already expresses them; multi-agent/subagent compartmentalization; proving anything about servers whose source is unavailable (remote-only servers are summarized from docs and marked as such).

## 5. Architecture

```
config/    parse and emit .mcp.json, .cursor/mcp.json, .vscode/mcp.json, claude_desktop_config.json, .codex/config.toml
effects/   provider effect profiles: (package, version) -> per-tool labels + value conditions + evidence
policy/    forbidden-flow policy; default: SECRET and UNTRUSTED and SINK co-present; HOSTEXEC implies all three
decide/    apply value conditions to a config -> label union, verdict, witness tool sets
repair/    witness-guided MaxSAT over knobs -> minimum-cost patch -> config diff
recheck/   provider or config change -> re-summarize changed provider, re-decide, report patch validity
harness/   docker MCP gateway, agent loop, benign task runner, attack scenarios, canary and honeypot
```

Interfaces are files, so each stage is independently testable: `profile.json` per provider version, `verdict.json` per config, `patch.diff` per config, `run.jsonl` per execution.

### Design decisions

1. **Trust-ranked evidence.** Effects come from configuration arguments and server source code first, then declared schemas and annotations. Free-text descriptions may only *raise* an effect, never lower one, because they are attacker-writable.
2. **LLM adjudication with a deterministic check.** Summaries are produced per tool with mandatory `file:line` evidence; a checker verifies the cited location exists and contains the claimed construct. Unverified claims become `undetermined` and take the least-restrictive default. Cached by content hash of the package version. This industrialises what four agents did by hand in P1.
3. **Host-level deny is the primary knob.** Several drivers expose no server-side control (Playwright's `browser_run_code_unsafe`, the archived server-github, shadcn, mcp-server-fetch). Server flags are preferred when they exist because they are cheaper to review, but the synthesis must not depend on them.
4. **Task preservation in two strengths.** Strict: keep only tools observed in successful task traces. Expanded (RAZOR's lesson that suites under-specify): additionally keep read-only sibling tools of the same server. Both are reported; the expanded variant is the default recommendation.
5. **Witness-guided repair loop.** decide -> witness -> blocking clause -> MaxSAT (PySAT RC2) -> apply -> re-decide, until no forbidden flow. Knob space is finite, so it terminates. Costs: tool deny 1, server flag 2 per tool it removes, argument narrowing 3, server removal 50 (weights are a reported parameter, varied in RQ4).

## 6. Evaluation

RQ1 prevalence and mechanism census (extends P0/P1). RQ2 effect-summary accuracy against a human-labelled gold sample and dynamic confirmation, including a tool-level cross-classification against the capability categories of the registry census (arXiv 2509.06572, v3: 128 servers with an external-ingestion tool, 155 with a privacy-access tool, 96 with a network-access tool, 2 with all three out of 1,360): for the servers we adjudicate, we report where our code-derived effects and their metadata-level categories disagree and which of the two a manual reading supports, so that the order-of-magnitude difference between their per-server rate and ours is explained by measurement rather than asserted. RQ3 repair effectiveness and utility. RQ4 ablations (trust ranking off, one-shot instead of witness-guided, task constraint off, expansion off, cost weights). RQ5 durability across provider updates and generality across hosts and models.

Metrics follow AgentDojo: benign utility, utility under attack, targeted attack success rate; plus patch cost (tools denied, servers removed) and analysis time. Statistics: McNemar for paired task success, Wilcoxon signed-rank with Cliff's delta for costs, confidence intervals for ASR. Repetitions 3-5 with fixed seeds; the same models, budget and policy for every arm.

Baselines: undefended; whole-server removal; server modes only; heuristic name-based deny; runtime enforcement (Progent-style policy) under the same policy.

Benign tasks from LiveMCPBench and MCP-Universe. Attacks built per configuration from published patterns (public-issue injection to private-repo exfiltration, support-ticket rows, web-page injection into browser servers, local canary files) plus MCPTox cases. No existing suite covers benign and attack halves on the same servers, so the harness is an artifact contribution.

## 7. Phases and decision gates

Each phase ends at a gate. The gate's numeric thresholds are written into a `PREREG_<phase>.md` at the start of that phase, before any of its measurements run, in the style already used for P0 and P1; the qualitative branches below are fixed now. A gate can send the work down a different path; the plan is expected to change. No phase starts until the previous gate's outcome is recorded in a dated results file.

- **Phase A - corpus and profiles at scale.** Extend adjudication to the servers needed for a target evaluation set; build the checker; measure how often LLM claims fail verification.
  *Gate A:* if verified-claim rate is high enough to trust profiles, continue; if it is low, fall back to a narrower, hand-adjudicated server set and shrink the evaluation scope; if code-derived summaries prove impractical at any scale, the paper becomes measurement-only (C1, C2) and the technique is dropped.
- **Phase B - accuracy of summaries.** Human gold sample, agreement, dynamic confirmation of a sample of flows.
  *Gate B:* if summaries disagree with humans materially, fix the pipeline before any repair work; if flows cannot be confirmed dynamically at all, the policy or threat model is wrong and Phase C is re-designed.
- **Phase C - decide and repair.** Implement decide/repair, apply to the corpus, inspect patches by hand on a sample.
  *Gate C:* if patches are dominated by whole-server removal (that is, the cheap knobs rarely suffice), the contribution shifts from synthesis to the ecosystem finding that hosts and servers lack the necessary controls, and the paper's technique section becomes a mechanism proposal plus prototype.
- **Phase D - execution study.** Benign and attack runs before and after, all baselines.
  *Gate D:* if utility loss is severe at acceptable ASR reduction, report the trade-off honestly and investigate whether the expanded task preservation or different cost weights recover utility; if the runtime baseline dominates on both axes, reposition as complementary (config-time narrowing plus runtime enforcement) and say so.
- **Phase E - durability and generality.** Update replay, hosts, models.
  *Gate E:* if patches break often under updates, re-decision becomes a first-class part of the technique rather than an appendix.

Rule for every gate: record the numbers and the branch taken in a dated results file, then rewrite the remaining plan. Do not carry an obsolete plan forward.

## 8. Risks

Task suites under-specify real use (mitigated by the expanded variant and by reporting both). Code-derived summaries are expensive and imperfect (mitigated by caching, the evidence checker, and honest `undetermined` handling). Worst-case threat model inflates prevalence (mitigated by reporting the secondary variant and the relaxed sensitivities). Public GitHub configs are not a random sample of deployments (reported as a threat; the search API returns ranked, capped results). Dynamic confirmation under-approximates: a failed attack does not prove safety, so precision is reported as a lower bound.

## 9. Artifacts

Tool implementation; the configuration corpus (identity and non-secret settings only, no credentials); adjudicated effect profiles with evidence; the benign-plus-attack harness; all pre-registration and results files.
