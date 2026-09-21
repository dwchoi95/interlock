# External checkouts

Third-party code used for the experiments. Not committed: clone it with the commands below,
which pin the revisions the experiments were run against.

```bash
git clone https://github.com/ethz-spylab/agentdojo.git benchmark/agentdojo && git -C benchmark/agentdojo checkout 089ed468cf3ed0322acc66b0211f26d9d90dbf60
git clone https://github.com/sunblaze-ucb/progent.git baselines/Progent && git -C baselines/Progent checkout 8a8eb894b9d568a32b4e58252fea85b47f789c77
git clone https://github.com/google-research/camel-prompt-injection.git baselines/CaMeL && git -C baselines/CaMeL checkout f083b6b396399d3b3c7f2ddaf613a5945eaf32d8
curl -L -o spa.zip https://anonymous.4open.science/api/repo/spa-C26E/zip && unzip -q spa.zip -d baselines/SPA
```

| Path | Source | Revision | Retrieved |
|---|---|---|---|
| `benchmark/agentdojo` | github.com/ethz-spylab/agentdojo | `089ed468cf3ed0322acc66b0211f26d9d90dbf60` | 2026-09-22 |
| `baselines/Progent` | github.com/sunblaze-ucb/progent | `8a8eb894b9d568a32b4e58252fea85b47f789c77` | 2026-09-22 |
| `baselines/CaMeL` | github.com/google-research/camel-prompt-injection | `f083b6b396399d3b3c7f2ddaf613a5945eaf32d8` | 2026-09-22 |
| `baselines/SPA` | anonymous.4open.science/r/spa-C26E | anonymous archive, no revision exposed | 2026-09-22 |
