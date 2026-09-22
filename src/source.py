"""Fetch provider source for reading only. Nothing here executes provider code.

Every package name, version, range and git source reaching this module is untrusted (mined
from public configuration files, or read from a fetched package.json), so each is checked
against an allowlist before any filesystem or subprocess use, and every path built from one
is proven to stay inside the cache directory before anything is created or deleted."""
from __future__ import annotations
import hashlib, json, re, shutil, stat, subprocess, tarfile, zipfile
from pathlib import Path
from src.evidence import classify_evidence
from src.select import is_candidate

_METADATA_SUFFIXES = (".dist-info", ".data", ".egg-info")
_NPM_NAME = re.compile(r"(?:@[a-z0-9~-][a-z0-9._~-]*/)?[a-z0-9~-][a-z0-9._~-]*")
_NPM_VERSION = re.compile(r"[A-Za-z0-9.+~^<>=*| -]+")
# npm reads a spec starting with "." as a directory and one ending in a tarball suffix as a
# local file, although neither has ':' or '/'. The unescaped '.' in "tar.gz" mirrors npm's
# own regex (npm-package-arg), which also matches e.g. "x.tar-gz".
_NPM_FILE_SPEC = re.compile(r"^\.|\.(?:tgz|tar.gz|tar)$", re.I)
_NPM_EXACT = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")
_PYPI_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PYPI_VERSION = re.compile(r"[A-Za-z0-9.+!-]+")
_GIT_SOURCE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_GIT_REF = re.compile(r"[A-Za-z0-9._/-]+")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

def _ok(pattern: re.Pattern, value) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None

def _require(ok: bool, what: str, value) -> None:
    if not ok:
        raise ValueError(f"invalid {what}: {value!r:.120}")

def _validate(kind: str, package, version) -> None:
    """Raise ValueError unless package and version are safe to hand to the fetcher for kind."""
    if kind == "npm":
        _require(_ok(_NPM_NAME, package) and len(package) <= 214, "npm package name", package)
        _require(_ok(_NPM_VERSION, version) and ".." not in version and not _NPM_FILE_SPEC.search(version.strip()),
                 "npm version or range", version)
    elif kind == "pypi":
        _require(_ok(_PYPI_NAME, package), "PyPI package name", package)
        _require(_ok(_PYPI_VERSION, version), "PyPI version", version)
    elif kind == "git":
        _require(_ok(_GIT_SOURCE, package) and ".." not in package,
                 "git source (only https://github.com/<owner>/<repo>)", package)
        _require(_ok(_GIT_REF, version) and ".." not in version, "git ref", version)
    else:
        raise ValueError(f"unknown source kind: {kind!r:.40}")

def slugify(text: str) -> str:
    """text with every character outside [A-Za-z0-9._-] replaced by '_' (one path component)."""
    return _UNSAFE.sub("_", text)

def _slug(kind: str, package: str, version: str) -> str:
    """Cache slug for kind/package/version: a readable sanitised prefix followed by the
    first 12 hex characters of sha256("kind:package:version") over the *unsanitised*
    identity. Two different packages whose sanitised names collide (e.g. "@a/b" and
    "a_b" both sanitise to "a_b") must never share a cache slug, or one could be judged
    from the other's source; the prefix alone is not unique, only prefix+digest is."""
    prefix = slugify(f"{kind}_{package.lstrip('@')}_{version}")
    digest = hashlib.sha256(f"{kind}:{package}:{version}".encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"

def cache_path(cache_dir: Path, name: str) -> Path:
    """cache_dir/name, or ValueError if that does not resolve strictly inside the resolved
    cache_dir (a '..' component, an absolute name, a symlink pointing out of the cache)."""
    cache_dir = Path(cache_dir)
    path = cache_dir / name
    base, resolved = cache_dir.resolve(), path.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        raise ValueError(f"refusing path outside the cache directory: {name!r:.120}")
    return path

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

def _fetch(kind: str, package: str, version: str, cache_dir: Path, run) -> tuple[Path, str]:
    """(cached tree, resolved version) for package@version; see fetch_source."""
    _validate(kind, package, version)
    dest = cache_path(cache_dir, _slug(kind, package, version))
    work = cache_path(cache_dir, f".work_{_slug(kind, package, version)}")
    # An npm range or tag resolves differently over time, so only an exact version may be
    # answered from the cache before npm says what it resolves to.
    if dest.exists() and (kind != "npm" or _NPM_EXACT.fullmatch(version)):
        return dest, version
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        if kind == "npm":
            # --ignore-scripts: no lifecycle script (e.g. prepare) may run; "--": the spec is
            # never parsed as an option.
            r = run(["npm", "pack", "--ignore-scripts", "--json", "--", f"{package}@{version}"],
                    cwd=str(work), capture_output=True, text=True, check=True)
            try:
                info = json.loads(r.stdout)[0]
                name, resolved = info["name"], info["version"]
            except (TypeError, ValueError, LookupError) as e:
                raise ValueError(f"unreadable `npm pack --json` output for {package}@{version}") from e
            _require(name == package, f"npm pack result for {package}", name)
            _validate(kind, package, resolved)
            version = resolved
            dest = cache_path(cache_dir, _slug(kind, package, version))
            if dest.exists():
                return dest, version
        elif kind == "pypi":
            # --only-binary: reading an sdist would mean building it (running its build
            # backend), so a package with no wheel is an error, never a build. cwd=work: pip
            # reads a requirement that names an existing path as that local file.
            r = run(["pip", "download", "--no-deps", "--no-build-isolation", "--only-binary=:all:",
                     "-d", str(work.resolve()), "--", f"{package}=={version}"],
                    cwd=str(work), capture_output=True, text=True)
            if r.returncode != 0 or not any(work.glob("*.whl")):
                raise RuntimeError(f"no wheel fetched for {package}=={version} (sdists are never built): "
                                   f"{(r.stderr or '').strip()[-300:]}")
        else:
            run(["git", "clone", "--depth", "1", f"--branch={version}", "--", package, str(work / "repo")],
                capture_output=True, text=True, check=True)
        unpacked = work / "unpacked"
        unpacked.mkdir()
        for archive in list(work.glob("*.tgz")) + list(work.glob("*.whl")) + list(work.glob("*.tar.gz")):
            _unpack(archive, unpacked)
        root = _pick_root(unpacked, work)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(dest))
        return dest, version
    finally:
        shutil.rmtree(work, ignore_errors=True)

def fetch_source(kind: str, package: str, version: str, cache_dir: Path, run=subprocess.run) -> Path:
    """Return a directory under cache_dir holding the unpacked sources of package@version.
    Raises ValueError, before touching the filesystem or running anything, for a name,
    version, range, ref or git source outside the allowlist."""
    return _fetch(kind, package, version, cache_dir, run)[0]

def _has_code_evidence(dep_root: Path, missing_names: list[str]) -> bool:
    """True if some file under dep_root that select_files would actually consider (not
    vendored, not a test file, not off the suffix allowlist) and that isn't documentation
    contains one of missing_names — so a dependency is kept only when its match would
    really reach the model as evidence."""
    for p in sorted(dep_root.rglob("*")):
        if not p.is_file() or not is_candidate(p, dep_root) or classify_evidence(str(p.relative_to(dep_root))) == "doc":
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, ValueError):
            continue
        if any(name in text for name in missing_names):
            return True
    return False

def fetch_dependencies(root: Path, missing_names: list[str], cache_dir: Path, run=subprocess.run,
                       max_deps: int = 5) -> tuple[list[tuple[str, str, Path]], list[tuple[str, str]]]:
    """Follow root's direct npm dependencies (no recursion) looking for tool names the
    package's own source never mentions — a thin wrapper package (e.g. @playwright/mcp)
    typically implements its tools in a dependency (playwright-core) instead.

    Returns (kept, skipped). kept: (name, resolved version, cached tree) for each dependency
    whose non-doc source mentions a missing name, in name order, at most max_deps. skipped:
    (name, reason) for each dependency whose name or range failed validation or whose fetch
    failed. Nothing is written outside the cache; the caller assembles its own view."""
    root = Path(root)
    try:
        data = json.loads((root / "package.json").read_text())
    except (OSError, ValueError):
        return [], []
    deps = data.get("dependencies") if isinstance(data, dict) else None
    if not isinstance(deps, dict):
        return [], []
    kept: list[tuple[str, str, Path]] = []
    skipped: list[tuple[str, str]] = []
    for name in sorted(deps):
        if len(kept) >= max_deps:
            break
        try:
            dep_root, resolved = _fetch("npm", name, deps[name], cache_dir, run)
        except ValueError as e:
            skipped.append((name, str(e)))
            continue
        except Exception as e:  # a dependency that fails to fetch is skipped, not fatal
            skipped.append((name, f"fetch failed: {e}"[:300]))
            continue
        if _has_code_evidence(dep_root, missing_names):
            kept.append((name, resolved, dep_root))
    return kept, skipped

def source_digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(Path(path).rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()
