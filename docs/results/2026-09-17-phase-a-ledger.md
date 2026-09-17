# SDD ledger — plan: docs/superpowers/plans/2026-09-16-phase-a-provider-effect-profiles.md

Spec: docs/superpowers/specs/2026-09-16-interlock-design.md (read).
Ruling: work happens on branch `phase-a-profiles` in the main working tree rather than a git worktree — the repo was initialised today, no concurrent work exists, and the tasks read multi-MB corpus files already checked out here. Cost if wrong: a dirty main tree during the run, undone by `git checkout main`.

## Pre-flight scan

| Row | Checked | Finding |
|---|---|---|
| T1 self | schema tests vs code | consistent; `ToolEffect.validate()` returns self so the test's `.validate()` call raises as asserted |
| T2 self | fetch tests vs code | consistent; cache short-circuit precedes any runner call, matching `test_fetch_is_cached` |
| T3 self | citation tests vs code | consistent; widen=2 makes `server.js:1` miss line 3, matching the negative assertion |
| T4 self | surface tests vs code | consistent; `ok: false` rows filtered before sorting, matching `latest_version` expectation |
| T5 self | adjudicate tests vs code | consistent; `thinking` absent from params as the test asserts |
| T6 self | select tests vs code | consistent; budget test allows 1-3 files, code breaks after budget with at least one file kept |
| T7 self | pipeline tests vs code | consistent; demotion keeps labels, unjudged tools get HOSTEXEC |
| T8 self | regression fixtures | expectations copied from P1 outputs; Step 2 explicitly runs it to confirm |
| T9 self | CLI tests vs code | consistent; `make_client` is monkeypatched at `interlock.cli.make_client`, which the code defines |
| T10/T11 self | prereg then run | ordering correct: criteria fixed before measurement |
| T1→T3,T7,T9 | `ToolEffect`/`Profile` fields | same field names and order used throughout |
| T2→T7,T9 | `fetch_source` signature | `(kind, package, version, cache_dir, run=...)`; T7 calls it positionally with 4 args, T9 via T7 |
| T3→T7 | `check_tool` return | `(bool, str)`; T7 unpacks two values |
| T4→T5,T7,T9 | `load_surface` dict shape | keys package/version/kind/tools used identically |
| T5→T7,T9 | `adjudicate` / `batch_request` | T7 calls `adjudicate(surface, files, client=...)`; T9 calls `batch_request(cid, surface, files)` |
| T6→T5,T7 | `select_files` return | `(rel, text)` pairs consumed by `build_messages` and `build_profile` |
| Global constraints | no execution; labels; evidence; model id; caching | tasks 2, 5, 7 carry them; reviewer prompts will repeat them verbatim |

Ruling: T2's `fetch_source` takes `package` as the git URL when kind is `git`; T7 exposes this as `source_ref`. Recorded because the brief for T7 mentions `source_ref` while T2's brief does not. Cost if wrong: serena (a git-sourced server) profiles under the wrong identity, caught by its profile filename.

Ruling: tests run as `uv run --with pytest python -m pytest ...` — the system python3 has no pytest and uv is installed. Carried into every later dispatch. Cost if wrong: none beyond a different runner command in reports.
Ruling: `uv.lock` is git-ignored — this is a research prototype with a single loose dependency, so a lockfile adds noise without protecting a release. Cost if wrong: dependency drift between machines, recoverable by committing the lock later.
Task 1: complete (commits d4b15a4..afacbcc, review pending)
Task 1: fix round 1/5 (1 addressed, 0 open — validate() now runs in __post_init__; commits afacbcc..e56c143)
Task 1: complete (commits d4b15a4..e56c143, review clean after round 1)
Task 1: minor (deferred): to_json duplicates asdict's recursion; no round-trip test with non-empty value_conditions/notes
Task 2: review — spec OK, 3 Important (zip symlink escape, .work_* left on failed fetch, multi-dir wheel root selection); entering fix round 1
Task 3: implemented with a plan defect corrected. Ruling: the brief's reference code contradicted the brief's own tests — a symmetric two-line widen makes a single-line citation of the wrong line verify. The implementer restricted the widen to explicit multi-line ranges, so single-line citations must hit exactly. Accepted: the checker is the trust anchor, and a checker that accepts a wrong line is worse than one that is strict. Cost if wrong: genuine claims whose citation is off by a line or two are demoted to undetermined, which is the safe direction and shows up as a lower verified-claim rate at Gate A.
Task 3: minor (deferred): no test yet for the widen applied to a multi-line range citation
Task 2: fix round 1/5 (3 addressed pending re-review; commits 760430d..f15527a)
Task 2: fix round 1/5 (3 addressed, 0 open; commits 6eee08e..f15527a)
Task 2: complete (commits afacbcc..f15527a, review clean after round 1)
Task 2: minor (deferred): zip guard drops every symlink instead of keeping in-bounds ones; traversal half of the new zip test is redundant because CPython's zipfile already strips '..'; tar-path containment untested (covered by filter="data")
Task 3: review — spec OK, 1 Critical (citation paths escape the source root) and 2 Important (rglob fallback ambiguity, untested range widen); fix round 1 dispatched
Task 3: fix round 1/5 (3 addressed, 0 open; commits 6eee08e..63d78e7)
Task 3: complete (commits 760430d..63d78e7, review clean after round 1)
Task 4: implemented (commit b6286c3), real-data check: @playwright/mcp 40 versions, latest 0.0.81, 26 tools; review dispatched
Task 5: implemented (commit 029048e), rubric and schema copied verbatim; review pending
Task 4: review — spec OK, 2 Important (no memoisation of an 11MB JSONL re-read per package; missing-version KeyError untested); fix round 1 dispatched
Task 4: minor (deferred): lexicographic published sort (verified safe across all 854 rows); KeyError wording does not separate "never recovered" from "all attempts failed"
Task 6: implemented (commit af0a08b); ranking tuple's text element shown unreachable because paths are unique
Task 4: fix round 1/5 (2 addressed pending re-review; commits b6286c3..2749fbe, memoisation 52ms -> 0.055ms)
Task 5: review — spec OK (rubric byte-identical), 3 Important. Ruling: the cache-placement finding needs no code change — one call judges all tools of a package, so the source breakpoint pays only on retries and re-runs, which is still free when it misses. Cost if wrong: a little cache spend that never converts, visible as cache_read_input_tokens staying at zero. Truncation and the unguarded next() entered fix round 1.
Task 6: review — spec OK, 1 Critical (a minified bundle such as playwright-core's coreBundle.js outranks every real source file and its first 120k chars eat a third of the budget) and 3 Important (unanchored `test_` marker drops files like latest_release.go; `.snap` files excluded by the suffix allowlist, which hides github-mcp-server's toolsnaps; raw occurrence counting lets README files outrank implementations).
Ruling: do not fix the Critical by skipping large files — for @playwright/mcp the bundle IS the implementation, and the P1 hand run found its evidence there. Fix by excerpting windows around tool-name occurrences with their real line numbers, so the model sees the relevant code and cited lines still resolve in the checker. Cost if wrong: the model sees less context per file and some claims fail verification, which shows up as a lower verified-claim rate rather than as a wrong label.
Task 5: fix round 1/5 (2 addressed pending re-review, 1 ruled no-change; commits 029048e..ccdc7e4)
Task 4: fix round 2/5 dispatched — re-review found the memoisation returns cache-shared mutable lists
Task 5: fix round 1/5 (2 addressed, 0 open; rubric confirmed byte-identical; commits 029048e..ccdc7e4)
Task 5: complete (commits b6286c3..ccdc7e4, review clean after round 1)
Task 4: fix round 2/5 (1 addressed, 0 open; deepcopy at the return boundary, 0.35ms/call; commits ccdc7e4..6c7ec8b)
Task 4: complete (commits 63d78e7..6c7ec8b, review clean after round 2)
Task 6: fix round 1/5 dispatched (excerpt windows with true line numbers, anchored test markers, .snap allowed, distinct-name ranking)
Task 6: fix round 1/5 (4 addressed pending re-review; commits 6c7ec8b..e546b0f)
Task 7: implemented (commit 1a96c35); note: tools judged with empty labels count in stats[tools] but not in claims
Task 6: fix round 1/5 (4 addressed, 0 open; line-number arithmetic hand-verified; commits 6c7ec8b..e546b0f)
Task 6: complete (commits 029048e..e546b0f, review clean after round 1)
Task 6: minor (deferred): the excerpt test recomputes the implementation's own line formula instead of deriving it independently; densely clustered hits can merge into one window that crowds out other regions
Task 8: complete (commit 767c95d, 5/5 P1 unions matched the fixtures on first run, no fixture edits)
Task 8: review clean (1 minor: fixture path is relative to cwd)
Task 7: review — spec OK, 1 Critical (tools the model clears with empty labels skip verification and vanish from the stats) + 2 Important (hallucinated tool names dropped; label validation error lacks package/tool context); fix round 1 dispatched
Ruling: Gate A's pre-registration (Task 10) must define the published rate over claims AND cleared judgements, because a cleared tool is a judgement too. The PREREG text in the plan will be amended when Task 10 is dispatched. Cost if wrong: a rate that looks higher than the evidence supports.
Task 9: implemented (commit 3a19fa4); batch preparation aborts wholesale if one package's fetch fails
Task 9: review — spec OK, 1 Critical (no resumability: batch id and the custom_id mapping exist only in memory, so a crash re-bills the run) + 2 Important (custom_id keeps dots, which the Batches API rejects, and nothing guards id collisions); fix round 1 dispatched
Ruling: the Gate A computation in Task 11 must deduplicate stats.jsonl by (package, version), keeping the last row, because reruns append. Cost if wrong: pooled claims and verified double-count a rerun package and the gate reads high.
Task 7: fix round 1/5 (4 addressed, 0 open; partition of every surface tool into exactly one stats bucket verified by walk; commits 3a19fa4..718d664)
Task 7: complete (commits e546b0f..718d664, review clean after round 1)
Task 7: minor (deferred): no test isolates the anti-aliasing fix; the CLI's printed line still omits cleared_unverified and unknown_tools, though stats.jsonl carries them
Ruling: Gate A's criteria will name four rates, not one — verified/claims, cleared_unverified/tools, missing/tools and unknown/tools — because the re-review confirmed that verified/claims alone can look healthy while a package is full of unverifiably cleared tools. Cost if wrong: a gate that passes on a package whose dangerous tools were waved through.
Task 9: fix round 1/5 (4 addressed, 0 open; manifest written before create, id sanitised with an 8-hex suffix over the untruncated spec, duplicate guard, poll progress; commits 718d664..8f36d0f)
Task 9: complete (commits 767c95d..8f36d0f, review clean after round 1)
Task 9: minor (deferred): the poll loop retries a permanent failure (bad id, revoked key) forever; manifest stores absolute paths; the unused positional is still required with --resume
Task 10: complete (commit 4704a37, Gate A pre-registered with four rates)
Task 11: step 1 complete (commit 7e0ba9e; 34 packages, 433 tools). User approved spend up to about USD 30 and supplied ANTHROPIC_API_KEY in .env; .env git-ignored (commit after 4704a37) and confirmed never committed.
Ruling: the CLI recorded no token usage, so the pre-registered cost criterion (A3) had no data source. Added usage and cost capture to the stats rows before any paid call, rather than reading cost off the console afterwards. Cost if wrong: none to the measurement; one extra review cycle before the run.
Usage-capture change: review spec OK, 1 Important (cost test mirrors the implementation's formula); reviewer's independent hand check matched ($0.0189, batch $0.00945); test-only fix dispatched.
Task 11: dry-run BLOCKED — API returned 400 "credit balance is too low". Key authenticates; nothing billed, nothing written. Waiting on the user to add credits. .venv added to .gitignore (commit pending until the test fix lands).
Usage-capture change: complete (commits 7e0ba9e..db2bd17, test pinned with hardcoded figures; 60 passed)
Task 11: credits added; second dry-run rejected with 400 — structured outputs require additionalProperties false on every object, and EFFECT_SCHEMA keyed tools by name with additionalProperties {schema}. Nothing billed.
Ruling: express tools as an array of objects with a `name` field, and convert back to a name-keyed dict in a shared parse_effects() so verify() and every caller keep their interface. The RUBRIC text is unchanged. Cost if wrong: none to the measurement instrument; a duplicate judgement for the same tool keeps the first one and is noted.
Task 11: dry-run 1 (schema fixed, commit c5620aa) — server-filesystem@2026.8.31: union {SECRET} matches the P1 fixture; 6/11 claims verified, 5 demoted, 2 cleared_verified, 1 cleared_unverified; cost USD 0.2451 non-batch (23,048 cache-write, 3,110 input, 3,421 output tokens). Stats row preserved at docs/results/2026-09-17-dryrun1-filesystem-stats.jsonl.
Ruling: the demotions are an instrument defect, not model error — the prompt showed source without line numbers, and the cited lines drift upward with depth (354->412, 438->512, 513->602, 536->632, 558->657, the last two past EOF). Fix by numbering every source line in the prompt with its true number; the checker is not loosened and the PREREG thresholds are untouched. Recorded as a pre-batch deviation found by the dry-run the plan prescribed. Cost if wrong: if line numbering does not raise the verified rate, the low rate is genuine and Gate A reads it as such.
Task 11: dry-run 2 (line numbers, commit 026e0ab) — server-filesystem: 11/11 verified, 0 demoted, 3 cleared_verified, union {SECRET} matches P1; USD 0.2648.
Task 11: dry-run 3 — @playwright/mcp@0.0.81: 25/25 "verified", union matches P1, USD 0.4582; but all 39 citations point at README.md because the package is a thin wrapper over playwright-core.
Ruling: count documentation-only evidence separately (verified_doc) and never let documentation justify clearing a tool, because the rubric says judge from code and the spec's trust rule forbids prose from lowering an effect. The primary verified-claim rate counts code evidence only. Cost if wrong: a stricter rate than a reader might expect, stated in the deviation note.
Ruling: for npm packages whose own code does not mention a tool name, fetch the direct dependencies that do and show their code to the model, because the implementation of wrapper packages lives there (P1 found Playwright's evidence in playwright-core). PyPI dependency following is out of scope for Phase A and reported as a limitation. Cost if wrong: extra download time and a larger prompt for wrapper packages.
Ruling: none of the three instrument fixes (line numbers, documentation class, dependency following) changes a PREREG threshold; all three are recorded in a dated deviation note before the at-scale run.
Review of schema + line-number fixes (019c71b..026e0ab): spec OK, RUBRIC byte-identical, line numbering hand-traced correct, budget arithmetic correct, recursive schema test genuine. 2 Important: parse_effects raises bare KeyError/TypeError on malformed model JSON with no package named, and the batch loop does not catch it per package; a long single line shows one slice around the first hit, dropping evidence elsewhere on that line.
Queue (sequential, shared files): (1) documentation evidence class [running]; (2) parse_effects robustness + per-package catch in the batch loop; (3) one slice per hit on long single lines; (4) npm dependency following. Then review, Playwright dry-run 4, deviation note, batch.
Minor (deferred): the final MAX_FILE_CHARS slice cuts without a marker; select_files keeps the first file even if it alone exceeds budget_bytes (cannot trigger while MAX_FILE_CHARS < budget_bytes).
Fix (1) documentation evidence class: done (commit f255e6d, 72 passed); review pending. Fix (2) parse_effects robustness dispatched.
Fix (2) parse_effects robustness + per-package catch: done (commit a6dca6d, 77 passed). Fix (3) long-line slices dispatched.
Fix (3) long-line slices: done (commit 1721872, 79 passed). Fix (4) npm dependency following dispatched, with a shared prepare_sources() used by profile and batch paths.
Fix (4) dependency following: done (commit e97c618, 87 passed). Review of fixes 1-4 (026e0ab..e97c618) dispatched; dry-run 4 on @playwright/mcp running.
Review of fixes 1-4 (026e0ab..e97c618): 1 Critical (untrusted version strings reach the cache slug and rmtree, and npm pack accepts git/file/http specs without --ignore-scripts; flag injection via names) + 4 Important (cache mutation via .deps; detection and selection use different file filters; batch loop still stops on StopIteration/KeyError/TypeError and ignores non-succeeded results; PREREG unaware of verified_doc, and doc-only claims keep undetermined=False, which lowers effects).
Confirmed no damage from dry-runs: no tracked file missing, Playwright's dependencies were exact registry versions.
Ruling: the security fix went to a fresh implementer on the most capable model rather than resuming the author of the vulnerable code. Cost if wrong: a slower fix; no correctness cost.
Security fix: done (commit eea0f99, 111 passed). Validates npm/PyPI/git specs before any filesystem or subprocess use, adds --ignore-scripts and a -- separator, rejects `x@.` and `x@*.tgz` as local specs, caches deps by resolved version, and builds a fresh per-run view instead of mutating the cache. Stale .deps removed from the cached playwright tree. Open from its report: cache-slug collisions (@a/b vs a_b), unbounded npm pack attempts, one bad top-level package aborts batch preparation.
Security re-review (opus, adversarial, on the working tree): all four original vulnerabilities CLOSED; slug collision CLOSED; could not break no-execute or no-write-outside-cache. Minor (deferred): unbounded npm pack attempts for a package.json listing many deps; no length cap on npm version, git ref or PyPI name/version; PyPI version accepts `..` (not exploitable).
Pre-run fixes I2/I3/I4 + slug hash: done by implementer but not committed (the dispatch omitted "commit"); controller verified 119 passed and committed as 47ec195. Review dispatched.
Deviation note committed (fc87b60) before the at-scale run: D1 schema, D2 line numbers, D3 documentation bucket (verified = code-verified only; stricter), D4 dependency following, D5 security hardening, D6 failed packages count as not usable. Thresholds unchanged. Dry-run spend USD 1.66.
Review of 47ec195: spec OK on I2/I3/I4/slug; 1 Important (manifest entry appended before batch_request can still raise, leaving a never-submitted custom_id); fix dispatched.
Minor (deferred): profile written before the stats append, so a failed append leaves a profile with no stats row; stale failure lines persist after a successful resume (gate reads stats only); `test_` filename rule would skip a real implementation file named test_runner.py.
Task 11: ghost manifest fix done (1b917e4, 120 passed). Dry-run profiles moved to .cache/dryrun-profiles so no stale row can stand in for a failed package. Batch starting on 34 packages.
Task 11: batch msgbatch_01N1jyzikYCL5wBGB47EE2Zc submitted at about 05:05Z with 34 requests, 0 preparation failures; batch id persisted in profiles/batch-manifest.json (resumable).
Task 11: user asked to cancel and run synchronously. Cancel request found the batch had just finished 32 of 34 (2 canceled). --resume collected results: 30 packages written, 4 failed — 2 canceled (mcp-server-git, server-postgres) and 2 truncated JSON (chrome-devtools-mcp 29 tools, @azure/mcp 61 tools). Batch cost for written packages USD 4.7237 (in 149,759 / out 150,548 / cache write 771,426 / cache read 226,902 tokens).
Ruling: truncation is an output-budget limit, not model error — adaptive thinking draws on max_tokens=16000. Raise to 64000, stream on the synchronous path, and report stop_reason=max_tokens as truncation. The 30 written packages completed within the old budget, so they are unaffected. Recorded as deviation D7 before the gate is computed. Cost if wrong: a few dollars of extra output tokens on the four reruns.
Observation for the gate analysis (not acted on): @notionhq/notion-mcp-server 0/14 verified and supabase 10/19 stand out against near-100% elsewhere; inspect before reading the gate.
Task 11: Gate A computed exactly as registered (with deviations D1-D7 recorded first): branch A2 — verified-claim 0.725, unverified-clear 0.076, unjudged 0, hallucinated 0.005, usable 27/34 (sensitivity 30/34), cost USD 8.53. Results committed. Final whole-branch review dispatched.
Final whole-branch review (opus): no Critical. Independently recomputed the gate (0.725 / 0.076 / 0 / 0.005 / 27 of 34) and re-ran verify on all 34 profiles against current views with identical stats. 3 Important: I1 missing view silently demotes a package (fix before merge); I2 unverified clears understate effects in Profile.union (fix before Phase B); I3 no record of whether code reached the model — excluding the two no-code packages gives 243/268 = 0.907 (add stats fields, report sensitivity). Minors M1-M6 and ledger triage recorded in the review; promoted: manifest stores relative, not absolute, roots (folded into I1).
Ruling: I2 is fixed in Profile.union with a conservative default rather than by rewriting recorded labels, so the stored profile remains a faithful record of what the model claimed and what verified, and every consumer gets the least-restrictive reading by default. Cost if wrong: consumers wanting literal labels must pass conservative=False.
Final review fix wave: re-review clean (I1, I2, I3 ADDRESSED; commits 38cc4ab..91bdad1, 130 passed). Phase A complete.
