"""interlock profile npm:@scope/pkg  |  interlock batch packages.txt"""
from __future__ import annotations
import argparse, hashlib, json, re, sys, time
from pathlib import Path
from src.summarize.adjudicate import batch_request, is_openai_model, parse_effects, usage_dict, MAX_OUTPUT_TOKENS, USAGE_FIELDS
from src.summarize.pipeline import build_profile, evidence_kind_counts, prepare_sources, verify
from src.summarize.source import source_digest
from src.summarize.surface import load_surface

PRICE_PER_MTOK = {"claude-opus-5": {"input": 5.00, "output": 25.00, "cached": 0.50},
                  "gpt-4o-2024-08-06": {"input": 2.50, "output": 10.00, "cached": 1.25},
                  "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cached": 0.20},
                  "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cached": 0.20}}

def estimate_cost_usd(usage: dict, model: str = "claude-opus-5", batch: bool = False) -> float:
    p = PRICE_PER_MTOK.get(model)
    if p is None:  # a model without a price entry: tokens are recorded, the cost is not estimated
        return 0.0
    dollars = (usage.get("input_tokens", 0) * p["input"]
               + usage.get("cache_creation_input_tokens", 0) * p["input"] * 1.25
               + usage.get("cache_read_input_tokens", 0) * p["cached"]
               + usage.get("output_tokens", 0) * p["output"]) / 1_000_000
    return round(dollars * (0.5 if batch else 1.0), 6)

def make_client(model: str = "claude-opus-5"):
    if is_openai_model(model):
        import openai
        return openai.OpenAI()
    import anthropic
    return anthropic.Anthropic()

def _split(spec: str) -> tuple[str, str]:
    kind, _, package = spec.partition(":")
    return kind, package

def _slug(profile) -> str:
    return f"{profile.kind}_{profile.package.replace('/', '_').lstrip('@')}_{profile.version}"

def _write(out_dir: Path, profile, stats, raw: dict | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(profile)
    (out_dir / f"{slug}.json").write_text(profile.to_json())
    if raw:
        (out_dir / f"{slug}.raw.json").write_text(json.dumps(raw, indent=1))   # the model's answers before the checker
    with (out_dir / "stats.jsonl").open("a") as f:
        f.write(json.dumps({"package": profile.package, "version": profile.version, **stats}) + "\n")

def cmd_profile(args) -> int:
    kind, package = _split(args.package)
    client = make_client(args.model)
    usage_out: dict = {}
    raw_out: dict = {}
    start = time.monotonic()
    profile, stats = build_profile(package, args.version, kind, Path(args.surfaces), Path(args.cache),
                                   client=client, source_ref=args.source_ref, usage_out=usage_out, model=args.model,
                                   source_version=args.source_version, temperature=args.temperature, raw_out=raw_out)
    stats["wall_seconds"] = time.monotonic() - start
    stats["usage"] = usage_out
    stats["model"] = args.model
    stats["cost_usd"] = estimate_cost_usd(usage_out, model=args.model, batch=False)
    _write(Path(args.out), profile, stats, raw_out)
    print(f"{package}@{profile.version}: {stats['verified']}/{stats['claims']} claims verified, "
          f"{stats['demoted']} demoted, {len(stats['missing_tools'])} tools unjudged, "
          f"${stats['cost_usd']:.4f}")
    return 0

def _custom_id(kind: str, package: str) -> str:
    """Batches API custom_id: letters/digits/_/- only, <=64 chars. Sanitise, then append a
    hash suffix of the untruncated kind:package so truncation can't collide two long ids."""
    base = re.sub(r"[^A-Za-z0-9_-]", "-", f"{kind}_{package}")
    suffix = hashlib.sha256(f"{kind}:{package}".encode()).hexdigest()[:8]
    return f"{base[:64 - len(suffix) - 1]}-{suffix}"

def _manifest_path(out_dir: Path) -> Path:
    return out_dir / "batch-manifest.json"

def _write_manifest(path: Path, entries: list[dict], batch_id: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"batch_id": batch_id, "packages": entries}, indent=1))

def _failures_path(out_dir: Path) -> Path:
    return out_dir / "batch-failures.jsonl"

def _append_failures(out_dir: Path, batch_id: str | None, stage: str, failures: list[dict]) -> None:
    """Append one JSON object per line to <out>/batch-failures.jsonl, never overwriting
    what an earlier run in the same output directory already recorded."""
    if not failures:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    with _failures_path(out_dir).open("a") as f:
        for entry in failures:
            f.write(json.dumps({"batch_id": batch_id, "stage": stage, **entry}) + "\n")

def _prepare_batch(specs: list[str], surfaces_path: Path, cache_dir: Path,
                   model: str = "claude-opus-5") -> tuple[list, list[dict], list[dict]]:
    """Load each surface, fetch its source, and build the batch request plus a manifest
    entry recording enough to resume: custom_id, kind, package, version, resolved root.
    A package whose surface lookup, source fetch, validation or request-building fails is
    recorded in the returned failures list (package, custom_id, message) and excluded, so
    one bad top-level package does not stop the rest of the batch from being submitted.
    A manifest entry is added only once the request it describes has actually been built
    and queued, so a package appears in the manifest if and only if it was submitted -
    never a manifest entry with no matching request (e.g. batch_request's MAX_TOOLS_CHARS
    guard raising after the entry would otherwise have been recorded)."""
    seen: dict[str, str] = {}
    manifest_entries = []
    requests = []
    failures = []
    for spec in specs:
        kind, package = _split(spec)
        cid = None
        try:
            surface = load_surface(surfaces_path, package)
            root, files, notes = prepare_sources(kind, package, surface["version"], cache_dir,
                                                 [t["name"] for t in surface["tools"]],
                                                 source_ref=surface.get("source_ref"),
                                                 source_version=surface.get("source_version"))
            cid = _custom_id(kind, package)
            if cid in seen:
                raise ValueError(f"duplicate custom_id {cid!r} for {seen[cid]!r} and {spec!r}")
            seen[cid] = spec
            requests.append(batch_request(cid, surface, files, model))
            code_files_shown, doc_files_shown = evidence_kind_counts(files)
            # An absolute root and a content digest, both recorded now while the view still
            # exists: a resume that reads this manifest may run from a different working
            # directory, long after the cache was rebuilt or cleaned, so "root" must not
            # depend on cwd and a rebuilt-but-different tree at the same path must be caught
            # rather than silently re-verified against the wrong source (I1).
            manifest_entries.append({"custom_id": cid, "kind": kind, "package": package,
                                      "version": surface["version"], "root": str(Path(root).resolve()),
                                      "digest": source_digest(root), "notes": notes,
                                      "code_files_shown": code_files_shown, "doc_files_shown": doc_files_shown})
        except Exception as e:
            print(f"{spec}: preparation failed: {e}", file=sys.stderr)
            failures.append({"package": package, "custom_id": cid, "message": str(e)})
    return requests, manifest_entries, failures

def _poll(client, batch_id: str) -> None:
    """Poll until the batch ends, printing progress. Tolerates transient retrieve() errors
    (network blip mid multi-hour run) instead of dying and losing an already-billed batch."""
    while True:
        try:
            status = client.messages.batches.retrieve(batch_id)
        except Exception as e:
            print(f"batch {batch_id}: poll error, retrying: {e}", file=sys.stderr)
            time.sleep(30)
            continue
        print(f"batch {batch_id}: {status.processing_status} {getattr(status, 'request_counts', None)}")
        if status.processing_status == "ended":
            return
        time.sleep(30)

def cmd_batch(args) -> int:
    if is_openai_model(args.model):
        print("batch mode uses the Anthropic Batches API; run `profile` per package for an OpenAI model", file=sys.stderr)
        return 2
    client = make_client()
    out_dir = Path(args.out)
    manifest_path = _manifest_path(out_dir)
    prepare_failures: list[dict] = []

    if args.resume:
        manifest = json.loads(manifest_path.read_text())
        batch_id = manifest["batch_id"]
        if not batch_id:
            print(f"{manifest_path}: no batch id recorded, nothing to resume", file=sys.stderr)
            return 1
        entries = {e["custom_id"]: e for e in manifest["packages"]}
    else:
        specs = [l.strip() for l in Path(args.packages).read_text().splitlines() if l.strip() and not l.startswith("#")]
        requests, manifest_entries, prepare_failures = _prepare_batch(specs, Path(args.surfaces), Path(args.cache), args.model)
        _write_manifest(manifest_path, manifest_entries, batch_id=None)
        _append_failures(out_dir, None, "prepare", prepare_failures)
        batch = client.messages.batches.create(requests=requests)
        _write_manifest(manifest_path, manifest_entries, batch_id=batch.id)
        prep_note = f", {len(prepare_failures)} package(s) failed preparation" if prepare_failures else ""
        print(f"batch {batch.id} with {len(requests)} requests{prep_note}")
        batch_id = batch.id
        entries = {e["custom_id"]: e for e in manifest_entries}

    poll_start = time.monotonic()
    _poll(client, batch_id)
    batch_wall_seconds = time.monotonic() - poll_start

    written = 0
    totals = {f: 0 for f in USAGE_FIELDS}
    total_cost = 0.0
    failures: list[dict] = []
    for result in client.messages.batches.results(batch_id):
        entry = None
        try:
            entry = entries.get(result.custom_id)
            if entry is None:
                print(f"{result.custom_id}: not in manifest, skipping", file=sys.stderr)
                continue
            if result.result.type != "succeeded":
                raise RuntimeError(f"batch result type {result.result.type!r}, not succeeded")
            if result.result.message.stop_reason == "max_tokens":
                # Caught here, before parsing, so a truncated response is reported as what
                # it is rather than surfacing as a confusing "invalid JSON" error.
                raise ValueError(f"{entry['package']}: output truncated at max_tokens={MAX_OUTPUT_TOKENS}")
            root = Path(entry["root"])
            if not root.is_dir():
                raise ValueError(f"{entry['package']}: source root missing: {root}")
            digest = source_digest(root)
            if entry.get("digest") is not None and digest != entry["digest"]:
                # The tree at this path is not the one the model actually saw (a rebuilt
                # view, a same-path different-version fetch): re-verifying against it would
                # silently check evidence against the wrong source, not against nothing (I1).
                raise ValueError(f"{entry['package']}: source root changed since preparation "
                                 f"(digest {entry['digest'][:12]} -> {digest[:12]})")
            surface = load_surface(Path(args.surfaces), entry["package"], entry["version"])
            text = next(b.text for b in result.result.message.content if b.type == "text")
            profile, stats = verify(parse_effects(text, entry["package"]), surface, root)
            profile.notes = list(profile.notes) + entry.get("notes", [])
            stats["code_files_shown"] = entry.get("code_files_shown")
            stats["doc_files_shown"] = entry.get("doc_files_shown")
            usage = usage_dict(result.result.message.usage)
            stats["usage"] = usage
            stats["cost_usd"] = estimate_cost_usd(usage, model=args.model, batch=True)
            stats["batch_wall_seconds"] = batch_wall_seconds
            _write(out_dir, profile, stats)
            written += 1
            for f in USAGE_FIELDS:
                totals[f] += usage[f]
            total_cost += stats["cost_usd"]
            print(f"{profile.package}@{profile.version}: {stats['verified']}/{stats['claims']} verified")
        except Exception as e:
            # Anything that can go wrong handling one result - no text block (StopIteration),
            # a malformed model response (verify can raise ValueError/KeyError/TypeError), a
            # non-"succeeded" result type (errored/canceled/expired) - is recorded as a
            # failure for this one package, never lets a paid batch run stop halfway.
            pkg = entry["package"] if entry is not None else None
            print(f"{result.custom_id}: {e}", file=sys.stderr)
            failures.append({"package": pkg, "custom_id": result.custom_id, "message": str(e)})
    _append_failures(out_dir, batch_id, "result", failures)
    all_failures = prepare_failures + failures
    names = ", ".join(f["package"] for f in all_failures if f.get("package"))
    failed_note = f", {len(all_failures)} failed" + (f" ({names})" if names else "") if all_failures else ""
    print(f"batch {batch_id}: {written} packages written{failed_note}, tokens "
          f"in={totals['input_tokens']} out={totals['output_tokens']} "
          f"cache_creation={totals['cache_creation_input_tokens']} cache_read={totals['cache_read_input_tokens']}, "
          f"cost_usd={total_cost:.4f}")
    return 0

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="interlock")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("profile", "batch"):
        s = sub.add_parser(name)
        s.add_argument("package" if name == "profile" else "packages")
        s.add_argument("--surfaces", default="benchmark/mcpwild/surfaces.jsonl")
        s.add_argument("--cache", default=".cache/sources")
        s.add_argument("--out", default="results/summarize/mcpwild")
        s.add_argument("--model", default="claude-opus-5", help="claude-* (Anthropic) or gpt-*/o* (OpenAI)")
        s.add_argument("--temperature", type=float, default=None, help="sampling temperature for an OpenAI model (default: the API's)")
        if name == "profile":
            s.add_argument("--version", default=None)
            s.add_argument("--source-ref", default=None, help="git URL when kind is git")
            s.add_argument("--source-version", default=None, help="git tag holding the source, when it differs from --version")
        else:
            s.add_argument("--resume", default=None, metavar="BATCH_ID",
                           help="skip preparation/submission; poll and write results for an already-submitted batch")
    args = p.parse_args(argv)
    return cmd_profile(args) if args.cmd == "profile" else cmd_batch(args)

if __name__ == "__main__":
    raise SystemExit(main())
