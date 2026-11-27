"""Alembic 실행 환경. DB 주소는 `DATABASE_URL`(없으면 SQLite `apps/api/catalog.db`)이다."""

from logging.config import fileConfig

from alembic import context

from api.db.engine import get_engine
from api.db.models import Base

config = context.config

# 명령줄(alembic upgrade)에서만 로그 설정을 읽는다. 앱·테스트에서 부를 때(connection을 넘길 때)
# 앱의 로거를 끄지 않도록 건너뛴다.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(get_engine().url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 테스트는 config.attributes["connection"]으로 연결을 넘긴다.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
