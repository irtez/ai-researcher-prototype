from typing import List
import atexit
import signal
import time
from typing import Optional, Any
from contextlib import suppress
from urllib.parse import urlparse

import fire  # type: ignore
from prompt_toolkit import prompt

from codearkt.event_bus import EventType
from codearkt.llm import ChatMessage
from codearkt.client import query_agent, stop_agent
from codearkt.session_id import resolve_session_id
from codearkt.settings import settings


def main(
    host: str = settings.DEFAULT_SERVER_HOST,
    port: int = settings.DEFAULT_SERVER_PORT,
    root_agent_name: str = "manager",
    url: Optional[str] = None,
    session_id: Optional[str] = None,
    message: Optional[str] = None,
    message_file: Optional[str] = None,
    delay_seconds: float = 0.0,
    session_mode: str = "research",
) -> None:
    if url:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        if parsed.hostname:
            host = parsed.hostname
        if parsed.port:
            port = parsed.port

    real_messages: List[ChatMessage] = []
    agent_names: List[str] = []
    resolved_session_id = session_id

    def cleanup_session(signum: Optional[Any] = None, frame: Optional[Any] = None) -> None:
        if resolved_session_id:
            with suppress(Exception):
                stop_agent(resolved_session_id, host=host, port=port)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt()

    atexit.register(cleanup_session)
    signal.signal(signal.SIGINT, cleanup_session)
    signal.signal(signal.SIGTERM, cleanup_session)

    def process_message(message_text: str) -> None:
        nonlocal resolved_session_id
        if not resolved_session_id:
            resolved_session_id = resolve_session_id(
                resolved_session_id,
                first_user_message=message_text,
                mode=session_mode,
            )
            print(f"\n[session_id] {resolved_session_id}\n")

        real_messages.append(ChatMessage(role="user", content=message_text))
        events = query_agent(
            real_messages,
            session_id=resolved_session_id,
            host=host,
            port=port,
            agent_name=root_agent_name,
        )
        for event in events:
            is_root_agent = (
                len(agent_names) == 1
                and agent_names[0] == root_agent_name
                and agent_names[0] == event.agent_name
            )
            if event.event_type == EventType.TOOL_RESPONSE:
                if is_root_agent:
                    real_messages.append(
                        ChatMessage(role="user", content="Tool response:\n" + str(event.content))
                    )
                print("Tool Response:\n", event.content)
            elif event.event_type == EventType.AGENT_START:
                print(f"\n**Starting {event.agent_name} agent...**\n\n")
                agent_names.append(event.agent_name)
            elif event.event_type == EventType.AGENT_END:
                print(f"\n**Agent {event.agent_name} completed the task!**\n\n")
                agent_names.pop()
            elif event.event_type == EventType.PLANNING_OUTPUT:
                if not event.content:
                    continue
                print(event.content, end="")
            elif event.event_type == EventType.OUTPUT:
                if not event.content:
                    continue
                print(event.content, end="")
                if is_root_agent:
                    if real_messages[-1].role == "assistant":
                        assert isinstance(real_messages[-1].content, str)
                        real_messages[-1].content += event.content
                    else:
                        real_messages.append(ChatMessage(role="assistant", content=event.content))

    one_shot_message = message
    if message_file:
        with open(message_file, encoding="utf-8") as f:
            one_shot_message = f.read().strip()

    if one_shot_message:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        process_message(one_shot_message)
        return

    while True:
        message = prompt(
            "Your message (Esc then Enter to accept):\n",
            multiline=True,
        )
        if message == "exit":
            break

        process_message(message)


if __name__ == "__main__":
    fire.Fire(main)
