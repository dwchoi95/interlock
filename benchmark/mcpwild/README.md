# MCPWild

MCP servers chosen by a public popularity ranking, each pinned to one release whose tool definitions (`tools/list`)
and source code can both be obtained. Paper: Section 5.1 and Table 1.

## Candidates

Following the procedure of No-Box/MCPSEC, the candidates are the "Top 100 MCP servers by GitHub stars" of
`apappascs/mcp-servers-hub` at commit 8e2a252 (2026-03-26), `candidates/hub_0326.{md,json}`; the repository's
current README no longer carries star counts. 100 entries → 88 after merging entries of one repository → 84 after
dropping archived repositories (`candidates/hub_status.json`) → **80** after merging renamed repositories.

## Criteria

| # | A candidate is accepted when | Rejected |
| --- | --- | --- |
| (i) | it is an MCP server exposing tools (not an app, platform, editor plugin, example, or tool-less bridge) | 12 |
| (ii) | the code serving the tools is distributed and runs locally (not remote-only, not a connector to a remote service) | 7 |
| (iii) | there is a pinned artifact with its source: an npm/PyPI version, or a tagged binary or image with the source at that tag | 3 |
| (iv) | that version returns its tool definitions over `tools/list` | 1 |

Servers needing credentials or a backing service are kept. **57 repositories, 63 servers, 1,353 tools** pass.

## Files

| File | What it is |
| --- | --- |
| `criteria.py` | the 80 repositories: servers to run, or the reason there are none; the criterion each rejected one fails |
| `registry.py` → `registry.json` | latest npm/PyPI release of each server, looked up 2026-09-28; the pinned versions (`--refresh` to redo) |
| `collect.py` → `tools_list.jsonl` | one launch per server and one tool list per server (`Dockerfile` is the runner image) |
| `report.py` → `result.json`, `stats.json` | verdict per repository; the statistics of Table `tab:bench` |
| `candidates/` | the dated list, archive status, packages named on the list, GitHub metadata of accepted repositories |
| `logs/collect.log` | console output of the collection run |

```sh
python run.py benchmark collect | tee benchmark/mcpwild/logs/collect.log   # from the repository root; Docker, about an hour
python run.py benchmark report
```

Every server runs alone in a container with 2 GB of memory, one CPU and placeholder credentials; npm and PyPI
dependencies resolve as of one day after the release. Three servers contact a backing service at start-up, so
`collect.py` starts real MySQL, PostgreSQL and n8n instances on a private network for them; the Slack server runs in
its built-in demo mode. The latest `figma-developer-mcp` release (0.13.2) misses a file it imports, so 0.13.1 is used.
