"""Turn a model judgement into a profile only the checked parts of which are trusted."""
from __future__ import annotations
import os, shutil, subprocess
from pathlib import Path
from interlock.adjudicate import adjudicate
from interlock.evidence import check_tool, classify_evidence
from interlock.profile import Profile, ToolEffect
from interlock.select import is_candidate, select_files
from interlock.source import cache_path, fetch_dependencies, fetch_source, slugify
from interlock.surface import load_surface


def verify(raw: dict, surface: dict, root: Path) -> tuple[Profile, dict]:
    tools: dict[str, ToolEffect] = {}
    stats = {"tools": len(surface["tools"]), "claims": 0, "verified": 0, "verified_doc": 0, "demoted": 0,
              "cleared_verified": 0, "cleared_unverified": 0, "missing_tools": [], "unknown_tools": []}
    judged = raw.get("tools", {})
    surface_names = {t["name"] for t in surface["tools"]}
    stats["unknown_tools"] = sorted(set(judged) - surface_names)
    for t in surface["tools"]:
        name = t["name"]
        j = judged.get(name)
        if j is None:
            stats["missing_tools"].append(name)
            tools[name] = ToolEffect(labels=["HOSTEXEC"], evidence=[], rationale="not judged; least restrictive reading",
                                     default_enabled=True, undetermined=True)
            continue
        try:
            # Copy labels/evidence: they must not alias the caller's raw lists.
            effect = ToolEffect(labels=list(j["labels"]), evidence=list(j["evidence"]), rationale=j["rationale"],
                                default_enabled=j.get("default_enabled", True),
                                undetermined=bool(j.get("undetermined")))
        except ValueError as e:
            raise ValueError(f"{surface['package']}@{surface['version']}: tool {name!r}: {e}") from e
        # Evidence is checked whether the model asserted labels or cleared the tool outright:
        # a clearance is itself a judgement, and an unverifiable one must not pass silently.
        # Documentation may raise an effect but never lower one, so a citation that only
        # verifies against a README/CHANGELOG/etc counts separately from verified code,
        # and can never justify clearing a tool.
        ok, reason, evidence_class = check_tool(root, effect, name)
        if effect.labels:
            stats["claims"] += 1
            if ok and evidence_class == "code":
                stats["verified"] += 1
            elif ok and evidence_class == "doc":
                stats["verified_doc"] += 1
                effect.undetermined = True
                effect.rationale = f"{effect.rationale} [evidence: documentation only]"
            else:
                stats["demoted"] += 1
                effect.undetermined = True
                effect.rationale = f"{effect.rationale} [unverified: {reason}]"
        else:
            if ok and evidence_class == "code":
                stats["cleared_verified"] += 1
            else:
                stats["cleared_unverified"] += 1
                effect.undetermined = True
                clear_reason = reason if not ok else "documentation cannot verify a clearance"
                effect.rationale = f"{effect.rationale} [unverified: {clear_reason}]"
        tools[name] = effect
    profile = Profile(package=surface["package"], version=surface["version"], kind=surface["kind"],
                      source=str(root), tools=tools, value_conditions=raw.get("value_conditions", []),
                      notes=raw.get("notes", []))
    return profile, stats


def _own_code_missing_names(root: Path, tool_names: list[str]) -> list[str]:
    """Tool names that occur in none of root's own non-documentation source files that
    select_files would ever consider (excludes root/.deps: a dependency copied into a
    cached tree by a version of this code from before per-run views doesn't count as
    "own code"). Uses select_files's own is_candidate rule plus classify_evidence's
    documentation rule, so a name that appears only in a vendored, test or non-allowlisted
    file never counts as "found" when select_files would never surface that file anyway."""
    found: set[str] = set()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts[0] == ".deps" or not is_candidate(p, root) or classify_evidence(str(rel)) == "doc":
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        found |= {name for name in tool_names if name not in found and name in text}
    return sorted(name for name in tool_names if name not in found)


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy src to dst, materialising no symlink (one in an untrusted tree could point
    anywhere) and leaving out src's own top-level .deps (a stale copy-in, see above)."""
    def ignore(d, names):
        return [n for n in names if os.path.islink(os.path.join(d, n)) or (n == ".deps" and Path(d) == Path(src))]
    shutil.copytree(src, dst, ignore=ignore)


def prepare_sources(kind: str, package: str, version: str, cache_dir: Path, tool_names: list[str],
                    source_ref: str | None = None, run=subprocess.run) -> tuple[Path, list[tuple[str, str]], list[str]]:
    """Fetch a package's source and return (view, selected files, notes), where view is a
    per-run copy of the cached tree at cache_dir/.views/<slug>, rebuilt from scratch every
    call. For npm, when a tool name the model must judge appears nowhere in the package's
    own code (a thin wrapper package typically implements its tools in a dependency), each
    direct dependency kept is copied into the view under .deps/<sanitised name>/. Cached
    trees are never modified, so nothing kept by an earlier run can reach this one."""
    cached = fetch_source(kind, source_ref or package, version, cache_dir, run=run)
    kept, skipped = [], []
    if kind == "npm":
        missing = _own_code_missing_names(cached, tool_names)
        if missing:
            kept, skipped = fetch_dependencies(cached, missing, cache_dir, run=run)
    view = cache_path(cache_dir, f".views/{cached.name}")
    if view.exists():
        shutil.rmtree(view)
    view.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree(cached, view)
    included = []
    for name, resolved, dep_root in kept:
        dest = view / ".deps" / slugify(name)
        if dest.exists():
            skipped.append((name, "its directory name collides with another dependency's"))
            continue
        _copy_tree(dep_root, dest)
        included.append(f"{name}@{resolved}")
    notes = [f"dependency sources included: {', '.join(included)}"] if included else []
    notes += [f"dependency skipped: {name!r:.120} ({reason})" for name, reason in skipped]
    return view, select_files(view, tool_names), notes


def build_profile(package: str, version: str | None, kind: str, surfaces_path: Path, cache_dir: Path, client,
                  source_ref: str | None = None, usage_out: dict | None = None) -> tuple[Profile, dict]:
    surface = load_surface(surfaces_path, package, version)
    root, files, notes = prepare_sources(kind, package, surface["version"], cache_dir,
                                         [t["name"] for t in surface["tools"]], source_ref=source_ref)
    raw = adjudicate(surface, files, client=client, usage_out=usage_out)
    profile, stats = verify(raw, surface, root)
    profile.notes = list(profile.notes) + notes
    return profile, stats
