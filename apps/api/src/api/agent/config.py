from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/.env (이 파일 기준 상대 경로라 실행 위치와 무관하다)
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentSettings(BaseSettings):
    """에이전트 설정. 환경변수가 `.env`보다 우선한다."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    anthropic_api_key: SecretStr | None = None
    agent_model: str = "claude-sonnet-5-5"
    agent_effort: Effort = "medium"
    agent_max_tokens: int = 16000


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()
