"""Fetch provider source for reading only. Nothing here executes provider code."""
from __future__ import annotations
import hashlib, shutil, stat, subprocess, tarfile, zipfile
from pathlib import Path

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

def source_digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(Path(path).rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()
