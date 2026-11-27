"""DB 연결(ADR 0001의 Catalog 조회 계층 뒤). 엔진은 처음 쓸 때 만든다.

`DATABASE_URL`이 없으면 `apps/api/catalog.db`(SQLite)를 쓴다. 배포(Railway)는 PostgreSQL 주소를
넣는다. `postgres://`·`postgresql://`로 넣어도 psycopg 드라이버 주소(`postgresql+psycopg://`)로 바꾼다.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

# apps/api (이 파일 기준 상대 경로라 실행 위치와 무관하다)
API_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_URL = f"sqlite:///{API_ROOT / 'catalog.db'}"


class DbSettings(BaseSettings):
    """DB 설정. 환경변수가 `.env`보다 우선한다."""

    model_config = SettingsConfigDict(env_file=API_ROOT / ".env", extra="ignore")

    # 비어 있으면 DEFAULT_URL(SQLite)
    database_url: str = ""


def normalize_url(url: str) -> str:
    """Railway가 주는 `postgres://` 주소를 SQLAlchemy psycopg 드라이버 주소로 바꾼다."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(normalize_url(url or DbSettings().database_url or DEFAULT_URL))


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(engine or get_engine(), expire_on_commit=False)
