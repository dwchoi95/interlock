# P1 results (2026-09-16) — see PREREG_P1.md

## Setup as run
- N = 880 eligible configs (all 880 re-fetched and matched P0 identities).
- 17 servers adjudicated from source code by four agents (13 npm/PyPI packages at latest recovered version, github-mcp-server v1.12.1, serena v1.7.0, learn.microsoft.com remote docs). 377 tool summaries with evidence.
- Value conditions encoded: serena --context/--mode; github read-only (flag/env/header/path) and toolsets (tool->toolset map extracted from Go source, 9 names mapped by prefix); supabase read-only and features (tool->group map from source); context7 credential presence (sensitivity only).

## Pre-registered result
| | value |
|---|---|
| precise UNSAFE (primary) | **740/880 = 84.1%** |
| bounds over the undetermined learn.microsoft.com docs_fetch, 3 configs with unmodelled chrome-devtools flags, context7 credential visibility | 84.0% – 85.6% |
| secondary variant | 84.1% |
| v0 (strict) on the same configs and versions | 788/880 = 89.5%; verdicts disagree on 86 configs (67 v0-only UNSAFE, 19 precise-only UNSAFE) |
| guard | learn.microsoft.com has 1/3 tools undetermined (> 20%) -> **formally INCONCLUSIVE**; the branch (>= 70%) holds at both bounds |
| branch | interpretation 1 (real danger) |

## Spot-checks (5/5 agree)
Playwright browser_run_code_unsafe is in the default "Core automation" set and documented as RCE-equivalent; chrome-devtools navigate_page validateUrl blocks only chrome:/chrome-untrusted:/chrome-extension:; mcp-server-fetch issues a plain httpx GET with redirects and no host filter; serena claude-code context excludes execute_shell_command; shadcn resolves agent-supplied URLs and local .json items (SECRET there is weak: only registry-schema fields are returned).

## Exploratory (not pre-registered)
- Every UNSAFE config already contains one server whose own tools complete SECRET + UNTRUSTED + SINK: composition-only UNSAFE = **0/880**.
- Single-server drivers (configs): @playwright/mcp 359, github-mcp-server 183, server-github (archived) 172, chrome-devtools-mcp 139, shadcn 121, supabase 81, serena (default context) 28, mcp-server-fetch 26, server-puppeteer 14.
- Composition-only share rises to 1.5% (docs_fetch as SINK), 3.3% (no local-URL SECRET for fetch/chrome-devtools), and 19.1% only when browsers and fetch get neither SECRET nor HOSTEXEC (ignoring Playwright's documented RCE tool); 58 of those 168 are Playwright + context7, which depend on context7's credential-gated SECRET.
- Available mitigations are rarely used: github read-only 3/189 server entries, toolsets 12/189; supabase read-only 32/116. Several drivers expose no mitigation at all (Playwright's RCE tool cannot be disabled by flag; archived server-github, shadcn, fetch have no flags).

## Readings
1. The 84% is not a labelling artefact: code-level adjudication reproduces it within 6 points of v0.
2. The operative risk in popular configurations is intra-server capability bundling, not cross-server composition.
3. Where a remedy exists it is a server flag or a host-level per-tool deny; for several drivers only the host-level deny exists.
