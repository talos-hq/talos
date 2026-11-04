"""Catalogue installs provision optional MCP support before probing (#87)."""

from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("missing", [True, False])
def test_list_sdk_status(monkeypatch, capsys, missing):
    from hermes_cli import mcp_config

    monkeypatch.setattr(
        mcp_config, "_get_mcp_servers", lambda: {"demo": {"command": "demo"}}
    )
    monkeypatch.setattr(
        "importlib.util.find_spec", lambda name: None if missing else object()
    )
    monkeypatch.setattr("pm.features.read_features", list)
    mcp_config.cmd_mcp_list()
    output = capsys.readouterr().out
    if missing:
        assert "⚠ SDK missing" in output
        assert "hermes pm install --extra mcp" in output
        assert "✓ enabled" not in output
    else:
        assert "✓ enabled" in output
        assert "SDK missing" not in output


def test_clud_manifest(monkeypatch):
    from hermes_cli import mcp_catalog as catalog

    monkeypatch.setenv(
        "HERMES_OPTIONAL_MCPS",
        str(Path(__file__).resolve().parents[2] / "optional-mcps"),
    )
    entry = catalog.get_entry("clud")
    assert entry is not None
    assert entry.install is None
    assert entry.transport.type == "stdio"
    assert entry.transport.version
    assert entry.transport.command == "uvx"
    assert entry.transport.args == [
        "--from",
        f"clud=={entry.transport.version}",
        "python",
        "-m",
        "clud.mcp_server",
    ]
    assert catalog._build_server_config(entry, None)["command"] == "uvx"


@pytest.mark.parametrize("fails", [True, False])
@pytest.mark.parametrize("recorded", [True, False])
def test_install_provisions_before_probe(
    monkeypatch, capsys, tmp_path, fails, recorded
):
    from hermes_cli import mcp_catalog as catalog

    entry = catalog.CatalogEntry(
        name="demo",
        description="Demo",
        source="",
        transport=catalog.TransportSpec(type="stdio", command="demo"),
        auth=catalog.AuthSpec(type="none"),
    )
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    from hermes_cli import config, mcp_picker

    monkeypatch.setattr(
        "pm.features.read_features", lambda: ["mcp"] if recorded else []
    )
    monkeypatch.setattr(config, "get_config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(catalog, "get_entry", lambda name: entry)
    monkeypatch.setattr("pm.environments_adopt.adopt_selected", Mock(return_value=True))
    monkeypatch.setattr("pm.environments_adopt.note_restart_pending", Mock())
    monkeypatch.setattr(catalog, "_read_prior_tool_list", lambda *args: None)
    probe = Mock()
    monkeypatch.setattr(catalog, "_apply_tool_selection", probe)

    def synchronize(*args, **kwargs):
        probe.assert_not_called()
        assert config.load_config()["mcp_servers"]["demo"]["command"] == "demo"
        if fails:
            raise RuntimeError("sync failed")

    sync = Mock(side_effect=synchronize)
    monkeypatch.setattr("pm.sync_venv", sync)
    if fails:
        assert mcp_picker.install_by_name("demo") == 1
        probe.assert_not_called()
        output = capsys.readouterr().out
        assert "✓ Installed" not in output
        assert (
            "hermes pm repair" if recorded else "hermes pm install --extra mcp"
        ) in output
        assert "sync failed" in output
    else:
        catalog._install_entry(entry, enable=True, preloaded_env=None)
        probe.assert_called_once()
        assert "restart" in capsys.readouterr().out.lower()
    assert config.load_config()["mcp_servers"]["demo"]["enabled"] is True
    if recorded:
        sync.assert_called_once_with(explicit=True, repair=True)
    else:
        sync.assert_called_once_with(["mcp"], explicit=True)
