"""DB 행과 `Catalog`를 서로 바꾼다(ADR 0001의 교체 지점).

도구와 가격 계산은 지금처럼 `Catalog`만 받는다. DB에서 읽어도 스냅샷을 읽은 것과 같은 `Catalog`가
나와야 하므로, 목록 순서는 `position`으로 지키고 테스트가 스냅샷과 통째로 비교한다.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from api.db import models as m
from api.domain import catalog as c

# 지우는 순서(외래키가 가리키는 쪽을 나중에)
_TABLES = (
    m.Proof,
    m.Channel,
    m.Offer,
    m.ConcernMenu,
    m.JpBenchmark,
    m.Place,
    m.Concern,
    m.Menu,
    m.Area,
    m.FxRate,
    m.CatalogMeta,
    m.SnapshotSource,
)


def save_catalog(session: Session, catalog: c.Catalog) -> None:
    """DB의 병원·가격 데이터를 `catalog`로 통째로 바꾼다. 여러 번 실행해도 결과가 같다."""
    for table in _TABLES:
        session.execute(delete(table))
    session.add_all(
        m.SnapshotSource(path=path, sha256=sha, position=i)
        for i, (path, sha) in enumerate(catalog.sources.items())
    )
    session.add(m.CatalogMeta(id=1, **catalog.meta.model_dump()))
    session.add(m.FxRate(id=1, **catalog.fx.model_dump()))
    session.add_all(
        m.Area(position=i, **area.model_dump()) for i, area in enumerate(catalog.areas)
    )
    for i, menu in enumerate(catalog.menus):
        session.add(m.Menu(position=i, **menu.model_dump(exclude={"jp_benchmarks"})))
        session.add_all(
            m.JpBenchmark(menu_id=menu.id, position=j, **jp.model_dump())
            for j, jp in enumerate(menu.jp_benchmarks)
        )
    for i, concern in enumerate(catalog.concerns):
        session.add(m.Concern(id=concern.id, position=i))
        session.add_all(
            m.ConcernMenu(concern_id=concern.id, position=j, menu_id=menu_id)
            for j, menu_id in enumerate(concern.menu_ids)
        )
    for i, place in enumerate(catalog.places):
        fields = place.model_dump(exclude={"area", "prices", "channels", "proofs"})
        session.add(m.Place(position=i, area_id=place.area, **fields))
        session.add_all(
            m.Offer(
                place_id=place.id, menu_id=menu_id, position=j, **offer.model_dump()
            )
            for j, (menu_id, offer) in enumerate(place.prices.items())
        )
        session.add_all(
            m.Channel(place_id=place.id, position=j, **channel.model_dump())
            for j, channel in enumerate(place.channels)
        )
        session.add_all(
            m.Proof(
                place_id=place.id, menu_id=menu_id, position=j, **proof.model_dump()
            )
            for j, (menu_id, proof) in enumerate(place.proofs.items())
        )
    session.flush()


def _fields(row: m.Base, *exclude: str) -> dict:
    """행의 열 값. position과 다른 표를 가리키는 키는 뺀다."""
    skip = {"position", *exclude}
    return {
        col.key: getattr(row, col.key)
        for col in row.__table__.columns
        if col.key not in skip
    }


def _ordered(session: Session, table: type[m.Base]) -> list:
    return list(session.scalars(select(table).order_by(table.position)))


def _grouped(session: Session, table: type[m.Base], key: str) -> dict[str, list]:
    groups: dict[str, list] = {}
    for row in _ordered(session, table):
        groups.setdefault(getattr(row, key), []).append(row)
    return groups


def load_catalog(session: Session) -> c.Catalog:
    """DB에서 `Catalog`를 만든다. 데이터가 없으면 LookupError."""
    meta = session.get(m.CatalogMeta, 1)
    fx = session.get(m.FxRate, 1)
    if meta is None or fx is None:
        raise LookupError("DB에 병원·가격 데이터가 없다. 시드 명령으로 넣어야 한다")
    jp = _grouped(session, m.JpBenchmark, "menu_id")
    concern_menus = _grouped(session, m.ConcernMenu, "concern_id")
    offers = _grouped(session, m.Offer, "place_id")
    channels = _grouped(session, m.Channel, "place_id")
    proofs = _grouped(session, m.Proof, "place_id")
    return c.Catalog(
        sources={s.path: s.sha256 for s in _ordered(session, m.SnapshotSource)},
        meta=c.Meta(**_fields(meta, "id")),
        fx=c.Fx(**_fields(fx, "id")),
        areas=tuple(c.Area(**_fields(a)) for a in _ordered(session, m.Area)),
        concerns=tuple(
            c.Concern(
                id=concern.id,
                menu_ids=tuple(cm.menu_id for cm in concern_menus.get(concern.id, [])),
            )
            for concern in _ordered(session, m.Concern)
        ),
        menus=tuple(
            c.Menu(
                **_fields(menu),
                jp_benchmarks=tuple(
                    c.JpBenchmark(**_fields(b, "menu_id")) for b in jp.get(menu.id, [])
                ),
            )
            for menu in _ordered(session, m.Menu)
        ),
        places=tuple(
            c.Place(
                **_fields(place, "area_id"),
                area=place.area_id,
                prices={
                    o.menu_id: c.Offer(**_fields(o, "place_id", "menu_id"))
                    for o in offers.get(place.id, [])
                },
                channels=tuple(
                    c.Channel(**_fields(ch, "place_id"))
                    for ch in channels.get(place.id, [])
                ),
                proofs={
                    p.menu_id: c.Proof(**_fields(p, "place_id", "menu_id"))
                    for p in proofs.get(place.id, [])
                },
            )
            for place in _ordered(session, m.Place)
        ),
    )


def load_catalog_from_db() -> c.Catalog:
    """`DATABASE_URL`의 DB에서 `Catalog`를 읽는다(`get_catalog`의 DB 구현)."""
    from api.db.engine import get_engine

    with Session(get_engine()) as session:
        return load_catalog(session)
