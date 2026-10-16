import asyncio
import json
from collections import defaultdict, deque
from enum import StrEnum
from typing import Any, AsyncGenerator, Deque, Dict, List, Optional

from pydantic import BaseModel

from codearkt.settings import settings

# Per-session replay buffer size. Replay exists so a consumer that subscribes
# late -- or RE-subscribes after its connection dropped -- can rebuild the run's
# timeline instead of silently missing everything in between (the UI-freeze
# failure mode: the run continues server-side while the browser shows stale
# state forever). Bounded so a chatty run cannot grow memory without limit; at
# this size a reconnecting UI recovers the practical entirety of a run.
EVENT_HISTORY_LIMIT = 2000


class EventType(StrEnum):
    AGENT_START = "agent_start"
    OUTPUT = "output"
    PLANNING_OUTPUT = "planning_output"
    TOOL_RESPONSE = "observation"
    AGENT_END = "agent_end"
    AGENT_INTERRUPTED = "agent_interrupted"
    # Session run-slot dispatcher (multi-user concurrent UI [B],
    # SC4/SC5/SC6, `docs/plans/2026-06-30-multiuser-concurrent-ui.md`).
    # Payload (resource class, queue position/depth) travels in the existing
    # `AgentEvent.content` field as a JSON string -- see `publish_status`
    # below -- so unrecognized-type consumers keep ignoring it safely.
    SESSION_QUEUED = "session_queued"
    SESSION_POSITION = "session_position"
    SESSION_RUNNING = "session_running"


class AgentEvent(BaseModel):  # type: ignore
    session_id: str
    agent_name: str
    event_type: EventType
    content: Optional[str] = None
    # Per-session monotonic sequence number, assigned at publish time. Lets a
    # subscriber that replays history and then switches to the live feed drop
    # the overlap deterministically (JSON consumers that don't know the field
    # simply ignore it).
    seq: Optional[int] = None


class AgentEventBus:
    """Fan-out event bus with per-session bounded replay.

    Previously one ``asyncio.Queue`` per session meant exactly ONE consumer:
    a second subscriber would steal events from the first, and a consumer that
    died could never recover what it missed. Now every subscriber gets its own
    queue, and each session keeps a bounded ``history`` so a (re)subscriber
    first replays what already happened, then follows the live feed -- which is
    what makes browser re-attach after a dropped connection possible at all.
    """

    def __init__(self) -> None:
        self.subscribers: Dict[str, List[asyncio.Queue[AgentEvent]]] = defaultdict(list)
        self.history: Dict[str, Deque[AgentEvent]] = {}
        self._seq: Dict[str, int] = defaultdict(int)
        self.running_tasks: Dict[str, List[asyncio.Task[Any]]] = defaultdict(list)
        self.root_agent_name: Dict[str, str] = {}

    def register_task(
        self, session_id: str, agent_name: str, task: asyncio.Task[Any] | None
    ) -> None:
        if session_id not in self.running_tasks:
            self.root_agent_name[session_id] = agent_name
        if task is not None:
            self.running_tasks[session_id].append(task)

    def is_known(self, session_id: str) -> bool:
        """Whether this session ever registered a run (streamable or replayable)."""
        return session_id in self.root_agent_name

    def finish_session(self, session_id: str) -> None:
        tasks = self.running_tasks.pop(session_id, [])
        while tasks:
            task = tasks.pop()
            task.cancel()
        root = self.root_agent_name.pop(session_id, None)
        # Wake every live subscriber with a terminal event instead of leaving
        # them blocked on queues nothing will ever feed again.
        interrupt = AgentEvent(
            session_id=session_id,
            agent_name=root or "",
            event_type=EventType.AGENT_INTERRUPTED,
            seq=self._next_seq(session_id),
        )
        for queue in self.subscribers.pop(session_id, []):
            queue.put_nowait(interrupt)
        self.history.pop(session_id, None)
        self._seq.pop(session_id, None)

    def _next_seq(self, session_id: str) -> int:
        self._seq[session_id] += 1
        return self._seq[session_id]

    async def publish_event(
        self,
        session_id: str,
        agent_name: str,
        event_type: EventType = EventType.OUTPUT,
        content: Optional[str] = None,
    ) -> None:
        event = AgentEvent(
            session_id=session_id,
            agent_name=agent_name,
            event_type=event_type,
            content=content,
            seq=self._next_seq(session_id),
        )
        if session_id not in self.history:
            self.history[session_id] = deque(maxlen=EVENT_HISTORY_LIMIT)
        self.history[session_id].append(event)
        # put_nowait on unbounded queues: the publisher (the agent run itself)
        # must never block on a slow or dead consumer.
        for queue in self.subscribers.get(session_id, []):
            queue.put_nowait(event)

    async def publish_status(
        self,
        session_id: str,
        agent_name: str,
        event_type: EventType,
        **payload: Any,
    ) -> None:
        """Publish a dispatcher status event (SESSION_QUEUED/_POSITION/_RUNNING).

        `payload` (resource class, queue position/depth, ...) is JSON-encoded
        into the existing `content` field -- no new `AgentEvent` fields, so
        consumers that don't know these event types keep ignoring them.
        Reuses `publish_event`, the bus's one publish path.
        """
        await self.publish_event(
            session_id=session_id,
            agent_name=agent_name,
            event_type=event_type,
            content=json.dumps(payload),
        )

    async def stream_events(self, session_id: str) -> AsyncGenerator[AgentEvent, None]:
        """Yield this session's events: bounded replay first, then live.

        Every call gets its OWN queue (multi-consumer safe), subscribed BEFORE
        the history snapshot is taken so no event can fall between the two;
        the overlap that ordering creates is dropped by ``seq``. Ends after the
        root agent's AGENT_END / AGENT_INTERRUPTED -- so attaching to an
        already-finished (but uncancelled) session replays its full history and
        terminates immediately, which is exactly what a reconnecting client
        wants. The outer timeout is a last-resort guard against a run that dies
        without ever publishing a terminal event.
        """
        assert session_id in self.root_agent_name, f"Session {session_id} is not tied to an agent"
        root_agent_name = self.root_agent_name[session_id]
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.subscribers[session_id].append(queue)
        last_seq = 0
        try:
            for event in list(self.history.get(session_id, ())):
                yield event
                if event.seq is not None:
                    last_seq = max(last_seq, event.seq)
                if self._is_terminal(event, root_agent_name):
                    return
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=settings.EVENT_BUS_STREAM_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    break
                if event.seq is not None and event.seq <= last_seq:
                    continue  # already delivered via replay
                yield event
                if event.seq is not None:
                    last_seq = max(last_seq, event.seq)
                if self._is_terminal(event, root_agent_name):
                    break
        finally:
            with_queues = self.subscribers.get(session_id)
            if with_queues is not None and queue in with_queues:
                with_queues.remove(queue)

    @staticmethod
    def _is_terminal(event: AgentEvent, root_agent_name: str) -> bool:
        return (
            event.event_type in (EventType.AGENT_END, EventType.AGENT_INTERRUPTED)
            and event.agent_name == root_agent_name
        )
