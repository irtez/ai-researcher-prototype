from codearkt.codeact import CodeActAgent
from codearkt.llm import LLM
from codearkt.run_config import (
    clear_compute_mode,
    clear_model_name,
    clear_owner,
    get_compute_mode,
    get_model_name,
    get_owner,
    normalize_compute_mode,
    set_compute_mode,
    set_model_name,
    set_owner,
)
from codearkt.util import get_unique_id


class TestNormalizeComputeMode:
    def test_lowercase(self) -> None:
        assert normalize_compute_mode("local") == "Local"

    def test_uppercase(self) -> None:
        assert normalize_compute_mode("LOCAL") == "Local"

    def test_gpu(self) -> None:
        assert normalize_compute_mode("gpu") == "GPU"

    def test_none(self) -> None:
        assert normalize_compute_mode(None) == "Auto"

    def test_unknown(self) -> None:
        assert normalize_compute_mode("bogus") == "Auto"


class TestComputeModeRegistry:
    def test_get_default_is_auto(self) -> None:
        session_id = get_unique_id()
        assert get_compute_mode(session_id) == "Auto"

    def test_set_get_round_trip(self) -> None:
        session_id = get_unique_id()
        set_compute_mode(session_id, "gpu")
        assert get_compute_mode(session_id) == "GPU"

    def test_set_normalizes_unknown_to_auto(self) -> None:
        session_id = get_unique_id()
        set_compute_mode(session_id, "bogus")
        assert get_compute_mode(session_id) == "Auto"

    def test_per_session_id_isolation(self) -> None:
        session_a = get_unique_id()
        session_b = get_unique_id()
        set_compute_mode(session_a, "Local")
        set_compute_mode(session_b, "GPU")
        assert get_compute_mode(session_a) == "Local"
        assert get_compute_mode(session_b) == "GPU"

    def test_clear_resets_to_default(self) -> None:
        session_id = get_unique_id()
        set_compute_mode(session_id, "Local")
        clear_compute_mode(session_id)
        assert get_compute_mode(session_id) == "Auto"

    def test_clear_is_idempotent(self) -> None:
        session_id = get_unique_id()
        clear_compute_mode(session_id)
        clear_compute_mode(session_id)
        assert get_compute_mode(session_id) == "Auto"


class TestModelNameRegistry:
    def test_get_default_is_none(self) -> None:
        session_id = get_unique_id()
        assert get_model_name(session_id) is None

    def test_set_get_round_trip(self) -> None:
        session_id = get_unique_id()
        set_model_name(session_id, "deepseek/deepseek-v4-pro")
        assert get_model_name(session_id) == "deepseek/deepseek-v4-pro"

    def test_per_session_id_isolation(self) -> None:
        session_a = get_unique_id()
        session_b = get_unique_id()
        set_model_name(session_a, "deepseek/deepseek-v4-pro")
        set_model_name(session_b, "google/gemini-3.1-pro-preview")
        assert get_model_name(session_a) == "deepseek/deepseek-v4-pro"
        assert get_model_name(session_b) == "google/gemini-3.1-pro-preview"

    def test_clear_resets_to_none(self) -> None:
        session_id = get_unique_id()
        set_model_name(session_id, "deepseek/deepseek-v4-pro")
        clear_model_name(session_id)
        assert get_model_name(session_id) is None

    def test_clear_is_idempotent(self) -> None:
        session_id = get_unique_id()
        clear_model_name(session_id)
        clear_model_name(session_id)
        assert get_model_name(session_id) is None


class TestOwnerRegistry:
    def test_get_default_is_none(self) -> None:
        session_id = get_unique_id()
        assert get_owner(session_id) is None

    def test_set_get_round_trip(self) -> None:
        session_id = get_unique_id()
        set_owner(session_id, "alice")
        assert get_owner(session_id) == "alice"

    def test_blank_owner_is_ignored(self) -> None:
        # Nested delegate calls carry no header; a blank must not register or
        # wipe an existing owner (mirrors set_compute_mode's explicit-only rule).
        session_id = get_unique_id()
        set_owner(session_id, "alice")
        set_owner(session_id, None)
        set_owner(session_id, "")
        assert get_owner(session_id) == "alice"

    def test_per_session_id_isolation(self) -> None:
        session_a = get_unique_id()
        session_b = get_unique_id()
        set_owner(session_a, "alice")
        set_owner(session_b, "bob")
        assert get_owner(session_a) == "alice"
        assert get_owner(session_b) == "bob"

    def test_clear_resets_to_none(self) -> None:
        session_id = get_unique_id()
        set_owner(session_id, "alice")
        clear_owner(session_id)
        assert get_owner(session_id) is None

    def test_clear_is_idempotent(self) -> None:
        session_id = get_unique_id()
        clear_owner(session_id)
        clear_owner(session_id)
        assert get_owner(session_id) is None


class TestEffectiveToolNames:
    def _make_agent(self) -> CodeActAgent:
        return CodeActAgent(
            name="mle_solver",
            description="solver",
            llm=LLM(model_name="gpt-4o"),
            tool_names=["bash", "remote_bash", "agent__x"],
            compute_mode_tools={
                "Local": ["bash"],
                "GPU": ["remote_bash"],
                "Auto": ["bash", "remote_bash"],
            },
        )

    def test_local_mode_gates_to_local_tools_plus_agent_tools(self) -> None:
        agent = self._make_agent()
        session_id = get_unique_id()
        set_compute_mode(session_id, "Local")
        assert agent._effective_tool_names(session_id) == ["bash", "agent__x"]

    def test_gpu_mode_gates_to_remote_tools_plus_agent_tools(self) -> None:
        agent = self._make_agent()
        session_id = get_unique_id()
        set_compute_mode(session_id, "GPU")
        assert agent._effective_tool_names(session_id) == ["remote_bash", "agent__x"]

    def test_auto_mode_returns_all_tools(self) -> None:
        agent = self._make_agent()
        session_id = get_unique_id()
        set_compute_mode(session_id, "Auto")
        assert agent._effective_tool_names(session_id) == [
            "bash",
            "remote_bash",
            "agent__x",
        ]

    def test_default_mode_without_explicit_set_is_auto(self) -> None:
        agent = self._make_agent()
        session_id = get_unique_id()
        assert agent._effective_tool_names(session_id) == [
            "bash",
            "remote_bash",
            "agent__x",
        ]

    def test_no_compute_mode_tools_falls_back_to_full_tool_names(self) -> None:
        agent = CodeActAgent(
            name="plain",
            description="no gating configured",
            llm=LLM(model_name="gpt-4o"),
            tool_names=["bash", "remote_bash"],
        )
        session_id = get_unique_id()
        set_compute_mode(session_id, "Local")
        assert agent._effective_tool_names(session_id) == ["bash", "remote_bash"]

    def test_different_sessions_gate_independently_on_shared_agent_instance(
        self,
    ) -> None:
        agent = self._make_agent()
        session_local = get_unique_id()
        session_gpu = get_unique_id()
        set_compute_mode(session_local, "Local")
        set_compute_mode(session_gpu, "GPU")
        assert agent._effective_tool_names(session_local) == ["bash", "agent__x"]
        assert agent._effective_tool_names(session_gpu) == ["remote_bash", "agent__x"]
