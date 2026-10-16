"""Configured endpoints and TLS reach clients without changing global settings."""

import json
import os

import pytest

import cc_config


def test_common_servers_match_cli_compatibility_file_and_chemistry_is_additive():
    common = cc_config.load_mcp_servers(environ={})
    compat = json.loads((cc_config.PROJECT_DIR / ".mcp.json").read_text())
    assert compat == {"mcpServers": common}
    chemistry = cc_config.load_mcp_servers("chemistry", environ={})
    assert set(chemistry) - set(common) == {"chem", "chem_conversions"}
    assert all(chemistry[name] == value for name, value in common.items())
    chemistry["mle_kit"]["url"] = "changed"
    assert cc_config.load_mcp_servers(environ={}) == common


def test_address_overrides_stay_runtime_only():
    chemistry = cc_config.load_mcp_servers(
        "chemistry", environ={"CHEM_MCP_URL": "http://example.test:5063/mcp"}
    )
    assert chemistry["chem"]["url"] == "http://example.test:5063/mcp"
    assert "url_env" not in chemistry["chem"]
    assert cc_config.load_mcp_servers(
        "chemistry", include_common=False, environ={}
    ).keys() == {"chem", "chem_conversions"}
    with pytest.raises(ValueError, match="profile"):
        cc_config.load_mcp_servers("typo")


def test_tls_overrides_insecure_inheritance_without_mutating_it(monkeypatch):
    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:1234")
    extra = {
        "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        "NODE_EXTRA_CA_CERTS": "/private/ca.pem",
        "CLAUDE_CODE_SHELL_PREFIX": "/executor",
    }
    configured = cc_config.cli_environment(extra)
    assert configured["NODE_TLS_REJECT_UNAUTHORIZED"] == "1"
    assert configured["NODE_EXTRA_CA_CERTS"] == extra["NODE_EXTRA_CA_CERTS"]
    assert configured["CLAUDE_CODE_SHELL_PREFIX"] == extra["CLAUDE_CODE_SHELL_PREFIX"]
    assert extra["NODE_TLS_REJECT_UNAUTHORIZED"] == "0"
    assert os.environ["NODE_TLS_REJECT_UNAUTHORIZED"] == "0"
    assert os.environ["HTTPS_PROXY"] == "http://proxy.example:1234"


@pytest.mark.parametrize("text", ["[]", "mcpServers: []", "mcpServers: {bad: null}"])
def test_invalid_config_is_reported_without_silent_fallback(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ValueError):
        cc_config.load_mcp_servers(config_path=path)
