from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env independent of the launch directory: cwd-relative first (back-compat),
# then walk up from this file to the repo root (Holosophus/.env). Missing paths are
# skipped by pydantic-settings; later entries take priority over earlier ones.
_HERE = Path(__file__).resolve()
_ENV_FILES = (
    str(_HERE.parents[2] / ".env"),  # repo root, e.g. Holosophus/.env
    str(_HERE.parents[1] / ".env"),  # codearkt/.env
    "../.env",
    ".env",
)


class Settings(BaseSettings):
    PLANNING_LAST_N: int = 50
    PLANNING_CONTENT_MAX_LENGTH: int = 4000
    DEFAULT_MAX_ITERATIONS: int = 20
    MAX_LENGTH_TRUNCATE_CONTENT: int = 20000

    AGENT_TOOL_PREFIX: str = "agent__"

    DEFAULT_SERVER_HOST: str = "0.0.0.0"
    DEFAULT_SERVER_PORT: int = 5055

    # Session run-slot dispatcher (multi-user concurrent UI [B], SC4/SC5/SC6):
    # max concurrent ROOT agent runs admitted at once; excess runs queue with a
    # live position instead of piling straight onto the host. Nested delegate
    # calls (agent__* sub-agents reusing the same session_id) are never gated
    # by this -- see server.py's root-vs-nested detection in `agent_tool`.
    MAX_ACTIVE_SESSIONS: int = 6
    CAPACITY_ENABLED: bool = True

    # Team-login per-account cap (app-level auth, see
    # docs/plans/2026-07-05-ui-login-and-per-account-queue.md): max concurrent
    # ROOT runs one logged-in account may hold at once, counted across BOTH
    # runner processes via the shared admission ledger (codearkt.admission).
    # Only enforced when app-level auth is enabled (HOLO_AUTH_USERS set); with
    # auth off the session dispatcher stays a plain in-process semaphore.
    PER_OWNER_MAX_ACTIVE_SESSIONS: int = 3

    # App-level GPU admission gate (multi-user concurrent UI [B]/[C], SC5):
    # how many compute_mode=GPU ROOT runs may hold a GPU slot for their whole
    # run at once. A second concurrent GPU run queues with a visible
    # position (`resource="gpu"` events) instead of piling onto mle_kit's
    # box-level lease invisibly. Pool-ready: bump past 1 once more GPU boxes
    # are available -- see server.py's `gpu_dispatcher`.
    GPU_SLOTS: int = 1

    # Executor settings
    CODEARKT_EXECUTOR_URL: Optional[str] = None
    EXECUTOR_IMAGE: str = (
        "phoenix120/codearkt_http@sha256:e00d11db4bc70918f61ebd53e19b0b2f382af6165346322af401b701118404e1"
    )
    DOCKER_MEM_LIMIT: str = "1g"
    DOCKER_CPU_QUOTA: int = 50000
    DOCKER_CPU_PERIOD: int = 100000
    DOCKER_CLEANUP_TIMEOUT: int = 10
    DOCKER_PIDS_LIMIT: int = 256
    DOCKER_NET_NAME: str = "codearkt_sandbox_net"

    # Timeout settings
    EVENT_BUS_STREAM_TIMEOUT: int = 24 * 60 * 60
    FINISH_WAIT_TIMEOUT: int = 10
    EXEC_TIMEOUT: int = 24 * 60 * 60
    PROXY_SSE_READ_TIMEOUT: int = 12 * 60 * 60

    # Legacy defaults. Credentials are checked when selecting a model connection;
    # importing shared runner modules must not require an unrelated provider key.
    BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_API_KEY: str = ""
    HTTP_REFERRER: str = "https://github.com/IlyaGusev/codearkt/"
    X_TITLE: str = "CodeArkt"

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

settings = Settings()
