# P0 go/no-go pre-registration (fixed 2026-09-15, before any measurement)

Throwaway spike for direction A (compositional verdict drift). Code here is not the implementation.

## Question
Do real provider updates flip the set-level forbidden-flow verdict of real agent configurations often enough, and rarely enough relative to pin-all alerts, to motivate per-provider update contracts?

## Definitions (v0)
- Config: one committed MCP config file on public GitHub (.cursor/mcp.json, .vscode/mcp.json, .mcp.json, claude_desktop_config.json, .codex/config.toml), with >= 2 servers.
- Server identity: npm/PyPI package name (version stripped), docker image, or remote URL host.
- Labels (rule-based, per tool, from name + inputSchema + annotations): SECRET (reads private data), UNTRUSTED (brings external content into context), SINK (sends data outside), EXEC (arbitrary execution; implies all three).
- Verdict v0: config is UNSAFE iff the union of its servers' labels contains SECRET, UNTRUSTED and SINK (context modelled as universal channel). This is deliberately coarse.
- Flip: a version step of one server turns a SAFE config UNSAFE, all other servers held at the version current at that time.
- Pin-all alert: any version step whose tool surface (name, inputSchema, annotations) differs from the previous version.

## Criteria
- N (inconclusive guard): at least 100 configs with >= 2 servers whose servers all have version-resolved tool surfaces. Otherwise INCONCLUSIVE, not NO-GO.
- G1 observation exists: >= 5% of baseline-SAFE configs experience >= 1 flip over the replay window.
- G2 not saturated: <= 80% of configs are UNSAFE at baseline under v0.
- G3 pin-all over-alerts: pin-all alerts / flips >= 5 over the same configs and window.
- G4 (supporting, not gating): share of stdio server entries launched without an explicit version.

GO iff G1, G2, G3 hold. If G2 fails, record it as evidence that coarse labels saturate (motivates value-level labels) and re-run G1/G3 only after a labeller change is agreed with the user; do not tune labels silently to pass.

## Data handling
Config files are parsed in memory. Stored records keep server identity, launch shape (command, package, version pin yes/no), and repo/path. Env values and any arg that is not a package spec or a path are dropped. No raw config file is written to disk.
