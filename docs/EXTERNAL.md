# External checkouts

Third-party code used for the experiments. Not committed: clone it with the commands below,
which pin the revisions the experiments were run against.

```bash
git clone https://github.com/ethz-spylab/agentdojo.git baselines/agentdojo && git -C baselines/agentdojo checkout 089ed468cf3ed0322acc66b0211f26d9d90dbf60
git clone https://github.com/sunblaze-ucb/progent.git baselines/Progent && git -C baselines/Progent checkout 8a8eb894b9d568a32b4e58252fea85b47f789c77
git clone https://github.com/google-research/camel-prompt-injection.git baselines/CaMeL && git -C baselines/CaMeL checkout f083b6b396399d3b3c7f2ddaf613a5945eaf32d8
```

| Path | Source | Revision | Retrieved |
|---|---|---|---|
| `baselines/agentdojo` | github.com/ethz-spylab/agentdojo | `089ed468cf3ed0322acc66b0211f26d9d90dbf60` | 2026-09-22 |
| `baselines/Progent` | github.com/sunblaze-ucb/progent | `8a8eb894b9d568a32b4e58252fea85b47f789c77` | 2026-09-22 |
| `baselines/CaMeL` | github.com/google-research/camel-prompt-injection | `f083b6b396399d3b3c7f2ddaf613a5945eaf32d8` | 2026-09-22 |

## Modifications to the checkouts

`baselines/agentdojo` — `src/agentdojo/models.py` only. Three model identifiers
are registered so the benchmark will accept them: `gpt-4o-2024-08-06` (the
snapshot Progent's `run.sh` uses, absent from upstream's enum but present in
Progent's vendored copy), `gpt-5.6-sol` and `claude-opus-5`. Each addition is
one entry in `ModelsEnum`, one in `MODEL_PROVIDERS` and one in `MODEL_NAMES`,
the last being the prose name the `important_instructions` attack addresses the
model by. No task, checker, attack or pipeline code is touched.

`baselines/Progent` — none. An earlier attempt to port AgentDojo's v1.2.1 and
v1.2.2 suite overlays into its vendored copy was reverted with `git checkout`;
the experiments run at v1.2, which the copy supports as shipped.

`baselines/CaMeL` — none.
