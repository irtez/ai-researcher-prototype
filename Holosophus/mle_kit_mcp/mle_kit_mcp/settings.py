from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    WORKSPACE_DIR: Optional[str] = None
    HOST_WORKSPACE_DIR: Optional[str] = None
    PORT: int = 5057

    REMOTE_PROVIDER: Literal["vast", "cloudru", "selectel"] = "vast"

    # Hand the local `bash` sandbox one or more host GPUs. Empty (the default)
    # keeps the sandbox CPU-only, which is the only safe default: most hosts
    # have no GPU, and asking for one that isn't there fails container start.
    # "all" for every card, or a comma-separated index list such as "0,1".
    LOCAL_GPU_DEVICES: str = ""

    # Release a session's remote card after this long without a remote call.
    # Nothing signals end-of-run to this process, so without expiry a lease is
    # held until restart. Generous by default: a research run can sit in local
    # work for a long stretch between remote steps. 0 disables expiry.
    GPU_LEASE_IDLE_SECONDS: int = 3600

    GPU_TYPE: str = "RTX_3090"
    DISK_SPACE: int = 300
    EXISTING_INSTANCE_ID: Optional[int] = None
    EXISTING_SSH_KEY: Optional[str] = None
    VAST_AI_KEY: Optional[str] = None

    CLOUDRU_SSH_HOST: Optional[str] = None
    CLOUDRU_SSH_PORT: int = 2222
    CLOUDRU_SSH_USER: Optional[str] = None
    CLOUDRU_SSH_KEY: Optional[str] = None
    CLOUDRU_REMOTE_HOME: str = "/home/jovyan"

    # Selectel: a static, already-provisioned multi-GPU SSH host (same
    # integration shape as cloud.ru -- log into a running box, no cloud API, no
    # lifecycle provisioning) but with several cards. SSH is on the default
    # port 22 (cloud.ru uses 2222). SELECTEL_GPU_INDICES optionally restricts
    # Holosophus to a reserved subset of card indices on the shared host.
    SELECTEL_SSH_HOST: Optional[str] = None
    SELECTEL_SSH_PORT: int = 22
    SELECTEL_SSH_USER: Optional[str] = None
    SELECTEL_SSH_KEY: Optional[str] = None
    SELECTEL_REMOTE_HOME: str = "/home/m.mozikov"
    SELECTEL_GPU_INDICES: Optional[str] = None

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    GPU_SLOTS: int = 1
    LOCAL_EXEC_SLOTS: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
