"""``talos setup clud``: the section delegates to ``clud install talos``.

The point of these tests is that the section owns no configuration logic: it
builds one command, hands the terminal to it, and reports. Anything that reads
``~/.clud`` or writes ``config.yaml`` belongs to clud (#2609).
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path

import pytest

from hermes_cli import setup_clud


def test_the_command_is_configure_only_and_targets_this_install():
    target = Path("/opt/hermes-slim")
    command = setup_clud.clud_install_command("/usr/bin/clud", target)
    assert command == [
        "/usr/bin/clud",
        "install",
        "talos",
        "--configure-only",
        "--target",
        "/opt/hermes-slim",
    ]


def test_the_target_defaults_to_this_install_directory():
    command = setup_clud.clud_install_command("clud")
    assert command[0] == "clud"
    assert command[-1] == str(setup_clud.install_dir())
    assert setup_clud.install_dir().name != ""


def test_print_shows_the_command_and_runs_nothing(monkeypatch, capsys):
    def explode(*args, **kwargs):  # pragma: no cover - only reached on a bug
        raise AssertionError("--print must not run clud")

    monkeypatch.setattr(subprocess, "run", explode)
    setup_clud.print_clud_command()
    out = capsys.readouterr().out
    assert "clud install talos --configure-only --target" in out
    assert "--migrate-credentials" not in out


def test_a_missing_clud_is_a_refused_request(monkeypatch, capsys):
    monkeypatch.setattr(setup_clud.shutil, "which", lambda name: None)
    code = setup_clud.run_clud_section()
    assert code == setup_clud.EXIT_NOCLUD == 2
    out = capsys.readouterr().out
    assert setup_clud.CLUD_INSTALL_PAGE in out
    assert f"{setup_clud.CLI_NAME} setup clud" in out


def test_a_present_clud_is_run_with_the_caller_terminal(monkeypatch, capsys):
    seen = {}

    def fake_run(command, check):
        seen["command"] = command
        seen["check"] = check
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(setup_clud.shutil, "which", lambda name: "/usr/bin/clud")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert setup_clud.run_clud_section() == 0
    assert seen["command"][:3] == ["/usr/bin/clud", "install", "talos"]
    assert seen["check"] is False
    assert "configured" in capsys.readouterr().out


def test_clud_failure_is_reported_and_propagated(monkeypatch, capsys):
    monkeypatch.setattr(setup_clud.shutil, "which", lambda name: "/usr/bin/clud")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(command, 1),
    )
    assert setup_clud.run_clud_section() == 1
    out = capsys.readouterr().out
    assert "exited 1" in out
    assert "unchanged" in out


def test_the_parser_offers_clud_and_print():
    from hermes_cli.subcommands.setup import build_setup_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_setup_parser(subparsers, cmd_setup=lambda args: None)
    parsed = parser.parse_args(["setup", "clud", "--print"])
    assert parsed.section == "clud"
    assert parsed.print_only is True


def test_clud_is_a_setup_section():
    from hermes_cli.setup import SETUP_SECTIONS

    keys = [key for key, _, _ in SETUP_SECTIONS]
    assert "clud" in keys


def test_the_wizard_prints_instead_of_installing(monkeypatch, capsys):
    """``talos setup clud --print`` never reaches subprocess."""
    from hermes_cli import setup as setup_module

    def explode(*args, **kwargs):  # pragma: no cover - only reached on a bug
        raise AssertionError("--print must not run clud")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(setup_module, "load_config", dict)
    monkeypatch.setattr(setup_module, "get_hermes_home", lambda: Path("/tmp/hermes"))

    args = argparse.Namespace(section="clud", print_only=True)
    setup_module._run_setup_wizard_impl(args)
    out = capsys.readouterr().out
    assert "clud install talos" in out
