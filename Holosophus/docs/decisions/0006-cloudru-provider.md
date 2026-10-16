# 0006. cloud.ru ML Space as second remote-GPU provider

- **Status**: Accepted
- **Date**: 2026-05-03
- **Deciders**: @aleksei

## Context

`mle_kit_mcp` was Vast.ai-only: `tools/remote_gpu.py` always called
`vastai_sdk.VastAI` (offers, instance metadata, lifecycle), and hardcoded
`username="root"` + `/root` as the remote home in five places. Vast is paid by a
personal card.

A team has now granted compute on cloud.ru ML Space — Jupyter Server endpoint,
plain SSH on port 2222 with a colleague-issued private key. No SDK, no
lifecycle: the instance is created and reserved by the team in their UI, we
just SSH into it. Username is `jovyan`, home is `/home/jovyan`.

The mle-solver-remote agent prompt is already provider-neutral ("GPU
clusters"); only the MCP layer was Vast-coupled.

Two relevant constraints:
- Developer in RU on a corporate OpenConnect VPN with a Kazakh exit. cloud.ru
  must see a Russian residential IP, not the VPN exit. Validated 2026-05-03 to
  hold under the current split-tunnel VPN profile (cloud.ru subnet
  `89.232.0.0` not in `utun4` routes); ongoing notes in
  [`docs/cloudru-mlspace.md`](../cloudru-mlspace.md).
- The colleague's private key lives at `~/.ssh/sber_id_rsa` (chmod 600) on the
  developer host. It must not enter the repo or any container image.

## Decision

Add an env-driven provider switch in `mle_kit_mcp`:

- `REMOTE_PROVIDER: Literal["vast", "cloudru"] = "vast"` in
  `mle_kit_mcp/mle_kit_mcp/settings.py`. Default `vast` keeps every existing
  setup unchanged.
- For `cloudru`: `CLOUDRU_SSH_HOST` / `_PORT` / `_USER` / `_KEY` /
  `_REMOTE_HOME` describe the static endpoint; no SDK calls.
- `tools/remote_gpu.py` dispatches in `get_instance()` between
  `launch_instance(get_sdk(), ...)` (vast) and `build_cloudru_instance()`
  (cloudru). The cloudru branch builds `InstanceInfo` directly from settings
  and probes via `run_command(info, "echo cloudru-ok")` — no Vast SDK import is
  reached.
- `vastai_sdk` import is moved inside `get_sdk()` so cloudru-only setups don't
  need it installed/configured.
- `cleanup_instance()` is gated to never call `_sdk.destroy_instance(...)` when
  `REMOTE_PROVIDER != "vast"` — cloud.ru instances are externally owned by the
  team, destroying one would be destructive against shared state.
- `InstanceInfo.remote_home` (default `"/root"`) replaces the hardcoded
  `"/root"` in `send_scripts`, `remote_download`, `create_remote_text_editor`.
  Vast keeps `/root`; cloud.ru uses `/home/jovyan`.

Container plumbing lives in the standard `docker-compose.yml` and
`docker-compose.mcp.yml` directly: the `mle_kit` (and `mle_kit_2`, parallel
profile) services declare `REMOTE_PROVIDER`, `CLOUDRU_SSH_*` env vars with
`vast`/empty defaults, and a `${CLOUDRU_SSH_KEY_HOST_PATH:-/dev/null}`
bind-mount at `/root/.ssh/cloudru_key`. Vast users get a harmless 0-byte
mount they never read. cloud.ru users set `REMOTE_PROVIDER=cloudru` and
`CLOUDRU_SSH_KEY_HOST_PATH=~/.ssh/sber_id_rsa` in `.env` (or shell). No
separate compose override file: an earlier `docker-compose.cloudru.yml` was
folded back in to keep the surface area small.

We deliberately did **not** introduce a `RemoteGPUProvider` class hierarchy.
There are two providers, both behind the same SSH/rsync transport, and zero
callers asking for runtime polymorphism. Adding one would be premature.

We also did not make per-task provider routing (the agent picks per
`remote_bash` call). Both providers are not warm simultaneously in our usage,
and the env switch is operationally simpler.

## Consequences

- `InstanceInfo.remote_home` is now load-bearing: any future tool that
  constructs a remote path **must** read it from the instance, not assume
  `/root`. This is captured as an entry in
  [`docs/agent_rules/anti-patterns.md`](../agent_rules/anti-patterns.md).
- `get_sdk()` must not be called outside the vast branch of `get_instance()`.
  Calling it under `REMOTE_PROVIDER=cloudru` would crash if `vastai_sdk` is not
  installed and (worse) `destroy_instance(0)` against a partner-owned id is
  destructive. Same anti-patterns entry covers this.
- Operational surface for cloud.ru lives in
  [`docs/cloudru-mlspace.md`](../cloudru-mlspace.md) — a
  living doc updated as quirks of the cloud are discovered.
- The colleague's private key never enters the repo or any image. Loss of the
  key would expose the partner team's NFS workspace; treat it like a
  credential — chmod 600, do not commit, do not paste into chats.
- If the corporate VPN profile ever switches to full-tunnel (intercepting all
  traffic, not just specific /12s), the bypass route from
  [`~/notes/russian-traffic-via-openconnect-vpn.md`](file:~/notes/russian-traffic-via-openconnect-vpn.md)
  becomes mandatory and `~/bin/sber-up.sh` must be run with `SBER_IFACE=en1`
  (or whatever the real LAN interface is at that point).
- We are accepting the cost of two parallel remote-GPU code paths until a
  third provider arrives — at which point this decision should be revisited
  and the abstraction extracted.
- `MLE_SOLVER_IS_REMOTE` is now respected by both orchestrators: CodeArkt
  picks one mle-solver YAML (as before), and `scripts/sync_claude_agents.py`
  filters which `mle-solver*.md` are rendered for Claude Code. Unset = both
  agents available (current default); `true|false` = single-agent isolation.
  Used to launch parallel heavy/light research sessions without sub-agent
  contention.
- `recieve_rsync` and `send_rsync` now try rsync first and fall back to `scp
  -r` when the remote side has no rsync (cloud.ru jovyan does not). Vast.ai
  flow is unchanged because rsync still succeeds there. The `--max-size=10m`
  protection only applies on the rsync path; agents downloading on cloud.ru
  via the scp fallback should not pull arbitrary-large files. Anti-pattern
  entry covers the invariant.
