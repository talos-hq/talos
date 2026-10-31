#!/usr/bin/env python3
"""`hermes update` for a talos bundle (the launchers route `update` here).

Hermes' own updater moves a git checkout, and with none here tells the user to
reinstall upstream with its long git + pip installer. A bundle is updated by
installing the newest bundle over it with slim/install.sh --upgrade, which keeps
the configuration and restarts the gateway. install.sh also finds releases
published before the rename (assets with the legacy prefix), and an install in
the legacy directory is upgraded where it is (--dir is passed explicitly).

    hermes update [--check] [--version vX.Y.Z | --archive FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = "talos-hq/talos"
BUNDLE = Path(__file__).resolve().parent.parent.parent  # <dir>/current: bin/, app/, python/


def installed_version() -> str:
    try:
        return json.loads((BUNDLE / "BUNDLE.json").read_text()).get("version") or "unknown"
    except (OSError, ValueError):
        return "unknown"


def latest_version() -> str:
    with urllib.request.urlopen(f"https://github.com/{REPO}/releases/latest", timeout=30) as r:
        return r.geturl().rstrip("/").rsplit("/", 1)[-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hermes update", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only report whether a newer bundle exists")
    ap.add_argument("--version", help="install this release instead of the latest")
    ap.add_argument("--archive", type=Path, help="install this bundle file (offline)")
    args, _unknown = ap.parse_known_args(argv)

    current = installed_version()
    target = args.version or (None if args.archive else latest_version())
    if args.check:
        print(f"installed {current}, latest {target}")
        print("up to date" if target == current else "run `hermes update` to install it")
        return 0
    if target and target == current:
        print(f"already on {current}")
        return 0
    if os.name == "nt":
        print("rerun install.ps1 to update this bundle (it keeps your configuration):\n"
              f"  irm https://raw.githubusercontent.com/{REPO}/main/slim/install.ps1 | iex")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "install.sh"
        if args.archive:
            # The installer that came with this bundle; --archive is for offline use and tests.
            script.write_bytes((BUNDLE / "app" / "slim" / "install.sh").read_bytes())
            how = ["--archive", str(args.archive.resolve())]
        else:
            # The new release's installer: it knows that release's bundle format.
            with urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/{target}/slim/install.sh",
                                        timeout=60) as r:
                script.write_bytes(r.read())
            how = ["--version", target]
        print(f"updating {current} -> {target or args.archive.name}")
        # install.sh replaces BUNDLE (where this interpreter lives), so it runs as a
        # separate process and this one only waits for it.
        return subprocess.run(["sh", str(script), *how, "--dir", str(BUNDLE.parent), "--upgrade"]).returncode


if __name__ == "__main__":
    sys.exit(main())
