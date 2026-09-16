# P1 pre-registration (fixed 2026-09-16, before any P1 verdict is computed)

Throwaway spike. Question: is the 84% baseline-UNSAFE rate of P0 (strict) caused by label imprecision, or do most real configurations really compose a forbidden flow under the standard threat model?

## Subjects
Package set S (17, chosen by config coverage only):
@upstash/context7-mcp, @playwright/mcp, github-mcp-server, @modelcontextprotocol/server-filesystem, @modelcontextprotocol/server-sequential-thinking, @modelcontextprotocol/server-github, @modelcontextprotocol/server-memory, chrome-devtools-mcp, @supabase/mcp-server-supabase, shadcn, @modelcontextprotocol/server-postgres, next-devtools-mcp, serena, mcp-server-fetch, @modelcontextprotocol/server-brave-search, @modelcontextprotocol/server-puppeteer, learn.microsoft.com.
Identity aliases: api.githubcopilot.com and ghcr.io/github/github-mcp-server -> github-mcp-server; mcp.context7.com -> context7; mcp.supabase.com -> supabase; git+https://github.com/oraios/serena -> serena.
Eligible configs: non-fork, deduplicated, >= 2 distinct identities, every server in S (expected N = 880).
Version: the latest recovered version of each package (github-mcp-server v1.12.1 toolsnaps; serena latest release; learn.microsoft.com current remote).

## Threat model (primary)
The agent follows injected instructions and may pass any tool output to any tool input.
- Attacker controls: arbitrary web hosts and content; content in multi-writer or third-party sources (public GitHub issues/PRs/comments and repository content of third parties, community-submitted docs or registries, search results, any page reachable by navigation).
- Attacker does not control: the user's local files; data in the user's own databases and SaaS workspaces; responses of fixed vendor endpoints that serve vendor-authored content.
- Attacker can read: hosts/URLs they control; public repositories, issues, comments, gists; anything published publicly.

## Labels (per tool, decided by reading source code; evidence = file:line)
- SECRET: returns non-public data into context — host files; databases reached with user credentials; private SaaS data via user token or session; content of file://, localhost or intranet pages if the tool can navigate or fetch there.
- UNTRUSTED: returns attacker-controllable content (see threat model).
- SINK: can deliver agent-supplied data to an attacker-readable destination — requests to agent-chosen URLs/hosts (query strings, navigation, subresource loads count); writes to public repos/issues/comments/gists; messages to agent-chosen recipients. Queries sent to fixed vendor endpoints are not SINK.
- HOSTEXEC: arbitrary code execution on the host, including escapable sandboxes; implies SECRET, UNTRUSTED, SINK. Code execution confined to a browser page or a remote vendor sandbox is not HOSTEXEC; label its actual reach instead.
- Value conditions: config flags, env vars, URL paths/queries or headers that remove tools or labels (read-only modes, toolset selection, origin allow-lists restricting navigation to fixed non-attacker hosts, capability switches). Unknown values default to the least restrictive behaviour.

## Verdict
UNSAFE iff the union of labels over the config's servers, after applying value conditions present in that config, contains SECRET, UNTRUSTED and SINK (context as universal channel, same as v0). v0 (strict) labels are recomputed on the same configs and versions for comparison.

## Secondary variant (sensitivity only)
Rows and records in user-credential stores (Supabase, Postgres, GitHub private data) are also UNTRUSTED (end users or collaborators can write them).

## Branching (primary variant, eligible configs)
- precise UNSAFE >= 70%: interpretation 1 (real danger) -> direction: measurement + configuration-level least-privilege repair.
- precise UNSAFE <= 40%: interpretation 2 (label imprecision) -> direction: precise effect summaries, with update contracts as an application.
- otherwise: combined direction.
Guard: if N < 300 or any package lacks evidence-backed summaries for > 20% of its tools, report INCONCLUSIVE.

## Adjudication quality
Summaries are produced per package from source code (npm pack / git, never executed). The lead author spot-checks at least 5 labelled tool claims across packages against the cited evidence and reports disagreements.

## Data handling (extends P0)
Configs are re-fetched from stored hit metadata and parsed in memory. Additionally stored per server: flag names; values only for allow-listed non-secret flags/env/query keys (read-only, toolsets, caps, features, allowed/blocked origins, browser/profile mode, project-ref presence as boolean); remote URL path; filesystem roots reduced to a class (home, project-relative, system root, tmp, other). No tokens, keys, connection strings or full paths are written.
