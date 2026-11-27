"""web 스냅샷을 DB에 넣는다. 크롤링은 하지 않는다(ADR 0001).

apps/api에서 `uv run python -m api.db.seed`로 실행한다. 마이그레이션을 최신으로 올린 뒤 병원·가격
데이터를 스냅샷으로 통째로 바꾼다. 여러 번 실행해도 결과가 같다. 스냅샷을 다시 내보냈으면
(`pnpm --filter @repo/api export:web-data`) 이 명령도 다시 실행한다.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from api.db.catalog_store import save_catalog
from api.db.engine import get_engine
from api.domain.catalog import SNAPSHOT_PATH, load_snapshot

logger = logging.getLogger(__name__)


MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def migrate(engine: Engine) -> None:
    """마이그레이션을 최신으로 올린다. 배포 이미지에는 src만 들어가므로 alembic.ini 없이 설정한다."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def seed(engine: Engine, snapshot: Path = SNAPSHOT_PATH) -> None:
    migrate(engine)
    catalog = load_snapshot(snapshot)
    with Session(engine) as session, session.begin():
        save_catalog(session, catalog)
    prices = sum(len(p.prices) for p in catalog.places)
    logger.info("병원 %d곳, 가격 %d개를 넣었다", len(catalog.places), prices)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed(get_engine())
