from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/.env (이 파일 기준 상대 경로라 실행 위치와 무관하다)
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentSettings(BaseSettings):
    """에이전트 설정. 환경변수가 `.env`보다 우선한다."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    anthropic_api_key: SecretStr | None = None
    agent_model: str = "claude-haiku-5-5"
    agent_effort: Effort = "medium"
    agent_max_tokens: int = 16000
    # 프롬프트 캐싱(설계 문서 8장 "비용", 이슈 #59). 도구 정의·시스템 프롬프트와 대화 기록 앞부분을 캐시에서 읽어
    # 입력 비용과 첫 글자 대기 시간을 줄인다. 문제가 생기면 0으로 끈다.
    agent_prompt_cache: bool = True
    # 배포된 web의 환율 파일(예: https://<web 주소>/naver-fx.json). 없으면 스냅샷 환율을 쓴다(ADR 0010).
    web_fx_url: str | None = None
    # 실행 한도(설계 문서 2장, 6장, 12장). 넘으면 오류 대신 시세표 안내를 돌려준다(FR-08).
    # 한 턴의 LLM 호출 단계 수. 도구를 부를 때마다 한 번씩 늘어난다(병렬 호출은 한 번). 빈 응답 재요청
    # 한 번도 여기에 들어간다. SDK 재시도는 따로다. 실제 Sonnet이
    # 돌아가다 끝낸 턴이 7번까지 있었고 8번에서 잘린 턴이 있어 10으로 둔다. 끝없는 반복을 막는 값이다.
    agent_max_steps: int = 10
    # 한 턴 전체 시간 상한(초). 넘으면 다음 LLM 호출을 하지 않고 시세표 안내로 끝낸다. 이미 시작한 호출은
    # 끝까지 기다리므로 최악은 이 값 + 호출 하나(시간 제한 × 시도 횟수)다. 180초는 사용자가 정한 출발점이다.
    agent_turn_timeout_seconds: float = 180
    # LLM 호출 하나의 시간 제한(초)과 SDK 재시도 횟수(429·529·연결 오류).
    agent_timeout_seconds: float = 60
    agent_max_retries: int = 2
    # 하루(UTC) 대화 요청 수. 요청 하나가 LLM을 여러 번 부르므로, 하루 모델 호출 500회였던 버튼식 상담용 Worker보다
    # LLM 호출은 많다. 사용자별 50은 설계 문서 8장의 한 대화 최대 턴 수와 같다(대화 턴 상한은 T9).
    # 데모 응답 캐싱(설계 문서 13장). 켜면 녹화한 대본과 같은 메시지에 Claude 대신 녹화한 응답을 돌려준다.
    agent_demo_cache: bool = False
    agent_daily_limit: int = 500
    agent_user_daily_limit: int = 50
    # 대화 기억 관리(설계 문서 8장). 같은 대화의 요청은 앞 요청이 끝날 때까지 기다렸다가 처리하고, 이 시간(초)을
    # 넘기면 busy 안내로 답한다. 한 대화의 최대 턴 수를 넘으면 LLM을 부르지 않고 새 대화를 열라고 안내한다.
    # 마지막 요청 뒤 보관 시간(초)이 지난 대화와, 대화 수 상한을 넘은 오래된 대화는 서버 메모리에서 지운다.
    agent_thread_wait_seconds: float = Field(default=60, ge=0)
    agent_max_turns: int = Field(default=50, ge=1)
    agent_thread_ttl_seconds: float = Field(default=24 * 60 * 60, ge=0)
    agent_max_threads: int = Field(default=1000, ge=1)
    # 사용자 기억(설계 문서 8장 사용자 기억 관리, ADR 0019). 마지막 저장 뒤 보관 시간(초)이 지난 프로필과, 사용자 수
    # 상한을 넘은 오래된 프로필은 서버 메모리에서 지운다.
    agent_profile_ttl_seconds: float = Field(default=30 * 24 * 60 * 60, ge=0)
    agent_max_profiles: int = Field(default=10_000, ge=1)


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()
