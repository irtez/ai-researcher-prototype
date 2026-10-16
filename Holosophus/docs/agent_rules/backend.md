# Backend Rules

Rules for working with the Holosophus Python/Docker codebase.

## Language and Style

- Python 3.12+, PEP 8, 4-space indent, type hints.
- Formatting: Black + isort. Linting: Ruff.
- Keep diffs minimal. Don't reformat files not touched by the change.

## Commits

Short imperative summaries, scope-prefixed:

```
Holosophus: fix tool routing for writer agent
Holosophus: add speech_to_text to manager tools
```

One concern per commit.

## Vendored Repos

`codearkt/`, `mle_kit_mcp/`, `holosophos/` are checked out locally.
`academia_mcp` is consumed as a Docker image only — never edit locally.

When changes are needed in vendored code:
- Prefer changes in the Holosophus integration layer (docker-compose, .env, scripts).
- If upstream source edits are unavoidable, document them in the commit message.
- When updating a vendored repo, preserve local integration changes.

## Tool Routing

Tool routing has two layers:
1. **MCP servers** mounted globally in `MCP_CONFIG` (`main_agent.py`).
2. **Per-agent allowlists** in `settings.py` (`*_TOOLS` tuples).

To add a new tool:
1. Add the tool implementation to the relevant MCP server.
2. If using a new MCP server, add it to `MCP_CONFIG` in `main_agent.py`.
3. Add the tool name to the correct agent's `*_TOOLS` tuple in `settings.py`.

A tool name not in an agent's allowlist is silently invisible to that agent — typos produce no errors.

## Docker and Security

- LLM-generated code executes inside containers. Never run it on the host.
- The executor container runs read-only with `cap_drop: ALL`, `no-new-privileges`, capped at 0.5 CPU / 1GB / 256 PIDs.
- Don't expose MCP or observability ports publicly.
- Don't commit secrets. Use `.env` for API keys.
- macOS Docker: if host-gateway errors, `export CODEARKT_DISABLE_HOST_GATEWAY=1`.
