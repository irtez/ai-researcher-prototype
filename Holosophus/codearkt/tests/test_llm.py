from codearkt.llm import LLM, ChatMessage


class TestLLM:
    def test_llm_params_override(self) -> None:
        llm = LLM(
            model_name="test-model", temperature=0.25, top_p=0.3, max_tokens=1024, stop=["STOP"]
        )

        assert llm._model_name == "test-model"
        assert llm._params["temperature"] == 0.25
        assert llm._params["top_p"] == 0.3
        assert llm._params["max_tokens"] == 1024
        assert llm._params["stop"] == ["STOP"]

    async def test_llm_astream(self, deepseek: LLM) -> None:
        output = ""
        async for event in deepseek.astream(
            [ChatMessage(role="user", content="Output only 'Hello, world!'")],
        ):
            delta = event.choices[0].delta
            if delta.content:
                output += delta.content
        assert "Hello, world!" in output

    async def test_llm_astream_model_name_override(self) -> None:
        # Construct with a model that would fail if actually used -- proves
        # the explicit `model_name` kwarg, not `self._model_name`, reaches
        # the real API call.
        llm = LLM(model_name="this-model-does-not-exist-xyz")
        output = ""
        async for event in llm.astream(
            [ChatMessage(role="user", content="Output only 'Hello, world!'")],
            model_name="deepseek/deepseek-chat-v3-0324",
        ):
            delta = event.choices[0].delta
            if delta.content:
                output += delta.content
        assert "Hello, world!" in output

    async def test_llm_max_history_tokens_base(self, deepseek_small_context: LLM) -> None:
        messages = [
            ChatMessage(role="user", content="Hello, world!"),
            ChatMessage(role="assistant", content="Hello!"),
        ] * 10000
        output = ""
        usage = None
        async for event in deepseek_small_context.astream(messages):
            delta = event.choices[0].delta
            if delta.content:
                output += delta.content
            if event.usage:
                usage = event.usage
        assert usage
        assert usage.prompt_tokens < deepseek_small_context._max_history_tokens
        assert output

    async def test_llm_max_history_tokens_first_messages(self, deepseek_small_context: LLM) -> None:
        first_messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="First user message"),
        ]
        messages = [
            ChatMessage(role="user", content="Hello, world!"),
            ChatMessage(role="assistant", content="Hello!"),
        ] * 10000
        trimmed_messages = deepseek_small_context._trim_messages(first_messages + messages)
        assert trimmed_messages[0] == first_messages[0]
        assert trimmed_messages[1] == first_messages[1]
        assert len(trimmed_messages) < 100
