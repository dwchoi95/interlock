# P0 results (2026-09-16) — see PREREG.md for definitions

## Data
- GitHub code search: 85,065 indexed files across 40 query shards; 22,552 retrievable (1,000/shard cap on 14 shards; results are relevance-ranked, not random). 21,015 parsed; 19,777 after removing forks and same-repo duplicates.
- Set size: 1 server 10,532 (53.3%); >= 2 servers 9,302.
- Server entries: npm 22,010, remote 14,250 (25.6%), other 7,564, local 7,284, pypi 3,156, docker 1,507.
- Tool surfaces: 35 most frequent npm/PyPI packages, releases since 2025-03-01 (<= 40 per package, evenly thinned), 854 version runs, 607 recovered (71.1%). Dependencies resolved as of publish date (npm_config_before / uv --exclude-newer).

## Criteria
| | as registered | strict schema (deviation) | strict + manual adjudication of flip causes |
|---|---|---|---|
| N eligible | 666 | 666 | 666 |
| G2 baseline UNSAFE (<= 80%) | 525/666 = 78.8% | **558/666 = 83.8%** | 83.8% |
| G1 SAFE configs with a flip (>= 5%) | 54/141 = 38.3% | 21/108 = 19.4% | 17/108 = 15.7% |
| distinct flip-causing version steps | 5 | 4 | **1** |
| G3 pin-all alerts / flips (>= 5) | 2360/54 = 43.7 | 1805/21 = 86.0 | 1805/17 = 106 |
| G4 unpinned stdio entries | 24,354/26,673 = 91.3% | | |
| verdict | GO (invalid, see below) | **NO-GO (G2)** | |

Pin-all alerts over all eligible configs: 15,409 (23.1 per config over the window).

## Deviation: strict schema
17 of 35 recovered next-devtools-mcp versions advertise a serialized zod object instead of JSON Schema as inputSchema (server-filesystem 2025.8.21 advertises an empty schema). v0 read these as "no parameters"; the 0.2.1 -> 0.2.2 schema fix then appeared as a capability gain and produced 33 of 54 registered flips. Strict mode treats such versions as unobservable. This changes measurement validity, not labelling rules.

## Manual adjudication of strict flip causes
- @playwright/mcp 0.0.45 -> 0.0.47 (17 configs): adds browser_run_code, which runs agent code with Node vm.runInContext and an outer-realm `page` object. Node documents vm as not a security mechanism; EXEC judged real (code reading, not exploited).
- @bytebase/dbhub 0.11.8 -> 0.16.1 (2): search_objects is a local DB catalogue search; v0 "search" rule misfires as web search. False.
- awslabs.aws-documentation-mcp-server 1.1.26 -> 1.1.27 (1): search_table's `table` param triggers SECRET for a public documentation table. False.
- firecrawl-mcp 3.7.2 -> 3.9.0 (1): firecrawl_browser_execute runs code in a remote Firecrawl browser session, not on the host; EXEC -> SECRET overclaims. False.

## Readings
1. Coarse set-level semantics saturate: 84% of eligible multi-server configs are UNSAFE before any update, so per-provider contracts would be empty for them.
2. Real composition-changing updates exist but are rare and concentrated: one upstream change accounts for all adjudicated flips.
3. Update noise is large and robust: ~23 surface-change alerts per config over ~18 months; >= 86 alerts per real verdict change.
4. Auto-update is the default: 91% of stdio entries launch without a version pin.
5. Where code runs (host vs browser vs remote sandbox) and what a URL/table parameter reaches decide verdicts; name/schema rules cannot see either.
