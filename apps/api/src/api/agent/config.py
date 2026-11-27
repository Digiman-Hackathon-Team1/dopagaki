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
    # 배포된 web의 환율 파일(예: https://<web 주소>/naver-fx.json). 없으면 스냅샷 환율을 쓴다(ADR 0010).
    web_fx_url: str | None = None
    # 실행 한도(설계 문서 2장, 6장, 12장). 넘으면 오류 대신 시세표 안내를 돌려준다(FR-08).
    # 한 턴의 LLM 호출 수. 도구를 부를 때마다 한 번씩 늘어난다(병렬 호출은 한 번). 실제 Sonnet이
    # 돌아가다 끝낸 턴이 7번까지 있었고 8번에서 잘린 턴이 있어 10으로 둔다. 끝없는 반복을 막는 값이다.
    agent_max_steps: int = 10
    # LLM 호출 하나의 시간 제한(초)과 SDK 재시도 횟수(429·529·연결 오류).
    agent_timeout_seconds: float = 60
    agent_max_retries: int = 2
    # 하루(UTC) 대화 요청 수. 전체 500은 기존 Worker 상한과 같다. 사용자별 50은 한 대화 최대 턴 수와 같다.
    agent_daily_limit: int = 500
    agent_user_daily_limit: int = 50


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()
