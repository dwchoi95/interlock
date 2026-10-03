# Experiment results

Frozen copies of the per-case results behind the paper's figures and tables. `python experiments/tables.py`
recomputes Table 2, Table 3 and the exact McNemar tests of Sections 5.2 and 5.4 from them without any model call.
All runs use `gpt-4o-2024-08-06` for every LLM role, at temperature 0.

| Folder | Paper | What it holds |
| --- | --- | --- |
| `E1/` | Figure 2 (Section 2.2) | The hidden-effect audit. Per-tool judgments from the code and from the definition alone (`e1-main.json`, `e1-k8s.json`), both annotators' independent sheets (`e1_audit_annotator{1,2}-{main,k8s}.csv`, Cohen's kappa 0.83 over the 552 candidates), the consensus sheet that settles the final verdicts (`e1_audit_consensus-{main,k8s}.csv`), the summary (`e1_audit_summary-main.json`), the 111 confirmed tools on 21 MCPWild servers (`e1_confirmed.json`), and the figure script (`hidden_effects.py`). |
| `RQ1/` | Table 2 (Section 5.2) | AgentDojo `benign.csv` (97 user tasks) and `attack.csv` (949 security cases) per technique: No Defense, Tool Filter, Progent 0.1.35, CaMeL and Interlock (`interlock-v67c`: `--gate --strict --delegated` on the v6.7 summaries of AgentDojo's tool code). |
| `RQ2/` | Table 3 (Section 5.3) | Real-server episodes on chrome-devtools-mcp (`chrome`), @agent-infra/mcp-server-browser (`browser`) and mcp-grafana (`grafana`): three injections (`inj*.json`, leaks and UUA) and three benign runs (`benign*.json`, BU) per server under No Defense, Tool Filter and Interlock. Leaks: 9/9, 9/9 and 0/9 (Guard refused eight calls by destination; in the ninth the agent made no call toward the attacker); BU and UUA 9/9 for all three. |
| `RQ3/` | Figure 4 (Section 5.4) | The variants that turn off one stage. `agentdojo/{no-narrow,no-guard}.{benign,attack}.csv` are the AgentDojo runs (compare with `RQ1/interlock-v67c`); `realserver/<server>/{no-summarize,no-narrow,no-guard}/` are the real-server runs (compare with `RQ2/<server>/interlock`): without Summarize only mcp-grafana leaks (3/3), without Guard all nine leak, without Narrow none does, and every task is solved in all of them. |
| `summarize/` | Sections 2.2, 5.1–5.4 | The summaries the experiments read: Summarize's code summaries of the 63 MCPWild servers (`mcpwild/gpt-4o-2024-08-06-v67/`, with `stats.jsonl`), the summaries made from tool definitions alone (`mcpwild/description_vs_code/gpt-4o-2024-08-06-v67/`, the w/o Summarize variant), the audit directories behind `E1/` (`mcpwild/e1-v67{,-k8s}/`), and the AgentDojo summaries with the label file Interlock reads (`agentdojo/`). |
| `tables.py` | Tables 2 and 3, Sections 5.2 and 5.4 | Recomputes the numbers above from this folder (standard library only). |
