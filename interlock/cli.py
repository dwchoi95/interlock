"""interlock profile npm:@scope/pkg  |  interlock batch packages.txt"""
from __future__ import annotations
import argparse, hashlib, json, re, sys, time
from pathlib import Path
from interlock.adjudicate import batch_request
from interlock.pipeline import build_profile, verify
from interlock.select import select_files
from interlock.source import fetch_source
from interlock.surface import load_surface

def make_client():
    import anthropic
    return anthropic.Anthropic()

def _split(spec: str) -> tuple[str, str]:
    kind, _, package = spec.partition(":")
    return kind, package

def _write(out_dir: Path, profile, stats) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{profile.kind}_{profile.package.replace('/', '_').lstrip('@')}_{profile.version}"
    (out_dir / f"{slug}.json").write_text(profile.to_json())
    with (out_dir / "stats.jsonl").open("a") as f:
        f.write(json.dumps({"package": profile.package, "version": profile.version, **stats}) + "\n")

def cmd_profile(args) -> int:
    kind, package = _split(args.package)
    profile, stats = build_profile(package, args.version, kind, Path(args.surfaces), Path(args.cache),
                                   client=make_client(), source_ref=args.source_ref)
    _write(Path(args.out), profile, stats)
    print(f"{package}@{profile.version}: {stats['verified']}/{stats['claims']} claims verified, "
          f"{stats['demoted']} demoted, {len(stats['missing_tools'])} tools unjudged")
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

def _prepare_batch(specs: list[str], surfaces_path: Path, cache_dir: Path) -> tuple[list, list[dict]]:
    """Load each surface, fetch its source, and build the batch request plus a manifest
    entry recording enough to resume: custom_id, kind, package, version, resolved root."""
    seen: dict[str, str] = {}
    manifest_entries = []
    requests = []
    for spec in specs:
        kind, package = _split(spec)
        surface = load_surface(surfaces_path, package)
        root = fetch_source(kind, package, surface["version"], cache_dir)
        files = select_files(root, [t["name"] for t in surface["tools"]])
        cid = _custom_id(kind, package)
        if cid in seen:
            raise ValueError(f"duplicate custom_id {cid!r} for {seen[cid]!r} and {spec!r}")
        seen[cid] = spec
        manifest_entries.append({"custom_id": cid, "kind": kind, "package": package,
                                  "version": surface["version"], "root": str(root)})
        requests.append(batch_request(cid, surface, files))
    return requests, manifest_entries

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
    client = make_client()
    out_dir = Path(args.out)
    manifest_path = _manifest_path(out_dir)

    if args.resume:
        manifest = json.loads(manifest_path.read_text())
        batch_id = manifest["batch_id"]
        if not batch_id:
            print(f"{manifest_path}: no batch id recorded, nothing to resume", file=sys.stderr)
            return 1
        entries = {e["custom_id"]: e for e in manifest["packages"]}
    else:
        specs = [l.strip() for l in Path(args.packages).read_text().splitlines() if l.strip() and not l.startswith("#")]
        requests, manifest_entries = _prepare_batch(specs, Path(args.surfaces), Path(args.cache))
        _write_manifest(manifest_path, manifest_entries, batch_id=None)
        batch = client.messages.batches.create(requests=requests)
        _write_manifest(manifest_path, manifest_entries, batch_id=batch.id)
        print(f"batch {batch.id} with {len(requests)} requests")
        batch_id = batch.id
        entries = {e["custom_id"]: e for e in manifest_entries}

    _poll(client, batch_id)

    for result in client.messages.batches.results(batch_id):
        entry = entries.get(result.custom_id)
        if entry is None:
            print(f"{result.custom_id}: not in manifest, skipping", file=sys.stderr)
            continue
        if result.result.type != "succeeded":
            print(f"{result.custom_id}: {result.result.type}", file=sys.stderr)
            continue
        surface = load_surface(Path(args.surfaces), entry["package"], entry["version"])
        text = next(b.text for b in result.result.message.content if b.type == "text")
        profile, stats = verify(json.loads(text), surface, Path(entry["root"]))
        _write(out_dir, profile, stats)
        print(f"{profile.package}@{profile.version}: {stats['verified']}/{stats['claims']} verified")
    return 0

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="interlock")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("profile", "batch"):
        s = sub.add_parser(name)
        s.add_argument("package" if name == "profile" else "packages")
        s.add_argument("--surfaces", default="spikes/p0/data/surfaces.jsonl")
        s.add_argument("--cache", default=".cache/sources")
        s.add_argument("--out", default="profiles")
        if name == "profile":
            s.add_argument("--version", default=None)
            s.add_argument("--source-ref", default=None, help="git URL when kind is git")
        else:
            s.add_argument("--resume", default=None, metavar="BATCH_ID",
                           help="skip preparation/submission; poll and write results for an already-submitted batch")
    args = p.parse_args(argv)
    return cmd_profile(args) if args.cmd == "profile" else cmd_batch(args)

if __name__ == "__main__":
    raise SystemExit(main())
