"""스냅샷을 DB에 넣고 다시 읽으면 같은 Catalog인지, get_catalog가 설정대로 읽는지."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from api.db import engine as db_engine
from api.db.catalog_store import load_catalog
from api.db.models import Offer, Place
from api.db.seed import seed
from api.domain import catalog as catalog_module
from api.domain.catalog import load_snapshot


@pytest.fixture
def engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    seed(engine)
    yield engine
    engine.dispose()


def test_db_catalog_equals_snapshot(engine) -> None:
    with Session(engine) as session:
        from_db = load_catalog(session)

    snapshot = load_snapshot()
    assert from_db == snapshot
    # 순서가 의미 있는 목록(고민별 시술, 가격 dict 순서)도 같다.
    assert [c.menu_ids for c in from_db.concerns] == [
        c.menu_ids for c in snapshot.concerns
    ]
    assert [list(p.prices) for p in from_db.places] == [
        list(p.prices) for p in snapshot.places
    ]


def test_seed_puts_all_clinics_and_prices(engine) -> None:
    with Session(engine) as session:
        places = session.scalar(select(func.count()).select_from(Place))
        offers = session.scalar(select(func.count()).select_from(Offer))

    snapshot = load_snapshot()
    assert places == len(snapshot.places) == 137
    assert offers == sum(len(p.prices) for p in snapshot.places) == 735


def test_seed_twice_gives_same_catalog(engine) -> None:
    seed(engine)

    with Session(engine) as session:
        assert load_catalog(session) == load_snapshot()


def test_empty_db_raises(tmp_path: Path) -> None:
    from api.db.seed import migrate

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    migrate(engine)

    with Session(engine) as session, pytest.raises(LookupError):
        load_catalog(session)


def test_get_catalog_reads_db_when_configured(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CATALOG_SOURCE", "db")
    monkeypatch.setattr(db_engine, "get_engine", lambda url=None: engine)
    catalog_module.get_catalog.cache_clear()
    try:
        assert catalog_module.get_catalog() == load_snapshot()
    finally:
        catalog_module.get_catalog.cache_clear()


def test_get_catalog_defaults_to_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CATALOG_SOURCE", raising=False)
    monkeypatch.setattr(
        db_engine, "get_engine", lambda url=None: pytest.fail("DB를 읽으면 안 된다")
    )
    catalog_module.get_catalog.cache_clear()
    try:
        assert catalog_module.get_catalog() == load_snapshot()
    finally:
        catalog_module.get_catalog.cache_clear()
