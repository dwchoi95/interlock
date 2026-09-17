"""Turn a model judgement into a profile only the checked parts of which are trusted."""
from __future__ import annotations
import subprocess
from pathlib import Path
from interlock.adjudicate import adjudicate
from interlock.evidence import check_tool, classify_evidence
from interlock.profile import Profile, ToolEffect
from interlock.select import select_files
from interlock.source import fetch_dependencies, fetch_source
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
    """Tool names that occur in none of root's own non-documentation source files
    (excludes root/.deps: a previously copied-in dependency doesn't count as "own code",
    so a resumed/cached run still re-derives the same dependency note)."""
    found: set[str] = set()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts[0] == ".deps" or classify_evidence(str(rel)) == "doc":
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        found |= {name for name in tool_names if name not in found and name in text}
    return sorted(name for name in tool_names if name not in found)


def prepare_sources(kind: str, package: str, version: str, cache_dir: Path, tool_names: list[str],
                    source_ref: str | None = None, run=subprocess.run) -> tuple[Path, list[tuple[str, str]], list[str]]:
    """Fetch a package's source and, for npm, follow direct dependencies when a tool name
    the model must judge appears nowhere in the package's own code (a thin wrapper package
    typically implements its tools in a dependency instead)."""
    root = fetch_source(kind, source_ref or package, version, cache_dir, run=run)
    notes: list[str] = []
    if kind == "npm":
        missing = _own_code_missing_names(root, tool_names)
        if missing:
            kept = fetch_dependencies(root, missing, cache_dir, run=run)
            if kept:
                notes.append(f"dependency sources included: {', '.join(kept)}")
    files = select_files(root, tool_names)
    return root, files, notes


def build_profile(package: str, version: str | None, kind: str, surfaces_path: Path, cache_dir: Path, client,
                  source_ref: str | None = None, usage_out: dict | None = None) -> tuple[Profile, dict]:
    surface = load_surface(surfaces_path, package, version)
    root, files, notes = prepare_sources(kind, package, surface["version"], cache_dir,
                                         [t["name"] for t in surface["tools"]], source_ref=source_ref)
    raw = adjudicate(surface, files, client=client, usage_out=usage_out)
    profile, stats = verify(raw, surface, root)
    profile.notes = list(profile.notes) + notes
    return profile, stats
