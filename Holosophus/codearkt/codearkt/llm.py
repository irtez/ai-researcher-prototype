import asyncio
import copy
from contextlib import suppress
from typing import Any, AsyncGenerator, Dict, List, Optional, cast

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam
from pydantic import BaseModel
from tiktoken import encoding_for_model

from codearkt.settings import settings

HEADERS = {
    "HTTP-Referer": settings.HTTP_REFERRER,
    "X-Title": settings.X_TITLE,
}

STREAM_CHUNK_TIMEOUT_SECONDS = 180

class FunctionCall(BaseModel):  # type: ignore
    name: str
    arguments: str


class ToolCall(BaseModel):  # type: ignore
    id: str
    type: str = "function"
    function: FunctionCall


class ChatMessage(BaseModel):  # type: ignore
    role: str
    content: str | List[Dict[str, Any]]
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def __str__(self) -> str:
        dump: str = self.model_dump_json()
        return dump


ChatMessages = List[ChatMessage]
ChatStreamGenerator = AsyncGenerator[ChatCompletionChunk, None]


def count_openai_tokens(messages: ChatMessages) -> int:
    encoding = encoding_for_model("gpt-4o")
    tokens = [encoding.encode(str(message.content)) for message in messages]
    return sum(len(token) for token in tokens) + 3 * len(messages)

async def _next_stream_event(
    stream: AsyncStream[ChatCompletionChunk],
    timeout_seconds: float,
) -> ChatStreamGenerator:
    iterator = stream.__aiter__()
    while True:
        try:
            event = await asyncio.wait_for(
                iterator.__anext__(),
                timeout=timeout_seconds,
            )
        except StopAsyncIteration:
            return
        yield event

class LLM:
    def __init__(
        self,
        model_name: str,
        base_url: str = settings.BASE_URL,
        api_key: str = settings.OPENROUTER_API_KEY,
        max_history_tokens: int = 200000,
        num_retries: int = 3,
        config_resolver=None,
        **kwargs: Any,
    ) -> None:
        assert api_key or config_resolver, "LLM API key is required. Please set OPENROUTER_API_KEY environment variable."
        self.config_resolver = config_resolver
        self._model_name = model_name
        self._base_url = base_url
        self._api_key = api_key
        self._max_history_tokens = max_history_tokens
        self._params: Dict[str, Any] = {}
        self._num_retries = num_retries
        for k, v in kwargs.items():
            self._params[k] = v

    def _trim_messages(self, messages: ChatMessages, context_window: Optional[int] = None) -> ChatMessages:
        if len(messages) <= 2:
            return messages
        tokens_count = count_openai_tokens(messages)
        first_messages = []
        while messages and messages[0].role in ("system", "developer"):
            first_messages.extend(messages[:1])
            messages = messages[1:]
        if messages[0].role == "user":
            first_messages.extend(messages[:1])
            messages = messages[1:]
        while tokens_count > (context_window or self._max_history_tokens) and len(messages) >= 2:
            tokens_count -= count_openai_tokens(messages[:2])
            messages = messages[2:]
        if first_messages:
            messages = first_messages + messages
        return messages

    async def astream(
        self, messages: ChatMessages, model_name: Optional[str] = None, **kwargs: Any
    ) -> ChatStreamGenerator:
        messages = copy.deepcopy(messages)
        effective_model_name = model_name or self._model_name
        stream_chunk_timeout_seconds = float(
            kwargs.pop("stream_chunk_timeout_seconds", STREAM_CHUNK_TIMEOUT_SECONDS)
        )
        api_params = {**self._params, **kwargs}
        effort = api_params.pop("model_effort", None)
        routing = api_params.pop("model_configuration", None)
        if routing is None and self.config_resolver:
            routing = self.config_resolver(effective_model_name, effort)
        base_url, api_key = self._base_url, self._api_key
        if routing:
            effective_model_name = routing["model_name"]
            base_url, api_key = routing["base_url"], routing["api_key"]
            if "max_completion_tokens" in routing["params"]:
                api_params.pop("max_tokens", None)
            api_params.update(routing["params"])

        if "gpt-5" in effective_model_name:
            if messages[0].role == "system":
                messages[0].role = "developer"
            api_params.pop("stop", None)
            if "max_tokens" in api_params:
                api_params["max_completion_tokens"] = api_params.pop("max_tokens")

        if "grok-code" in effective_model_name:
            api_params.pop("stop", None)

        context = routing.get("context_window") if routing else None
        input_budget = None
        if context:
            output_budget = api_params.get("max_completion_tokens", api_params.get("max_tokens", 0)) or 0
            input_budget = context - output_budget
            if input_budget <= 0:
                raise ValueError("Output token limit leaves no room for model input")
        messages = self._trim_messages(messages, input_budget)
        casted_messages = [
            cast(ChatCompletionMessageParam, message.model_dump(exclude_none=True))
            for message in messages
        ]

        async with AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=self._num_retries,
        ) as api:
            stream: AsyncStream[ChatCompletionChunk] = await api.chat.completions.create(
                model=effective_model_name,
                messages=casted_messages,
                stream=True,
                extra_headers=HEADERS,
                **api_params,
            )
            try:
                async for event in _next_stream_event(stream, stream_chunk_timeout_seconds):
                    yield event
            finally:
                with suppress(Exception):
                    await stream.close()
