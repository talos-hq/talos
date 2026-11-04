"""``hermes setup clud``: let clud own this install's provider configuration.

clud already installs this product (``clud install talos``) and, in the same
command, offers to migrate its stored provider credentials into the product's
``config.yaml``. This section delegates to that one implementation instead of
reading ``~/.clud`` or writing a config file of another agent's format here:
one door, no drift between the two directions.

``--print`` shows the command and changes nothing. Without ``clud`` on ``PATH``
the section prints the install page and exits 2 rather than guessing.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from hermes_cli.cli_output import print_error, print_info, print_success

#: The product name clud knows this install by (``clud install <product>``).
CLUD_PRODUCT = "talos"

#: The command name this section is run as, for its own hints.
CLI_NAME = "talos"

#: Where clud's own installer lives, for a machine that has none.
CLUD_INSTALL_PAGE = "https://talos-hq.github.io/clud/install/index.html"

#: Exit status when clud is missing: a refused request, not a crash.
EXIT_NOCLUD = 2


def install_dir() -> Path:
    """The directory this installation lives in, which clud configures."""
    return Path(__file__).parent.parent.resolve()


def clud_install_command(clud: str = "clud", target: Path | None = None) -> list[str]:
    """The one command this section runs.

    ``--configure-only`` because the product is already installed; ``--target``
    so clud edits *this* installation's config rather than the default one.
    """
    return [
        clud,
        "install",
        CLUD_PRODUCT,
        "--configure-only",
        "--target",
        str(target if target is not None else install_dir()),
    ]


def print_clud_command(stream=None) -> None:
    """``hermes setup clud --print``: show the command, change nothing."""
    out = stream if stream is not None else sys.stdout
    print("  clud would run:", file=out)
    print(f"    {shlex.join(clud_install_command())}", file=out)
    print(
        "  It offers to migrate clud's stored provider credentials into this "
        "installation's config.yaml.",
        file=out,
    )


def run_clud_section() -> int:
    """Run ``clud install <product> --configure-only`` for this installation."""
    clud = shutil.which("clud")
    if clud is None:
        print_error("clud is not on PATH.")
        print_info("Install clud first: " + CLUD_INSTALL_PAGE)
        print_info(f"Then re-run: {CLI_NAME} setup clud")
        return EXIT_NOCLUD
    command = clud_install_command(clud)
    print_info("Handing provider setup to clud:")
    print_info(f"  {shlex.join(command)}")
    print_info(
        "clud owns the credential store and the migration prompt; nothing is "
        "written here directly."
    )
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        print_error(f"clud exited {completed.returncode}; config left unchanged.")
        return completed.returncode
    print_success("clud finished; this installation is configured.")
    return 0
