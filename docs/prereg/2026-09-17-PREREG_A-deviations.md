# Gate A pre-registration: deviations recorded before the at-scale run

Date: 2026-09-17. Applies to `docs/prereg/2026-09-16-PREREG_A.md`. Written before any at-scale profiling run; only the dry-runs the plan prescribes (Task 11, Step 2) had been executed.

No threshold in the pre-registration changes. What changed is the measurement instrument and the definition of one bucket. Every change below was found by a dry-run on one or two packages, and each is recorded with the observation that motivated it. Dry-run statistics rows are preserved in `docs/results/2026-09-17-dryrun*-stats.jsonl`.

## D1. Structured-output schema (instrument)

Observation: the first real request was rejected with HTTP 400 because structured outputs require `additionalProperties: false` on every object, and the effect schema keyed tools by name. Nothing was billed.
Change: tools are an array of objects carrying `name`, converted back to a name-keyed mapping before verification. The rubric text is unchanged.

## D2. Line numbers shown to the model (instrument)

Observation: on `@modelcontextprotocol/server-filesystem@2026.8.31` the model's labels matched the hand adjudication exactly, but 5 of 11 claims failed verification because cited line numbers drifted with depth (tools defined at lines 354, 438, 513, 536, 558 were cited at 412, 512, 602, 632, 657; the last two past the end of the file). The prompt showed source without line numbers.
Change: every source line is shown prefixed with its true line number, and the model is told to cite those numbers. The checker was not loosened.
Effect on that package: 6/11 verified before, 11/11 after.

## D3. Documentation evidence is its own bucket (definition)

Observation: on `@playwright/mcp@0.0.81`, a thin wrapper, all 25 claims verified, but all 39 citations pointed at README.md. The checker confirmed that the cited lines existed and named the tool; it did not distinguish documentation from code. The rubric says judge from code, and the spec's trust rule says free text may raise an effect but never lower one.
Change:
- A citation into `.md`, `.markdown`, `.rst`, `.txt`, `.adoc`, or a file named README*, CHANGELOG* or LICENSE* is documentation; anything else is code.
- A labelled claim whose evidence verifies only through documentation goes to a new bucket `verified_doc`, keeps its labels, and is marked undetermined (a documentation-backed label cannot rule out further effects).
- A cleared tool counts as `cleared_verified` only with code evidence; documentation-only or unverifiable clears count as `cleared_unverified`.

Amended accounting (replaces the split stated in the pre-registration's Definitions section):
- `claims = verified + verified_doc + demoted`
- `tools = claims + cleared_verified + cleared_unverified + len(missing_tools)`

The pre-registered **verified-claim rate remains `verified / claims`**, and `verified` now means code-verified only. Documentation-only claims therefore count against the rate. This is the stricter reading and is adopted deliberately. `verified_doc / claims` is reported alongside it.

## D4. Dependency following for npm wrapper packages (instrument)

Observation: the Playwright MCP package ships no implementation; every tool is implemented in its dependency `playwright-core`, where the earlier hand adjudication found its evidence.
Change: for an npm package, if a tool name appears in none of the package's own candidate source files, direct dependencies whose candidate source files mention a missing name are fetched (npm pack with scripts disabled, at most five kept, no recursion) and shown to the model under `.deps/`. The profile notes record each included dependency as `name@resolved-version`. PyPI packages are not followed; this is a stated limitation.
Effect on that package: citations moved from README.md only (39) to `playwright-core/lib/coreBundle.js` (31) and README.md (13); 20 of 25 claims code-verified, 4 documentation-only, 1 demoted; label union unchanged and equal to the hand adjudication.

## D5. Security hardening (no effect on measurement)

A review found that untrusted package names and version ranges could reach the cache path and `npm pack` unfiltered, allowing deletion outside the cache and lifecycle-script execution. Specs are now validated before any filesystem or subprocess use, `--ignore-scripts` and `--` are always passed, cache slugs are hashed, and each run assembles a fresh source view instead of mutating the cache. An adversarial re-review found the four original issues closed.

## D6. Failed packages (definition)

Every package in the list is either profiled or recorded in `profiles/batch-failures.jsonl` with its stage (`prepare` or `result`) and message. A package with no profile is **counted as not usable** in the "at least 80% / at least half of packages usable" criteria, so a failure can never improve the gate. Stats rows are deduplicated by (package, version), keeping the last row, as already pre-registered.

## Dry-run spend

Four dry-runs, non-batch pricing: USD 0.2451 + 0.2648 + 0.4582 + 0.6911 = USD 1.66. This counts toward the pre-registered USD 300 ceiling.
