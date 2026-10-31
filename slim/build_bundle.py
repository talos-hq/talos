#!/usr/bin/env python3
"""Build a self-contained talos bundle for one target.

    python slim/build_bundle.py --target darwin-x64 [--out dist] [--wheels DIR]

The bundle is a relocatable CPython (python-build-standalone, pinned with
sha256 in pm/lock.json) with every runtime dependency from uv.lock already
installed, the Hermes source tree, and launchers. The client machine only
unpacks it: no package resolution, no compilers, no downloads at install time.

Stdlib only, so it runs on any Python 3.11+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGETS = ("darwin-x64", "darwin-arm64", "win32-x64", "linux-x64-musl", "linux-x64", "linux-arm64")
# Oldest glibc a glibc bundle runs on: 2.28 is Debian 10, Ubuntu 20.04, RHEL 8.
# python-build-standalone itself needs only 2.17. install.sh checks the same floor.
GLIBC_FLOOR = {"linux-x64": "x86_64-manylinux_2_28", "linux-arm64": "aarch64-manylinux_2_28"}
# Extras the slim bundle ships. Gateway + Telegram + MCP + free web search.
# `sms` is just aiohttp, which the gateway API server (/health) needs.
EXTRAS = ("telegram", "mcp", "ddgs", "sms")
# Source paths copied into app/. Everything else (desktop app, website, tests) stays out.
APP_EXCLUDE = {
    ".git", ".github", "apps", "website", "web", "tests", "tests-js", "evals", "ui-tui",
    "node_modules", "dist", "build", ".venv", "docker", "nix", "contributors", "assets", ".clud",
    "wheels",  # CI downloads hermes-slim/wheels here; already installed into site-packages
}
# Stdlib parts Hermes never imports: its own test suite, IDLE, Tk and the pip bootstrapper.
STDLIB_PRUNE = ("test", "idlelib", "tkinter", "turtledemo", "turtle.py", "ensurepip", "__phello__")
# Tcl/Tk payload that only _tkinter loads (lib/ on Unix, the interpreter root on Windows).
# Versioned names only: on Windows python/lib *is* the stdlib (case-insensitive), where a bare
# "thread*" took threading.py with it.
TK_GLOBS = ("tcl[0-9]*", "tk[0-9]*", "libtcl*", "libtk*", "itcl[0-9]*", "thread[0-9]*", "_tkinter*")


def host_target() -> str:
    sysname = {"darwin": "darwin", "win32": "win32"}.get(sys.platform, "linux")
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine().lower()]
    t = f"{sysname}-{arch}"
    if sysname == "linux" and Path("/etc/alpine-release").exists():
        t += "-musl"
    return t


def pin(package: str, target: str) -> dict:
    lock = json.loads((REPO / "pm" / "lock.json").read_text())
    return lock["packages"][package]["artifacts"][target]


# pm/lock.json pins python-build-standalone's `install_only` builds, which carry full
# debug info (python3 and libpython are ~47 MiB each, mostly symbols). The bundle uses
# the same release's `install_only_stripped` build; stripping ourselves is not safe
# (Debian 12's binutils strip breaks the glibc interpreter). Hashes from the release's
# SHA256SUMS; update them with the python pin.
STRIPPED_PYTHON_SHA256 = {
    "cpython-3.14.9+20261021-aarch64-apple-darwin-install_only_stripped.tar.gz":
        "9f8c3480b8f0db553c270db548e8a21ae8e39df412e5661d66e8050c5c6ed2a8",
    "cpython-3.14.9+20261021-x86_64-apple-darwin-install_only_stripped.tar.gz":
        "9957784870f6eb613ab82fa7ac3201627940f493e6643c749fb3f126930a4232",
    "cpython-3.14.9+20261021-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "6f77731d85a14ed848dd4041478cd3ed511523caa3d896b84b436a7edf13cf62",
    "cpython-3.14.9+20261021-x86_64-pc-windows-msvc-install_only_stripped.tar.gz":
        "57cc230da61d897e8e593511c9dac781491675a036cb727b7de5cb7a62ceebea",
    "cpython-3.14.9+20261021-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "561e91c1cadf30e84aec94d1917384ae3267e5f3e6e135e51fd1578bb19f4064",
    "cpython-3.14.9+20261021-x86_64-unknown-linux-musl-install_only_stripped.tar.gz":
        "eb38460d0184268070aef47fa6b0774be8455d09d00f606a51f80de1f1ec9fc7",
}


# Windows: python.org's own CPython (the NuGet `python` package, same 3.14.9), whose every
# binary is Authenticode-signed by the Python Software Foundation. python-build-standalone's
# Windows build is unsigned, and Smart App Control blocks unsigned interpreters outright.
NUGET_PYTHON = {
    "win32-x64": {"url": "https://api.nuget.org/v3-flatcontainer/python/3.14.9/python.3.14.9.nupkg",
                  "sha256": "7cd9d07aec975212344dc6f7e45019378cf6775dd564181e269010a1eddaaec7"},
}


def python_pin(target: str) -> dict:
    if target in NUGET_PYTHON:
        return NUGET_PYTHON[target]
    url = pin("python", target)["url"].replace("-install_only.tar.gz", "-install_only_stripped.tar.gz")
    name = url.rsplit("/", 1)[-1]
    if name not in STRIPPED_PYTHON_SHA256:
        raise SystemExit(f"no sha256 for {name}: pm/lock.json moved the python pin; add it to STRIPPED_PYTHON_SHA256")
    return {"url": url, "sha256": STRIPPED_PYTHON_SHA256[name]}


def fetch(url: str, sha256: str, dest_dir: Path) -> Path:
    dest = dest_dir / url.rsplit("/", 1)[-1]
    if not dest.exists():
        print(f"fetch {url}", flush=True)
        with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if digest != sha256:
        dest.unlink()
        raise SystemExit(f"sha256 mismatch for {url}: {digest} != {sha256}")
    return dest


def unpack(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="tar") if hasattr(tarfile, "data_filter") else t.extractall(dest)


def find_exe(root: Path, names: tuple[str, ...]) -> Path:
    for name in names:
        for p in sorted(root.rglob(name)):
            if p.is_file():
                return p
    raise SystemExit(f"no {names} under {root}")


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def write_launchers(bundle: Path, windows: bool) -> None:
    bindir = bundle / "bin"
    bindir.mkdir(exist_ok=True)
    if windows:
        for name, script in (("hermes", "app\\hermes"), ("talos-setup", "app\\slim\\setup.py")):
            (bindir / f"{name}.cmd").write_text(
                "@echo off\r\n"
                "set \"HS_ROOT=%~dp0..\"\r\n"
                "set \"PYTHONPATH=%HS_ROOT%\\app\"\r\n"
                "set \"TALOS=1\"\r\n"
                # `hermes update` moves a bundle, not a git checkout: slim/update.py.
                + ("if /i \"%~1\"==\"update\" (\"%HS_ROOT%\\python\\python.exe\" \"%HS_ROOT%\\app\\slim\\update.py\" %* & exit /b)\r\n"
                   if name == "hermes" else "")
                + f"\"%HS_ROOT%\\python\\python.exe\" \"%HS_ROOT%\\{script}\" %*\r\n",
                newline="",
            )
    else:
        for name, script in (("hermes", "app/hermes"), ("talos-setup", "app/slim/setup.py")):
            p = bindir / name
            p.write_text(
                "#!/bin/sh\n"
                # Resolve symlinks (install.sh links these into ~/.local/bin).
                'self="$0"\n'
                'while [ -L "$self" ]; do l="$(readlink "$self")"; case "$l" in /*) self="$l" ;; *) self="$(dirname "$self")/$l" ;; esac; done\n'
                'HS_ROOT="$(cd "$(dirname "$self")/.." && pwd)"\n'
                'export PYTHONPATH="$HS_ROOT/app"\n'
                "export TALOS=1\n"
                # `hermes update` moves a bundle, not a git checkout: slim/update.py.
                + ('[ "${1:-}" = update ] && exec "$HS_ROOT/python/bin/python3" "$HS_ROOT/app/slim/update.py" "$@"\n'
                   if name == "hermes" else "")
                + f'exec "$HS_ROOT/python/bin/python3" "$HS_ROOT/{script}" "$@"\n'
            )
            p.chmod(0o755)


def write_stamp(app: Path, python: Path) -> None:
    """Give the bundle Hermes' packaged-build identity (it has no .git to read).

    Without it `hermes --version` says "vunknown", and `hermes update` treats the tree
    as a git checkout it can move. `external`: a new bundle, not hermes update, replaces it.
    """
    described = subprocess.run(["git", "describe", "--tags", "--match", "v*", "--long"],
                               cwd=REPO, capture_output=True, text=True).stdout.strip()
    tag, distance, _ = described.rsplit("-", 2) if described else ("v0.0.0", "0", "")
    run([python, REPO / "scripts" / "write_install_stamp.py", "--output", app / "install-stamp.json",
         "--base-version", tag.removeprefix("v"), "--distance", distance,
         "--source", "ci", "--update-mechanism", "external"], cwd=REPO)


def slim_python(python_root: Path, python: Path) -> None:
    """Drop the stdlib parts a bundle never runs."""
    stdlib = Path(subprocess.run([python, "-c", "import os; print(os.path.dirname(os.__file__))"],
                                 capture_output=True, text=True, check=True).stdout.strip())
    for name in STDLIB_PRUNE:
        p = stdlib / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    for d in (stdlib / "lib-dynload", python_root / "DLLs", python_root / "lib", python_root):
        if d.is_dir():
            for pattern in TK_GLOBS:
                for p in d.glob(pattern):
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
    shutil.rmtree(python_root / "include", ignore_errors=True)
    shutil.rmtree(python_root / "tcl", ignore_errors=True)


def precompile(bundle: Path, python: Path, windows: bool, work: Path) -> None:
    """Ship bytecode for the modules a first launch imports, and only those.

    Compiling the whole tree adds ~20 MiB to the download for modules most
    installs never load. Instead run the client's own first steps (`hermes
    --version`, setup bringing the gateway to /health, loading the chat agent)
    so Python writes .pycs for exactly what they import, then rewrite those as
    unchecked-hash: valid whatever mtimes unpacking gives the sources (zip keeps
    only 2-second ones), and never re-checked against the source.
    """
    home = work / "warm-home"
    shutil.rmtree(home, ignore_errors=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONDONTWRITEBYTECODE")}
    env.update(HERMES_HOME=str(home), HERMES_NONINTERACTIVE="1", PYTHONPATH=str(bundle / "app"), TALOS="1")
    bindir = bundle / "bin"
    hermes = bindir / ("hermes.cmd" if windows else "hermes")
    setup = bindir / ("talos-setup.cmd" if windows else "talos-setup")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    run([hermes, "--version"], env=env, stdout=subprocess.DEVNULL)
    try:
        run([setup, "--non-interactive", "--first-name", "Warm", "--last-name", "Up", "--openrouter-key",
             "sk-or-v1-" + "0" * 64, "--skip-online-checks", "--api-port", port, "--json"],
            env=env, stdout=subprocess.DEVNULL, timeout=600)
    finally:
        subprocess.run([str(hermes), "gateway", "stop"], env=env, capture_output=True, timeout=120)
    run([python, "-c", "import cli, run_agent"], env=env, cwd=bundle / "app", stdout=subprocess.DEVNULL)
    shutil.rmtree(home, ignore_errors=True)

    sources = []
    for pyc in bundle.rglob("*.pyc"):
        src = pyc.parent.parent / (pyc.name.split(".", 1)[0] + ".py")
        pyc.unlink()
        if src.is_file():
            sources.append(str(src))
    listing = work / "warm-modules.txt"
    listing.write_text("\n".join(sources) + "\n")
    print(f"precompiling {len(sources)} modules the first launch imports", flush=True)
    run([python, "-m", "compileall", "-q", "-i", listing, "--invalidation-mode", "unchecked-hash"],
        stdout=subprocess.DEVNULL)


def stage(target: str, wheels: Path | None, work: Path) -> Path:
    """Assemble work/talos; package() turns it into the archive."""
    windows = target.startswith("win32")
    bundle = work / "talos"
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    downloads = work / "downloads"
    downloads.mkdir(exist_ok=True)

    py_pin = python_pin(target)
    if target in NUGET_PYTHON:
        # The interpreter lives under tools/; its bundled pip (with unsigned launcher stubs) is not needed.
        nuget = work / "nuget"
        shutil.rmtree(nuget, ignore_errors=True)
        with zipfile.ZipFile(fetch(py_pin["url"], py_pin["sha256"], downloads)) as z:
            z.extractall(nuget)
        shutil.move(nuget / "tools", bundle / "python")
        for p in (bundle / "python" / "Lib" / "site-packages").glob("pip*"):
            shutil.rmtree(p)
    else:
        unpack(fetch(py_pin["url"], py_pin["sha256"], downloads), bundle)  # -> bundle/python/
    python = bundle / "python" / ("python.exe" if windows else "bin/python3")

    uv_dir = work / "uv"
    if not uv_dir.exists():
        uv_pin = pin("uv", host_target())
        unpack(fetch(uv_pin["url"], uv_pin["sha256"], downloads), uv_dir)
    uv = find_exe(uv_dir, ("uv.exe", "uv"))
    if not windows:
        uv.chmod(0o755)

    env = dict(os.environ, UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never")
    reqs = work / "requirements.txt"
    export = [uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes",
              "--format", "requirements.txt", "-o", reqs]
    for extra in EXTRAS:
        export += ["--extra", extra]
    run(export, cwd=REPO, env=env)

    install = [uv, "pip", "install", "--python", python, "--break-system-packages", "--no-deps", "--no-build", "-r", reqs]
    if target in GLIBC_FLOOR:
        # Otherwise uv picks wheels for the build host's glibc, and a bundle built on a new
        # distro refuses to load on an older one.
        install += ["--python-platform", GLIBC_FLOOR[target]]
    if wheels and wheels.is_dir():
        install += ["--find-links", wheels]
    log = work / "install.log"
    with open(log, "w") as f:
        proc = subprocess.run([str(c) for c in install], cwd=REPO, env=env, stdout=f, stderr=subprocess.STDOUT)
    text = log.read_text()
    print(text[-20000:])
    if proc.returncode:
        # --no-build: a package with no wheel for this target fails here instead of compiling.
        # Fix it by publishing a wheel to hermes-slim/wheels, not by compiling in the bundle.
        raise SystemExit(f"dependency install failed on {target} (see log above)")

    app = bundle / "app"
    # What a commit would hold (tracked, plus new files not gitignored): a working tree
    # also holds build output, caches and local bundles.
    tracked = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=REPO, capture_output=True, check=True).stdout
    for rel in tracked.decode().split("\0"):
        if not rel or rel.split("/", 1)[0] in APP_EXCLUDE:
            continue
        src = REPO / rel
        if src.is_file():
            (app / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, app / rel, follow_symlinks=False)
    write_stamp(app, python)
    write_launchers(bundle, windows)
    slim_python(bundle / "python", python)
    precompile(bundle, python, windows, work)
    if windows:
        write_unsigned_list(bundle, work)
    return bundle


def write_unsigned_list(bundle: Path, work: Path) -> None:
    """Smart App Control runs only signed binaries, wheels' .pyd/.dll included. List what
    the bundle ships unsigned; CI signs exactly these (Authenticode) before package()."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pe_signatures import scan

    signed, unsigned = scan(bundle)
    (work / "unsigned.txt").write_text("".join(f"{p}\n" for p in unsigned))
    print(f"{len(signed)} binaries signed upstream, {len(unsigned)} to sign: {work / 'unsigned.txt'}", flush=True)


def package(target: str, out: Path, work: Path) -> Path:
    windows = target.startswith("win32")
    bundle = work / "talos"
    python = bundle / "python" / ("python.exe" if windows else "bin/python3")
    out.mkdir(parents=True, exist_ok=True)
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip() or "dev"
    version = subprocess.run(["git", "describe", "--tags", "--match", "v*"], cwd=REPO,
                             capture_output=True, text=True).stdout.strip() or rev
    (bundle / "BUNDLE.json").write_text(json.dumps(
        {"target": target, "commit": rev, "version": version, "extras": EXTRAS}, indent=2))
    name = f"talos-{rev}-{target}"
    if windows:
        archive = out / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for p in bundle.rglob("*"):
                z.write(p, p.relative_to(work))
    else:
        # zstd -9: about as small as xz for this tree and several times faster to unpack.
        # The host Python may predate compression.zstd (3.14); the bundled one never does.
        archive = out / f"{name}.tar.zst"
        run([python, "-c", "import sys, tarfile; t = tarfile.open(sys.argv[1], 'w:zst', level=9); "
                           "t.add(sys.argv[2], arcname='talos'); t.close()", archive, bundle])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"bundle: {archive} ({archive.stat().st_size // (1 << 20)} MiB) sha256={digest}")
    return archive


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=host_target(), choices=TARGETS)
    ap.add_argument("--out", type=Path, default=REPO / "dist")
    ap.add_argument("--wheels", type=Path, help="directory of extra wheels (hermes-slim/wheels release assets)")
    ap.add_argument("--work", type=Path, help="scratch directory (default: a temp dir)")
    step = ap.add_mutually_exclusive_group()
    step.add_argument("--stage-only", action="store_true",
                      help="assemble WORK/talos and stop (Windows CI signs it next)")
    step.add_argument("--package-only", action="store_true", help="archive an already staged WORK/talos")
    args = ap.parse_args()
    if args.target != host_target():
        raise SystemExit(f"bundles build natively: host is {host_target()}, asked for {args.target}")
    work = args.work or Path(tempfile.mkdtemp(prefix="talos-build-"))
    work.mkdir(parents=True, exist_ok=True)
    if not args.package_only:
        stage(args.target, args.wheels, work.resolve())
    if not args.stage_only:
        package(args.target, args.out.resolve(), work.resolve())


if __name__ == "__main__":
    main()
