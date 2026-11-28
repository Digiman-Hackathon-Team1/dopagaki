"""병원·가격 데이터 테이블(ADR 0001).

`domain/catalog.py`의 `Catalog` 구조를 그대로 테이블로 나눴다. 크롤링은 하지 않고, web 데이터 스냅샷
(`domain/data/web-snapshot.json`)을 넣어 쓴다. `Catalog`의 목록은 순서가 의미 있으므로(고민별 시술은
가장 직접적인 순 등) 목록에서 온 행에는 `position`을 둔다.
"""

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SnapshotSource(Base):
    """스냅샷을 만든 web 원본 파일의 SHA-256(`Catalog.sources`)."""

    __tablename__ = "snapshot_source"

    path: Mapped[str] = mapped_column(String, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)


class CatalogMeta(Base):
    """`Catalog.meta`. 행은 하나(id=1)다."""

    __tablename__ = "catalog_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_date: Mapped[str] = mapped_column(String)
    vat_rate: Mapped[float] = mapped_column(Float)


class FxRate(Base):
    """`Catalog.fx`. web 순위표와 같은 환율 스냅샷(100엔당 원). 행은 하나(id=1)다."""

    __tablename__ = "fx_rate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    krw_per_100_jpy: Mapped[float] = mapped_column(Float)
    date_label: Mapped[str] = mapped_column(String)
    bank_ko: Mapped[str] = mapped_column(String)
    bank_ja: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(Text)


class Area(Base):
    __tablename__ = "area"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    name_ja: Mapped[str] = mapped_column(String)
    name_ko: Mapped[str] = mapped_column(String)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)


class Menu(Base):
    """시술 한 가지. 일본 비교가는 `jp_benchmark`에 있다."""

    __tablename__ = "menu"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String)
    name_ja: Mapped[str] = mapped_column(String)
    name_ko: Mapped[str] = mapped_column(String)
    short_ja: Mapped[str] = mapped_column(String)
    short_ko: Mapped[str] = mapped_column(String)
    jp_benchmark_jpy: Mapped[int] = mapped_column(Integer)
    vat_applies_in_korea: Mapped[bool] = mapped_column(Boolean)
    lead_time_note_ja: Mapped[str] = mapped_column(Text)
    lead_time_note_ko: Mapped[str] = mapped_column(Text)
    downtime_max_days: Mapped[int] = mapped_column(Integer)
    uses_needle: Mapped[bool] = mapped_column(Boolean)


class JpBenchmark(Base):
    """일본 비교 병원 한 곳의 세금 포함 공식가(`Menu.jp_benchmarks`)."""

    __tablename__ = "jp_benchmark"

    menu_id: Mapped[str] = mapped_column(ForeignKey("menu.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_ja: Mapped[str] = mapped_column(String)
    name_ko: Mapped[str] = mapped_column(String)
    plan_ja: Mapped[str] = mapped_column(Text)
    plan_ko: Mapped[str] = mapped_column(Text)
    price_jpy: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)


class Concern(Base):
    """고민 분류(`Catalog.concerns`). 시술 목록은 `concern_menu`에 있다."""

    __tablename__ = "concern"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)


class ConcernMenu(Base):
    """고민별 시술. position은 가장 직접적인 시술부터의 순서다."""

    __tablename__ = "concern_menu"

    concern_id: Mapped[str] = mapped_column(ForeignKey("concern.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    menu_id: Mapped[str] = mapped_column(ForeignKey("menu.id"))


class Place(Base):
    """서울 병원 한 곳. 가격·예약 채널·가격표 증빙은 따로 있다."""

    __tablename__ = "place"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String)
    area_id: Mapped[str] = mapped_column(ForeignKey("area.id"))
    name_ja: Mapped[str] = mapped_column(String)
    name_ko: Mapped[str] = mapped_column(String)
    brand_ja: Mapped[str] = mapped_column(String)
    address_ko: Mapped[str] = mapped_column(Text)
    station_ja: Mapped[str] = mapped_column(String)
    station_ko: Mapped[str] = mapped_column(String)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String)
    vat_excluded: Mapped[bool] = mapped_column(Boolean)
    japanese_support: Mapped[bool] = mapped_column(Boolean)
    caveat_ja: Mapped[str] = mapped_column(Text)
    caveat_ko: Mapped[str] = mapped_column(Text)
    ai_rating: Mapped[float] = mapped_column(Float)
    rating_scope: Mapped[str] = mapped_column(String)
    rating_tier: Mapped[str] = mapped_column(String)
    opened_date: Mapped[str | None] = mapped_column(String, nullable=True)
    central_channel: Mapped[bool] = mapped_column(Boolean)


class Offer(Base):
    """병원 한 곳의 시술 가격(`Place.prices`). 원화, web 데이터 그대로(부가세 포함 여부는 Place에 있다)."""

    __tablename__ = "offer"

    place_id: Mapped[str] = mapped_column(ForeignKey("place.id"), primary_key=True)
    menu_id: Mapped[str] = mapped_column(ForeignKey("menu.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    krw: Mapped[int] = mapped_column(Integer)
    note_ja: Mapped[str] = mapped_column(Text)
    note_ko: Mapped[str] = mapped_column(Text)


class Channel(Base):
    """예약 채널 하나(`Place.channels`)."""

    __tablename__ = "channel"

    place_id: Mapped[str] = mapped_column(ForeignKey("place.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    label_ja: Mapped[str] = mapped_column(String)
    label_ko: Mapped[str] = mapped_column(String)
    url_ja: Mapped[str] = mapped_column(Text)
    url_ko: Mapped[str] = mapped_column(Text)


class Proof(Base):
    """가격표 증빙(`Place.proofs`). 가격이 있는 시술마다 있다."""

    __tablename__ = "proof"

    place_id: Mapped[str] = mapped_column(ForeignKey("place.id"), primary_key=True)
    menu_id: Mapped[str] = mapped_column(ForeignKey("menu.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    url_ja: Mapped[str] = mapped_column(Text)
    url_ko: Mapped[str] = mapped_column(Text)
    domain_ja: Mapped[str] = mapped_column(String)
    domain_ko: Mapped[str] = mapped_column(String)
    captured_date: Mapped[str] = mapped_column(String)
    has_screenshot: Mapped[bool] = mapped_column(Boolean)
    matched_text: Mapped[str | None] = mapped_column(Text)
