"""병원·가격 DB 모델과 마이그레이션. SQLite 파일로 확인하므로 DB 서버가 필요 없다."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.db.engine import normalize_url
from api.db.models import Area, Base, Menu, Offer, Place

API_ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "snapshot_source",
    "catalog_meta",
    "fx_rate",
    "area",
    "menu",
    "jp_benchmark",
    "concern",
    "concern_menu",
    "place",
    "offer",
    "channel",
    "proof",
}


@pytest.fixture
def engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    # 명령줄과 같은 설정(alembic.ini)으로 올린다.
    config = Config(API_ROOT / "alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield engine
    engine.dispose()


def test_migration_creates_all_tables(engine) -> None:
    assert TABLES <= set(inspect(engine).get_table_names())


def test_migration_matches_models(engine) -> None:
    # 모델을 바꾸고 마이그레이션을 만들지 않았으면 차이가 난다.
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_models_round_trip(engine) -> None:
    with Session(engine) as session:
        session.add(
            Area(
                id="gangnam",
                position=0,
                name_ja="江南",
                name_ko="강남",
                lat=37.5,
                lng=127.0,
            )
        )
        session.add(
            Menu(
                id="shurink",
                position=0,
                category="lifting",
                name_ja="シュリンク",
                name_ko="슈링크",
                short_ja="シュリンク",
                short_ko="슈링크",
                jp_benchmark_jpy=50000,
                vat_applies_in_korea=True,
                lead_time_note_ja="",
                lead_time_note_ko="",
                downtime_max_days=0,
                uses_needle=False,
            )
        )
        session.add(
            Place(
                id="p1",
                position=0,
                category="clinic",
                area_id="gangnam",
                name_ja="A",
                name_ko="A",
                brand_ja="A",
                address_ko="서울",
                station_ja="江南",
                station_ko="강남",
                lat=37.5,
                lng=127.0,
                source_url="https://example.com",
                source_type="official_event",
                vat_excluded=False,
                japanese_support=True,
                caveat_ja="",
                caveat_ko="",
                ai_rating=4.5,
                rating_scope="branch",
                rating_tier="limited",
                central_channel=False,
            )
        )
        session.add(
            Offer(
                place_id="p1",
                menu_id="shurink",
                position=0,
                krw=99000,
                note_ja="",
                note_ko="",
            )
        )
        session.commit()

    with Session(engine) as session:
        offer = session.scalars(select(Offer)).one()
    assert (offer.place_id, offer.menu_id, offer.krw) == ("p1", "shurink", 99000)


def test_offer_requires_known_place_and_menu(engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        with Session(bind=connection) as session:
            session.add(
                Offer(
                    place_id="none",
                    menu_id="none",
                    position=0,
                    krw=1,
                    note_ja="",
                    note_ko="",
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///catalog.db", "sqlite:///catalog.db"),
    ],
)
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected
