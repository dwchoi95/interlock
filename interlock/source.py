"""Fetch provider source for reading only. Nothing here executes provider code."""
from __future__ import annotations
import hashlib, json, shutil, stat, subprocess, tarfile, zipfile
from pathlib import Path
from interlock.evidence import classify_evidence

_METADATA_SUFFIXES = (".dist-info", ".data", ".egg-info")

def _safe_extract_zip(z: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zip, refusing members that escape `dest` or are symlinks.

    zipfile.ZipFile.extractall has no path-traversal or symlink guard
    (unlike tarfile's filter="data"), and wheels are untrusted PyPI
    artifacts, so members are extracted one at a time with checks.
    """
    dest = dest.resolve()
    for info in z.infolist():
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            continue  # never materialize a symlink from an untrusted archive
        target = (dest / info.filename).resolve()
        try:
            target.relative_to(dest)
        except ValueError:
            continue  # member resolves outside dest
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)

def _unpack(archive: Path, dest: Path) -> None:
    if archive.suffix in (".tgz", ".gz"):
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")
    else:
        with zipfile.ZipFile(archive) as z:
            _safe_extract_zip(z, dest)

def _pick_root(unpacked: Path, work: Path) -> Path:
    entries = list(unpacked.iterdir()) if unpacked.exists() else []
    dirs = [p for p in entries if p.is_dir() and not p.name.endswith(_METADATA_SUFFIXES)]
    if len(dirs) == 1:
        return dirs[0]
    repo = work / "repo"
    return repo if repo.exists() else unpacked

def fetch_source(kind: str, package: str, version: str, cache_dir: Path, run=subprocess.run) -> Path:
    """Return a directory holding the unpacked sources of package@version."""
    if kind not in ("npm", "pypi", "git"):
        raise ValueError(f"unknown source kind: {kind}")
    cache_dir = Path(cache_dir)
    slug = f"{kind}_{package.replace('/', '_').lstrip('@')}_{version}"
    dest = cache_dir / slug
    if dest.exists():
        return dest
    work = cache_dir / f".work_{slug}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        if kind == "npm":
            run(["npm", "pack", f"{package}@{version}", "--silent"], cwd=str(work), capture_output=True, text=True, check=True)
        elif kind == "pypi":
            run(["pip", "download", "--no-deps", "--no-build-isolation", f"{package}=={version}", "-d", str(work)],
                capture_output=True, text=True, check=True)
        elif kind == "git":
            run(["git", "clone", "--depth", "1", "--branch", version, package, str(work / "repo")],
                capture_output=True, text=True, check=True)
        unpacked = work / "unpacked"
        unpacked.mkdir()
        for archive in list(work.glob("*.tgz")) + list(work.glob("*.whl")) + list(work.glob("*.tar.gz")):
            _unpack(archive, unpacked)
        root = _pick_root(unpacked, work)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(dest))
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)

def _has_code_evidence(dep_root: Path, missing_names: list[str]) -> bool:
    """True if some non-documentation file under dep_root contains one of missing_names."""
    for p in sorted(dep_root.rglob("*")):
        if not p.is_file() or classify_evidence(str(p.relative_to(dep_root))) == "doc":
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        if any(name in text for name in missing_names):
            return True
    return False

def fetch_dependencies(root: Path, missing_names: list[str], cache_dir: Path,
                       run=subprocess.run, max_deps: int = 5) -> list[str]:
    """Follow root's direct npm dependencies (no recursion) looking for tool names the
    package's own source never mentions — a thin wrapper package (e.g. @playwright/mcp)
    typically implements its tools in a dependency (playwright-core) instead. A kept
    dependency's tree is copied (never symlinked, so the evidence checker's containment
    rule accepts it) into root/.deps/<name>/. Returns the sorted names kept."""
    root = Path(root)
    try:
        data = json.loads((root / "package.json").read_text())
    except (OSError, ValueError):
        return []
    deps = data.get("dependencies")
    if not isinstance(deps, dict):
        return []
    kept: list[str] = []
    for name in sorted(deps):
        if len(kept) >= max_deps:
            break
        try:
            dep_root = fetch_source("npm", name, deps[name], cache_dir, run=run)
        except Exception:
            continue  # a dependency that fails to fetch is skipped, not fatal
        if not _has_code_evidence(dep_root, missing_names):
            continue
        dest = root / ".deps" / name.replace("/", "__")
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dep_root, dest, symlinks=False)
        kept.append(name)
    return sorted(kept)

def source_digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(Path(path).rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()
