"""SC8 (ui-claude-code-orchestrator): nginx orchestrator-routing parity.

The base (open) config and the auth (team) config are two independent manual
replicas -- nginx-ui-auth.conf is a full copy of nginx-ui.conf with auth_basic
plus the X-Holo-Client overwrite, not an overlay patch. So the orchestrator
routing (the `map` block that picks the upstream, and the /agents/ proxy_pass
that uses it) must be duplicated one-for-one. If it drifts, a claude_code run on
the team deploy would silently fall through to app:5055 and get a 400. These are
cheap string asserts (the config isn't executed here); the live routing / runner-
down behaviour is proved by the end-to-end run in the spec's Validation Plan.
"""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_CONF = REPO_ROOT / "docker" / "nginx-ui.conf"
AUTH_CONF = REPO_ROOT / "docker" / "nginx-ui-auth.conf"

# The routing key nginx maps on: the X-Holo-Orchestrator header the UI sends.
ROUTING_HEADER_VAR = "$http_x_holo_orchestrator"
UPSTREAM_VAR = "$agents_upstream"
DEFAULT_UPSTREAM = "app:5055"
CLAUDE_CODE_UPSTREAM = "host.docker.internal:5061"


def _extract_map_block(text: str) -> str:
    """Return the orchestrator `map ... { ... }` block, whitespace-normalized."""
    match = re.search(
        r"map\s+\$http_x_holo_orchestrator\s+\$agents_upstream\s*\{(.*?)\}",
        text,
        re.DOTALL,
    )
    assert match is not None, "orchestrator map block not found"
    body = match.group(1)
    return " ".join(body.split())


def _agents_location_block(text: str) -> str:
    """Return the `location /agents/ { ... }` block."""
    match = re.search(r"location\s+/agents/\s*\{(.*?)\}", text, re.DOTALL)
    assert match is not None, "location /agents/ block not found"
    return match.group(1)


def test_both_configs_have_orchestrator_map() -> None:
    for conf in (BASE_CONF, AUTH_CONF):
        text = conf.read_text(encoding="utf-8")
        block = _extract_map_block(text)
        assert f"default {DEFAULT_UPSTREAM}" in block, conf.name
        assert f"claude_code {CLAUDE_CODE_UPSTREAM}" in block, conf.name


def test_map_block_is_identical_across_configs() -> None:
    # Manual-replica parity guard: the two files must carry byte-identical routing
    # (after whitespace normalization) so the team deploy behaves like the open one.
    base = _extract_map_block(BASE_CONF.read_text(encoding="utf-8"))
    auth = _extract_map_block(AUTH_CONF.read_text(encoding="utf-8"))
    assert base == auth


def test_agents_location_uses_mapped_upstream() -> None:
    for conf in (BASE_CONF, AUTH_CONF):
        block = _agents_location_block(conf.read_text(encoding="utf-8"))
        assert f"proxy_pass http://{UPSTREAM_VAR};" in block, conf.name
        # The old fixed literal must be gone -- otherwise claude_code would never
        # be routed to the runner.
        assert "set $app_upstream" not in block, conf.name


def test_claude_code_upstream_literal_present() -> None:
    # The runner listen port is a literal here (nginx does not expand env in conf);
    # this asserts the exact literal both configs must agree on.
    for conf in (BASE_CONF, AUTH_CONF):
        assert CLAUDE_CODE_UPSTREAM in conf.read_text(encoding="utf-8"), conf.name


def test_capacity_stays_codearkt_scoped() -> None:
    # SC6/Non-Goal: /capacity is a process-local app:5055 snapshot and must NOT be
    # rerouted by the orchestrator map.
    for conf in (BASE_CONF, AUTH_CONF):
        text = conf.read_text(encoding="utf-8")
        match = re.search(r"location\s*=\s*/capacity\s*\{(.*?)\}", text, re.DOTALL)
        block = match.group(1) if match else ""
        assert match is not None, conf.name
        # /capacity keeps its own fixed app:5055 upstream, not the orchestrator map.
        assert UPSTREAM_VAR not in block, conf.name
        assert f"set $app_upstream {DEFAULT_UPSTREAM};" in block, conf.name
        assert "proxy_pass http://$app_upstream/capacity;" in block, conf.name


def test_websocket_upgrade_is_limited_to_completion_route():
    for conf in (BASE_CONF, AUTH_CONF):
        text = conf.read_text()
        match = re.search(r"location\s+/ui/ws/\s*\{(.*?)\}", text, re.DOTALL)
        assert match is not None, conf.name
        block = match.group(1)
        assert "proxy_pass http://$ui_api_upstream;" in block
        assert "proxy_set_header Upgrade $http_upgrade;" in block
        assert 'proxy_set_header Connection "upgrade";' in block
        if conf == AUTH_CONF:
            assert "proxy_set_header X-Holo-Client $remote_user;" in block
        ordinary = re.search(r"location\s+/ui/\s*\{(.*?)\}", text, re.DOTALL).group(1)
        assert "Upgrade" not in ordinary
        assert "Upgrade" not in _agents_location_block(text)
