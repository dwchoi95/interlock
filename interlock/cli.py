"""interlock profile npm:@scope/pkg  |  interlock batch packages.txt"""
from __future__ import annotations
import argparse, json, sys, time
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

def cmd_batch(args) -> int:
    client = make_client()
    specs = [l.strip() for l in Path(args.packages).read_text().splitlines() if l.strip() and not l.startswith("#")]
    prepared = {}
    requests = []
    for spec in specs:
        kind, package = _split(spec)
        surface = load_surface(Path(args.surfaces), package)
        root = fetch_source(kind, package, surface["version"], Path(args.cache))
        files = select_files(root, [t["name"] for t in surface["tools"]])
        cid = f"{kind}_{package.replace('/', '_').lstrip('@')}"[:64]
        prepared[cid] = (surface, root)
        requests.append(batch_request(cid, surface, files))
    batch = client.messages.batches.create(requests=requests)
    print(f"batch {batch.id} with {len(requests)} requests")
    while client.messages.batches.retrieve(batch.id).processing_status != "ended":
        time.sleep(30)
    for result in client.messages.batches.results(batch.id):
        surface, root = prepared[result.custom_id]
        if result.result.type != "succeeded":
            print(f"{result.custom_id}: {result.result.type}", file=sys.stderr)
            continue
        text = next(b.text for b in result.result.message.content if b.type == "text")
        profile, stats = verify(json.loads(text), surface, root)
        _write(Path(args.out), profile, stats)
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
    args = p.parse_args(argv)
    return cmd_profile(args) if args.cmd == "profile" else cmd_batch(args)

if __name__ == "__main__":
    raise SystemExit(main())
