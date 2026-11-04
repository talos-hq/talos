"""Missing optional MCP SDK must be actionable (regression for #87)."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from tools import mcp_tool_common as common


@pytest.mark.parametrize(
    "features,command",
    [(None, "hermes pm install --extra mcp"), (["mcp"], "hermes pm repair")],
)
def test_missing_sdk_diagnosis(monkeypatch, features, command):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    monkeypatch.setattr("pm.features.read_features", lambda: features)
    reason = common.mcp_sdk_diagnosis()
    assert command in reason
    assert "restart" in reason.lower()


def test_present_sdk(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    assert common.mcp_sdk_diagnosis() is None


def test_discovery_warns_once_only_with_servers(monkeypatch, caplog):
    from tools import mcp_tool as core, mcp_tool_discovery as discovery

    monkeypatch.setattr(core, "_MCP_AVAILABLE", False)
    monkeypatch.setattr(discovery, "_SDK_WARNING_EMITTED", False, raising=False)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    monkeypatch.setattr("pm.features.read_features", list)
    monkeypatch.setattr(discovery._config, "_load_mcp_config", dict)
    with caplog.at_level(logging.WARNING, logger="tools.mcp_tool"):
        discovery.discover_mcp_tools()
        discovery.register_mcp_servers({})
        assert not caplog.records
        monkeypatch.setattr(
            discovery._config, "_load_mcp_config", lambda: {"demo": {"command": "demo"}}
        )
        discovery.discover_mcp_tools(allowed_mcp_names=[])
        assert len(caplog.records) == 1
        discovery.discover_mcp_tools()
        discovery.register_mcp_servers({"demo": {"command": "demo"}})
        discovery.discover_mcp_tools()
    assert len(caplog.records) == 1
    assert "hermes pm install --extra mcp" in caplog.text


def test_transport_missing_sdk_message(monkeypatch):
    from tools import mcp_tool as core
    from tools.mcp_tool_transport import MCPServerTransportMixin

    monkeypatch.setattr(core, "_MCP_AVAILABLE", False)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    monkeypatch.setattr("pm.features.read_features", list)
    with pytest.raises(ImportError) as error:
        asyncio.run(
            MCPServerTransportMixin._run_stdio(SimpleNamespace(name="demo"), {})
        )
    assert "hermes pm install --extra mcp" in str(error.value)
    assert "hermes setup" not in str(error.value)
