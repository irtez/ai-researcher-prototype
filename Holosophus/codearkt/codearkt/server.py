import asyncio
import contextlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastmcp import FastMCP
from fastmcp import settings as fastmcp_settings
from fastmcp.client.transports import (
    ClientTransport,
    SSETransport,
    StreamableHttpTransport,
)
from fastmcp.mcp_config import (
    MCPConfig,
    RemoteMCPServer,
    StdioMCPServer,
    infer_transport_type_from_url,
)
from pydantic import BaseModel, Field
from sse_starlette.sse import AppStatus

from codearkt import auth
from codearkt.admission import AdmissionLedger, global_limit, per_owner_limit
from codearkt.codeact import CodeActAgent
from codearkt.dispatch import (
    SessionDispatcher,
    host_metrics,
    mle_kit_gpu_telemetry,
    mle_kit_resources,
)
from codearkt.event_bus import AgentEventBus, EventType
from codearkt.llm import ChatMessage
from codearkt.metrics import TokenUsageStore
from codearkt.run_config import (
    clear_compute_mode,
    clear_model_name,
    clear_owner,
    get_compute_mode,
    set_compute_mode,
    set_model_name,
    set_owner,
)
from codearkt.session_id import ensure_session_scaffold, read_owner_marker, write_owner_marker
from codearkt.settings import settings
from codearkt.util import append_jsonl_atomic, find_free_port, get_unique_id


fastmcp_settings.stateless_http = True
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _log(message: str, session_id: str, level: int = logging.INFO) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"| {timestamp} | {session_id:<8} | {message}"
    logger.log(level, message)


async def _wait_until_started(server: uvicorn.Server) -> None:
    while not server.started:
        await asyncio.sleep(0.05)


def reset_app_status() -> None:
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None


class AgentRequest(BaseModel):  # type: ignore
    messages: List[ChatMessage]
    session_id: Optional[str] = None
    input_files: List[str] = Field(default_factory=list)
    stream: bool = False
    compute_mode: Optional[str] = None
    # Per-session model override (UI model selector). Empty/None means "use
    # the LLM's own configured default" -- same optional-field contract as
    # compute_mode.
    model_name: Optional[str] = None
    effort: Optional[str] = None
    model_configuration: bool = False
    # SC1 (ui-claude-code-orchestrator): the UI now stamps which orchestrator a
    # run is addressed to. This CodeArkt server only serves "codearkt"; the
    # value is duplicated into the X-Holo-Orchestrator header that nginx routes
    # on. Default "codearkt" keeps the contract backward-compatible -- existing
    # clients (and nested delegate calls) that omit the field are unaffected.
    orchestrator: Optional[str] = "codearkt"


class AgentCard(BaseModel):  # type: ignore
    name: str
    description: str


AGENT_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def create_agent_endpoint(
    agent_app: FastAPI,
    agent_instance: CodeActAgent,
    server_host: str,
    server_port: int,
    event_bus: AgentEventBus,
    dispatcher: SessionDispatcher,
    gpu_dispatcher: SessionDispatcher,
    token_usage_store: TokenUsageStore | None = None,
) -> Callable[..., Any]:
    @agent_app.post(f"/{agent_instance.name}")  # type: ignore
    async def agent_tool(
        request: AgentRequest,
        x_holo_client: Optional[str] = Header(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Any:
        # SC1 (ui-claude-code-orchestrator): reject any run addressed to a
        # different orchestrator. The UI routes on the X-Holo-Orchestrator
        # header (nginx map); a "claude_code" value reaching this codearkt
        # server means the routing desynced, so fail loud with 400 instead of
        # silently running the CodeArkt pipeline for a request meant for the
        # claude_code runner. `None`/absent and "codearkt" both stay on the
        # default (unchanged) path -- nested delegate calls omit the field.
        if request.orchestrator is not None and request.orchestrator != "codearkt":
            raise HTTPException(
                status_code=400,
                detail=(
                    "This server only serves orchestrator='codearkt', "
                    f"got '{request.orchestrator}'"
                ),
            )

        session_id = request.session_id or get_unique_id()

        # SC4/SC5/SC6 (session run-slot dispatcher): only ROOT runs are gated,
        # authed, and owner-tagged. A nested delegate call (agent__mle_solver
        # etc.) reuses the same session_id -- it must NOT be re-authed (it
        # carries no Authorization header) nor re-counted against capacity.
        # Computed here (before register_task tags the session with its
        # first-seen agent below) so the auth gate and owner all key off it.
        is_root_run = session_id not in event_bus.root_agent_name

        # App-level team auth (docs/plans/2026-07-05-ui-login-and-per-account-queue.md):
        # when a login list is configured (HOLO_AUTH_USERS), starting a run
        # requires a valid signed bearer token. The verified username -- NOT the
        # forgeable X-Holo-Client header -- becomes the run's owner (marker,
        # Langfuse `user`, per-account quota). With auth disabled (dev/smoke/CI)
        # nothing changes: the owner falls back to X-Holo-Client as before.
        authenticated_user = auth.verify_token(authorization) if auth.auth_enabled() else None
        if is_root_run and auth.auth_enabled() and not authenticated_user:
            raise HTTPException(status_code=401, detail="login required to start a run")
        owner = authenticated_user or x_holo_client

        if request.input_files:
            prepare = getattr(agent_instance, "input_files_preparer", None)
            if not is_root_run or prepare is None:
                raise HTTPException(400, "Attachments are only supported on the initial manager request")
            if (
                not request.messages
                or request.messages[-1].role != "user"
                or not isinstance(request.messages[-1].content, str)
            ):
                raise HTTPException(400, "Attachments require a text user message")
            try:
                context = prepare(
                    Path(os.environ.get("WORKSPACE_DIR", "workdir")),
                    session_id,
                    owner,
                    request.input_files,
                )
            except PermissionError as error:
                raise HTTPException(404, "Session not found") from error
            except (ValueError, OSError) as error:
                raise HTTPException(400, "Invalid session inputs") from error
            message = request.messages[-1]
            request.messages[-1] = message.model_copy(update={"content": message.content + context})

        # SC3: register this run's compute_mode (Auto/Local/GPU) in the
        # session-keyed registry so the solver sub-agent can gate its tool set
        # without mutating the shared agent instance (see codeact.py
        # `_effective_tool_names`). Nested delegate calls (manager ->
        # agent__mle_solver etc., same session_id, see container/tools.py
        # `_call_agent`) don't carry `compute_mode` in their payload, so a
        # `None` here must NOT downgrade an already-registered mode back to
        # Auto -- only an explicit value updates the registry.
        if request.compute_mode is not None:
            set_compute_mode(session_id, request.compute_mode)

        # UI model selector: register this run's model override the same way
        # as compute_mode -- a truthy check so an empty string ("use default")
        # never gets registered, and nested delegate calls (which omit the
        # field) can't clobber an already-registered override.
        if request.model_configuration and is_root_run:
            resolver = getattr(agent_instance.llm, "config_resolver", None)
            if resolver is None:
                raise HTTPException(status_code=400, detail="Model configurations are unavailable")
            try:
                configuration = resolver(request.model_name, request.effort, strict=True)
                from codearkt.run_config import set_model_config
                set_model_config(session_id, configuration)
                set_model_name(session_id, configuration["config_id"])
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
        if request.effort:
            from codearkt.run_config import set_model_effort
            set_model_effort(session_id, request.effort)
        if request.model_name:
            set_model_name(session_id, request.model_name)

        # Auth/team deploy: register this run's owner so the Langfuse tracer can
        # stamp it as the trace `user`. `owner` is the verified login when
        # app-level auth is on (the app checks a signed token, not a header nginx
        # rewrote); otherwise it falls back to the X-Holo-Client browser id.
        # Nested delegate calls carry no header -- `set_owner` ignores blanks so
        # they can't wipe an already-registered owner (same contract as compute_mode).
        set_owner(session_id, owner)

        # SC1: bridge paperbanana's shared OUTPUT_DIR into this session's cwd so
        # concurrent UI sessions don't overwrite each other's figures. SC2: tag
        # the session with the browser-client owner (X-Holo-Client) so ui-api can
        # filter the session list per user. The UI hits this endpoint (not
        # session_id.generate_session_id), so both must happen here. Resolve the
        # workspace root from WORKSPACE_DIR exactly as
        # holosophos.ui_api.resolve_workspace_root does (default "workdir"):
        # codearkt.settings has no WORKSPACE_DIR field and importing
        # holosophos.settings would invert the dependency direction.
        try:
            workspace_root = Path(os.environ.get("WORKSPACE_DIR", "workdir")).expanduser().resolve()
            session_dir = workspace_root / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            write_owner_marker(session_dir, owner)
            ensure_session_scaffold(session_dir)
            if request.model_configuration and is_root_run:
                import json
                metadata = session_dir / ".holosophus_ui"
                metadata.mkdir(exist_ok=True)
                (metadata / "model").write_text(configuration["config_id"], encoding="utf-8")
                (metadata / "settings.json").write_text(json.dumps({
                    "model_label": configuration["label"],
                    "effort": configuration.get("effort"),
                    "compute_mode": request.compute_mode,
                }), encoding="utf-8")
        except OSError:
            _log("could not prepare session scaffold", session_id, level=logging.WARNING)

        async def _run_gated() -> Any:
            async def _on_queued(sid: str, position: int, depth: int) -> None:
                await event_bus.publish_status(
                    sid,
                    agent_instance.name,
                    EventType.SESSION_QUEUED,
                    resource="session",
                    position=position,
                    depth=depth,
                )

            async def _on_running(sid: str) -> None:
                await event_bus.publish_status(
                    sid,
                    agent_instance.name,
                    EventType.SESSION_RUNNING,
                    resource="session",
                )

            async def _on_position(sid: str, position: int, depth: int) -> None:
                await event_bus.publish_status(
                    sid,
                    agent_instance.name,
                    EventType.SESSION_POSITION,
                    resource="session",
                    position=position,
                    depth=depth,
                )

            # SC5 ([B]/[C], multi-user concurrent UI): a compute_mode=GPU root
            # run must hold a GPU slot for its WHOLE run, not just the tool
            # call that happens to touch the GPU (mle_kit's box-level lease
            # dispatches sync tools serially, so it can't expose a visible
            # queue position -- see docs/plans/2026-06-30-multiuser-concurrent-ui.md
            # SC5 resolution). Acquire order matters: GPU slot FIRST, then the
            # session slot, so a run waiting for a GPU never holds (and
            # starves) a session slot -- avoids deadlocking other sessions
            # against a GPU run that hasn't even started yet.
            async def _gpu_queued(sid: str, position: int, depth: int) -> None:
                await event_bus.publish_status(
                    sid,
                    agent_instance.name,
                    EventType.SESSION_QUEUED,
                    resource="gpu",
                    position=position,
                    depth=depth,
                )

            async def _gpu_running(sid: str) -> None:
                await event_bus.publish_status(
                    sid,
                    agent_instance.name,
                    EventType.SESSION_RUNNING,
                    resource="gpu",
                )

            async def _gpu_position(sid: str, position: int, depth: int) -> None:
                await event_bus.publish_status(
                    sid,
                    agent_instance.name,
                    EventType.SESSION_POSITION,
                    resource="gpu",
                    position=position,
                    depth=depth,
                )

            needs_gpu = get_compute_mode(session_id) == "GPU"
            # SC5 addendum (Selectel, see `_mle_kit_multi_card_gpu`): once
            # mle_kit reports live multi-card discovery, this app-level
            # semaphore is a static single-box ceiling that is no longer the
            # real GPU admission gate -- mle_kit's own per-session card
            # registry is. Gating on top of it here would re-impose that
            # static ceiling on a host that actually has many free cards,
            # queuing a 2nd GPU run that could start immediately (and
            # disagreeing with what `/capacity` already displays for `gpu`).
            # So only acquire this slot for single-box providers
            # (vast/cloudru); otherwise publish "running" immediately -- there
            # is no app-level queue position to report in that case.
            gate_gpu_here = needs_gpu and _mle_kit_multi_card_gpu() is None
            gpu_acquired = False
            try:
                if needs_gpu:
                    if gate_gpu_here:
                        await gpu_dispatcher.acquire(
                            session_id, on_queued=_gpu_queued, on_running=_gpu_running
                        )
                        gpu_acquired = True
                    else:
                        await _gpu_running(session_id)

                await dispatcher.acquire(
                    session_id,
                    owner=owner,
                    on_queued=_on_queued,
                    on_running=_on_running,
                    on_position=_on_position,
                )
                try:
                    return await agent_instance.ainvoke(
                        messages=request.messages,
                        session_id=session_id,
                        event_bus=event_bus,
                        token_usage_store=token_usage_store,
                        server_host=server_host,
                        server_port=server_port,
                    )
                finally:
                    await dispatcher.release(session_id, on_position=_on_position)
            finally:
                if gpu_acquired:
                    await gpu_dispatcher.release(session_id, on_position=_gpu_position)

        async def _run_ungated() -> Any:
            return await agent_instance.ainvoke(
                messages=request.messages,
                session_id=session_id,
                event_bus=event_bus,
                token_usage_store=token_usage_store,
                server_host=server_host,
                server_port=server_port,
            )

        def _start_agent_task() -> asyncio.Task[Any]:
            run_coro = (
                _run_gated() if (is_root_run and settings.CAPACITY_ENABLED) else _run_ungated()
            )
            task = asyncio.create_task(run_coro)
            event_bus.register_task(
                session_id=session_id,
                agent_name=agent_instance.name,
                task=task,
            )
            return task

        async def stream_response(is_root_agent: bool) -> AsyncGenerator[str, None]:
            try:
                async for event in event_bus.stream_events(session_id):
                    yield event.model_dump_json() + "\n"
            finally:
                if is_root_agent:
                    clear_compute_mode(session_id)
                    clear_model_name(session_id)
                    clear_owner(session_id)

        task = _start_agent_task()

        # SC3: only the run's root agent (first to register a task for this
        # session_id, see event_bus.register_task) clears the compute_mode
        # entry once its run ends -- nested delegate calls (agent__mle_solver
        # etc.) share the same session_id but must leave the registry intact
        # for the rest of the run.
        is_root_agent = event_bus.root_agent_name.get(session_id) == agent_instance.name

        if request.stream:
            return StreamingResponse(
                stream_response(is_root_agent),
                media_type="application/x-ndjson",
                headers=AGENT_RESPONSE_HEADERS,
            )
        else:
            try:
                result = await task
            finally:
                if is_root_agent:
                    clear_compute_mode(session_id)
                    clear_model_name(session_id)
                    clear_owner(session_id)
            return result

    return agent_tool  # type: ignore


class CancelRequest(BaseModel):  # type: ignore
    session_id: str


class LoginRequest(BaseModel):  # type: ignore
    username: str
    password: str


def get_agent_app(
    agent: CodeActAgent,
    server_host: str,
    server_port: int,
    event_bus: AgentEventBus,
    token_usage_store: TokenUsageStore | None = None,
) -> FastAPI:
    agent_app = FastAPI(
        title="CodeArkt Agent App",
        description="Agent app for CodeArkt",
        version="1.0.0",
    )

    # SC4/SC5/SC6: one dispatcher per app instance, created once here and
    # shared by every agent endpoint below (all ROOT runs across the whole
    # app compete for the same MAX_ACTIVE_SESSIONS pool). Stashed on
    # `.state` so `get_main_app` can wire `GET /capacity` to it without
    # threading another parameter through the call chain.
    #
    # Team-login (docs/plans/2026-07-05-ui-login-and-per-account-queue.md): when
    # app-level auth is on, the session pool becomes a cross-process ledger so
    # the "6 total / 3 per account" caps count across BOTH runner processes
    # (this app + the host cc_runner) and add a per-owner sub-cap. With auth off
    # it stays the original single-process semaphore -- identical to before.
    if auth.auth_enabled():
        dispatcher = SessionDispatcher(
            global_limit(),
            ledger=AdmissionLedger(),
            per_owner_limit=per_owner_limit(),
            engine="codearkt",
        )
    else:
        dispatcher = SessionDispatcher(settings.MAX_ACTIVE_SESSIONS)
    agent_app.state.dispatcher = dispatcher

    # SC5 ([B]/[C]): a second dispatcher instance dedicated to the GPU
    # resource class, gating compute_mode=GPU root runs for their whole run
    # (see `_run_gated` in create_agent_endpoint). Kept separate from the
    # session dispatcher above so GPU admission has its own slot count
    # (`GPU_SLOTS`, pool-ready) and its own queue/position bookkeeping.
    gpu_dispatcher = SessionDispatcher(settings.GPU_SLOTS)
    agent_app.state.gpu_dispatcher = gpu_dispatcher

    agent_cards = []
    for sub_agent in agent.get_all_agents():
        agent_cards.append(AgentCard(name=sub_agent.name, description=sub_agent.description))
        create_agent_endpoint(
            agent_app=agent_app,
            agent_instance=sub_agent,
            server_host=server_host,
            server_port=server_port,
            event_bus=event_bus,
            dispatcher=dispatcher,
            gpu_dispatcher=gpu_dispatcher,
            token_usage_store=token_usage_store,
        )

    async def cancel_session(request: CancelRequest) -> Dict[str, str]:
        _log("Finishing session for all agents", request.session_id)
        event_bus.finish_session(request.session_id)
        # SC3: belt-and-suspenders -- also drop any lingering compute_mode /
        # owner registry entries on explicit cancellation (defense-in-depth
        # alongside the root-agent-scoped clear in agent_tool above).
        clear_compute_mode(request.session_id)
        clear_model_name(request.session_id)
        clear_owner(request.session_id)
        return {"status": "cancelled", "session_id": request.session_id}

    async def get_agents() -> List[AgentCard]:
        return agent_cards

    async def session_events(
        session_id: str,
        x_holo_client: Optional[str] = Header(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        """Re-attach to a session's event stream (replay + live).

        The run itself is a detached task on the event bus, so the NDJSON
        stream a browser gets from POST /agents/<name> is just a *view* -- when
        that connection drops (network blip, laptop sleep, tab reload) the run
        keeps going with no way back in. This endpoint is the way back in: it
        replays the bus's bounded history, then follows live events, ending on
        the root agent's terminal event exactly like the original stream.

        Owner scoping mirrors ui-api: identity is the verified login when
        app-level auth is on, else the browser client id; the durable owner
        marker on disk is the reference (the in-process owner registry is
        cleared the moment the original stream ends -- precisely the situation
        this endpoint exists for). Mismatch is a 404 indistinguishable from an
        unknown session, so it never confirms someone else's run exists.
        """
        if not session_id or any(sep in session_id for sep in ("/", "\\", "..")):
            raise HTTPException(status_code=404, detail="No run for this session")
        if not event_bus.is_known(session_id):
            raise HTTPException(status_code=404, detail="No run for this session")

        identity = (
            auth.verify_token(authorization) if auth.auth_enabled() else None
        ) or x_holo_client
        workspace_root = Path(os.environ.get("WORKSPACE_DIR", "workdir")).expanduser().resolve()
        owner_marker = read_owner_marker(workspace_root / session_id)
        if owner_marker is not None:
            if owner_marker != (identity or "").strip():
                raise HTTPException(status_code=404, detail="No run for this session")
        else:
            # No owner marker = a headless/legacy session. Mirror ui-api's
            # policy (UI_SHOW_LEGACY_SESSIONS, default off) instead of failing
            # open: unowned must not mean world-readable. Env-read because that
            # setting lives in holosophos and codearkt must not import it.
            show_legacy = os.environ.get("UI_SHOW_LEGACY_SESSIONS", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if not show_legacy:
                raise HTTPException(status_code=404, detail="No run for this session")

        async def stream() -> AsyncGenerator[str, None]:
            async for event in event_bus.stream_events(session_id):
                yield event.model_dump_json() + "\n"

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson",
            headers=AGENT_RESPONSE_HEADERS,
        )

    async def login(request: LoginRequest) -> Dict[str, Any]:
        # App-level team login: verify the plaintext password against the
        # env-configured allowed-user list and mint a short-lived signed bearer
        # token. Reachable at POST /agents/login (nginx already proxies
        # /agents/*), verified independently by both runner processes -- see
        # codearkt.auth. 401 on bad creds; never reveals which half was wrong.
        if not auth.check_password(request.username, request.password):
            raise HTTPException(status_code=401, detail="invalid login or password")
        token = auth.mint_token(request.username)
        return {
            "token": token,
            "username": request.username,
            "expires_in": auth.token_ttl_seconds(),
        }

    agent_app.get("/list")(get_agents)
    agent_app.get("/events/{session_id}")(session_events)
    agent_app.post("/cancel")(cancel_session)
    agent_app.post("/login")(login)
    return agent_app


def get_mcp_app(
    mcp_config: Optional[Dict[str, Any]],
    additional_tools: Optional[Dict[str, Callable[..., Any]]] = None,
    add_prefixes: bool = True,
) -> FastAPI:
    mcp: FastMCP[Any] = FastMCP(name="Codearkt MCP Proxy")
    if mcp_config:
        cfg = MCPConfig.from_dict(mcp_config)
        server_count = len(cfg.mcpServers)

        for name, server in cfg.mcpServers.items():
            transport: Optional[ClientTransport] = None
            if isinstance(server, RemoteMCPServer):
                transport_type = server.transport or infer_transport_type_from_url(server.url)
                if transport_type == "sse":
                    transport = SSETransport(
                        server.url,
                        headers=server.headers,
                        auth=server.auth,
                        sse_read_timeout=settings.PROXY_SSE_READ_TIMEOUT,
                    )
                else:
                    transport = StreamableHttpTransport(
                        server.url,
                        headers=server.headers,
                        auth=server.auth,
                        sse_read_timeout=settings.PROXY_SSE_READ_TIMEOUT,
                    )
            elif isinstance(server, StdioMCPServer):
                transport = server.to_transport()

            assert transport is not None, "Transport is required for the MCP server in the config"
            sub_proxy = FastMCP.as_proxy(backend=transport)
            prefix: Optional[str] = None if server_count == 1 else name
            if not add_prefixes:
                prefix = None
            mcp.mount(prefix=prefix, server=sub_proxy)

    if additional_tools:
        for name, tool in additional_tools.items():
            mcp.tool(tool, name=name)

    return mcp.http_app()


def _mle_kit_multi_card_gpu() -> Optional[Dict[str, int]]:
    """mle_kit's own `gpu` snapshot, but only once it reports live multi-card
    discovery (`slots > 1`, e.g. Selectel post-discovery) -- `None` otherwise
    (file absent/malformed, or a single-box provider like vast/cloudru that
    never lifts `slots` past the static default).

    Single source of truth for the boundary between the two GPU admission
    models, shared by `_capacity_handler` (which snapshot to *display*) and
    `_run_gated` (which layer actually *gates* a GPU root run below). They
    must agree: `_run_gated`'s own app-level `gpu_dispatcher` is a static
    single-box semaphore (`settings.GPU_SLOTS`, never reconciled with
    mle_kit's dynamic discovery) that would otherwise queue a 2nd GPU run
    behind the 1st even while mle_kit reports many free cards -- see SC5
    addendum in docs/plans/2026-07-01-selectel-provider-per-session-gpu.md.
    """
    try:
        mle_kit_gpu = mle_kit_resources()["gpu"]
    except Exception:
        return None
    if mle_kit_gpu["slots"] > 1:
        return mle_kit_gpu
    return None


def _capacity_handler(
    dispatcher: SessionDispatcher,
    gpu_dispatcher: SessionDispatcher,
) -> Callable[[Request], Awaitable[JSONResponse]]:
    async def capacity(request: Request) -> JSONResponse:
        # SC6: never 500 on a metrics hiccup -- a broken capacity readout
        # shouldn't take down an otherwise-healthy app.
        #
        # Team-login: the caller identity (verified token, else X-Holo-Client)
        # lets the ledger report this account's own active/limit as `yours`, so
        # the UI can show "your runs: N/3". Display-only, so a header is enough.
        caller = auth.verify_token(request.headers.get("authorization")) or request.headers.get(
            "x-holo-client"
        )
        try:
            sessions = dispatcher.snapshot(owner=caller)
        except Exception:
            sessions = {"active": 0, "limit": settings.MAX_ACTIVE_SESSIONS, "queued": 0}
        yours = None
        if caller and isinstance(sessions, dict) and "owner_active" in sessions:
            yours = {
                "active": sessions.get("owner_active", 0),
                "limit": sessions.get("per_owner_limit"),
            }
        # Keep the `sessions` block to its stable 3-key shape; per-account detail
        # rides in the separate `yours` block (null when unauthenticated/auth off).
        sessions = {
            "active": sessions.get("active", 0),
            "limit": sessions.get("limit"),
            "queued": sessions.get("queued", 0),
        }
        try:
            host = host_metrics()
        except Exception:
            host = {
                "load1": 0.0,
                "load5": 0.0,
                "load15": 0.0,
                "cpu_count": 0,
                "mem_used_pct": None,
            }
        # SC5/SC6: `gpu` normally comes from the app-level GPU admission gate
        # (`_run_gated` in create_agent_endpoint), not mle_kit's box-level
        # lease -- only the app dispatcher has a visible queue position (see
        # module docstring in dispatch.py). That holds for the single-box
        # providers (vast/cloudru) this gate was sized for. But a shared
        # multi-card host (Selectel) lifts mle_kit's own `gpu.slots` above the
        # static single-box default once live discovery runs, and at that
        # point mle_kit's per-session registry -- not this app-wide semaphore
        # -- is the real ground truth for how many cards are busy/free.
        # `_mle_kit_multi_card_gpu` is the single predicate deciding this --
        # `_run_gated` below uses the SAME predicate to decide whether the
        # app-level `gpu_dispatcher` actually gates admission, so this
        # display can never disagree with what was actually enforced.
        mle_kit_gpu = _mle_kit_multi_card_gpu()
        if mle_kit_gpu is not None:
            gpu = mle_kit_gpu
        else:
            try:
                gpu_snapshot = gpu_dispatcher.snapshot()
                gpu = {
                    "busy": gpu_snapshot["active"],
                    "waiting": gpu_snapshot["queued"],
                    "slots": gpu_snapshot["limit"],
                }
            except Exception:
                gpu = {"busy": 0, "waiting": 0, "slots": settings.GPU_SLOTS}
        # SC5/SC6: local_exec always surfaces mle_kit's own lease state
        # (written to the shared workdir) -- that resource class is gated
        # inside mle_kit, not by this app-level dispatcher, regardless of
        # which GPU provider is configured.
        try:
            local_exec = mle_kit_resources()["local_exec"]
        except Exception:
            local_exec = {"busy": 0, "waiting": 0, "slots": 0}
        # Per-card occupancy for the UI's usage view. Optional and never fatal:
        # if mle_kit hasn't published it, the key is simply absent.
        try:
            gpu_telemetry = mle_kit_gpu_telemetry()
        except Exception:
            gpu_telemetry = None
        payload: Dict[str, Any] = {
            "sessions": sessions,
            "yours": yours,
            "gpu": gpu,
            "local_exec": local_exec,
            "host": host,
        }
        if gpu_telemetry is not None:
            payload["gpu_telemetry"] = gpu_telemetry
        return JSONResponse(payload)

    return capacity


def get_main_app(
    agent: CodeActAgent,
    event_bus: AgentEventBus,
    token_usage_store: Optional[TokenUsageStore] = None,
    mcp_config: Optional[Dict[str, Any]] = None,
    server_host: str = settings.DEFAULT_SERVER_HOST,
    server_port: int = settings.DEFAULT_SERVER_PORT,
    additional_tools: Optional[Dict[str, Callable[..., Any]]] = None,
    add_mcp_server_prefixes: bool = True,
) -> FastAPI:
    agent_app = get_agent_app(
        agent=agent,
        server_host=server_host,
        server_port=server_port,
        event_bus=event_bus,
        token_usage_store=token_usage_store,
    )
    mcp_app = get_mcp_app(mcp_config, additional_tools, add_prefixes=add_mcp_server_prefixes)
    mcp_app.mount("/agents", agent_app)

    # SC6: GET /capacity lives at the app root (not under /agents, which is
    # reserved for per-agent tool routes), so it's reachable directly at
    # http://app:5055/capacity, matching /agents/list's sibling status.
    dispatcher: SessionDispatcher = agent_app.state.dispatcher
    gpu_dispatcher: SessionDispatcher = agent_app.state.gpu_dispatcher
    mcp_app.add_route("/capacity", _capacity_handler(dispatcher, gpu_dispatcher), methods=["GET"])

    return mcp_app


async def _shutdown_server(
    server: uvicorn.Server,
    server_task: asyncio.Task[None],
    timeout: float = 10.0,
) -> None:
    AppStatus.should_exit = True
    server.should_exit = True

    try:
        await asyncio.wait_for(server_task, timeout=timeout / 2.0)
        return
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    server.force_exit = True
    try:
        await asyncio.wait_for(server_task, timeout=timeout / 2.0)
        return
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    with contextlib.suppress(asyncio.CancelledError):
        server_task.cancel()
        await server_task


def run_server(
    agent: CodeActAgent,
    mcp_config: Dict[str, Any],
    host: str = settings.DEFAULT_SERVER_HOST,
    port: int = settings.DEFAULT_SERVER_PORT,
    additional_tools: Optional[Dict[str, Callable[..., Any]]] = None,
    add_mcp_server_prefixes: bool = True,
) -> None:
    event_bus = AgentEventBus()
    app = get_main_app(
        agent=agent,
        mcp_config=mcp_config,
        server_host=host,
        server_port=port,
        additional_tools=additional_tools,
        event_bus=event_bus,
        add_mcp_server_prefixes=add_mcp_server_prefixes,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        access_log=False,
        lifespan="on",
        ws="none",
    )


async def _start_temporary_server(
    agent: CodeActAgent,
    mcp_config: Optional[Dict[str, Any]] = None,
    additional_tools: Optional[Dict[str, Callable[..., Any]]] = None,
    add_mcp_server_prefixes: bool = True,
) -> tuple[uvicorn.Server, asyncio.Task[None], str, int, TokenUsageStore]:
    event_bus = AgentEventBus()
    token_usage_store = TokenUsageStore()
    host = settings.DEFAULT_SERVER_HOST
    port = find_free_port()
    assert port is not None, "No free port found for temporary server"

    reset_app_status()

    app = get_main_app(
        agent=agent,
        mcp_config=mcp_config,
        server_host=host,
        server_port=port,
        additional_tools=additional_tools,
        event_bus=event_bus,
        token_usage_store=token_usage_store,
        add_mcp_server_prefixes=add_mcp_server_prefixes,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="error",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    server_task: asyncio.Task[None] = asyncio.create_task(server._serve(sockets=None))

    await asyncio.wait_for(_wait_until_started(server), timeout=30)

    return server, server_task, host, port, token_usage_store


async def run_query(
    query: str,
    agent: CodeActAgent,
    mcp_config: Optional[Dict[str, Any]] = None,
    additional_tools: Optional[Dict[str, Callable[..., Any]]] = None,
    add_mcp_server_prefixes: bool = True,
) -> str:
    server, server_task, host, port, _ = await _start_temporary_server(
        agent,
        mcp_config=mcp_config,
        additional_tools=additional_tools,
        add_mcp_server_prefixes=add_mcp_server_prefixes,
    )

    try:
        result = await agent.ainvoke(
            [ChatMessage(role="user", content=query)],
            session_id=get_unique_id(),
            server_host=host,
            server_port=port,
        )
    finally:
        await _shutdown_server(server, server_task)

    return result


async def run_batch(
    queries: List[str],
    agent: CodeActAgent,
    mcp_config: Optional[Dict[str, Any]] = None,
    max_concurrency: int = 5,
    session_ids: Optional[List[str]] = None,
    task_timeout: Optional[int] = None,
    additional_tools: Optional[Dict[str, Callable[..., Any]]] = None,
    add_mcp_server_prefixes: bool = True,
    output_path: Optional[Path] = None,
) -> List[str]:
    if not queries:
        return []
    if session_ids is not None and len(session_ids) != len(queries):
        raise ValueError("session_ids length must match queries length")

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("")

    server, server_task, host, port, token_usage_store = await _start_temporary_server(
        agent,
        mcp_config=mcp_config,
        additional_tools=additional_tools,
        add_mcp_server_prefixes=add_mcp_server_prefixes,
    )

    semaphore = asyncio.Semaphore(max_concurrency if max_concurrency > 0 else len(queries))

    async def _run_single(q: str, session_id: str) -> str:
        async with semaphore:
            start_time = time.time()
            task = asyncio.create_task(
                agent.ainvoke(
                    [ChatMessage(role="user", content=q)],
                    session_id=session_id,
                    server_host=host,
                    server_port=port,
                    token_usage_store=token_usage_store,
                )
            )
            result: str
            try:
                if task_timeout and task_timeout > 0:
                    result = await asyncio.wait_for(task, timeout=task_timeout)
                else:
                    result = await task
            except asyncio.CancelledError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(task, timeout=2)
                result = f"Timeout after {task_timeout}"
            except Exception as e:
                result = f"Error: {e}"
            finally:
                end_time = time.time()
                duration = int(end_time - start_time)
                if output_path:
                    token_usage = token_usage_store.get(session_id).model_dump()
                    append_jsonl_atomic(
                        output_path,
                        {
                            "query": q,
                            "result": result,
                            "session_id": session_id,
                            "token_usage": token_usage,
                            "duration": duration,
                        },
                    )
                return result

    results: List[str] = []
    try:
        resolved_session_ids = session_ids or [get_unique_id() for _ in queries]
        tasks: List[asyncio.Task[str]] = [
            asyncio.create_task(_run_single(q, session_id))
            for q, session_id in zip(queries, resolved_session_ids)
        ]
        try:
            results = await asyncio.gather(*tasks)
        except (asyncio.CancelledError, KeyboardInterrupt):
            for t in tasks:
                t.cancel()
            with contextlib.suppress(Exception):
                await asyncio.gather(*tasks, return_exceptions=True)
            raise
    finally:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(_shutdown_server(server, server_task))

    return results
