"""병원·가격 데이터 조회 계층(ADR 0001).

지금은 web 데이터를 내보낸 JSON 스냅샷(`data/web-snapshot.json`)을 읽는다. 스냅샷은
`pnpm --filter @repo/api export:web-data`로 다시 만든다. 백엔드 DB가 준비되면 `get_catalog`의
구현만 바꾸고, 도구와 가격 계산은 `Catalog`를 그대로 쓴다.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "web-snapshot.json"

AreaId = Literal[
    "sinchon",
    "hongdae",
    "myeongdong",
    "yongsan",
    "gangnam",
    "jamsil",
    "seongsu",
    "yeouido",
    "magok",
    "nowon",
    "cheonho",
    "gwanak",
]


class SnapshotModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, frozen=True
    )


class GeoPoint(SnapshotModel):
    lat: float
    lng: float


class Fx(SnapshotModel):
    """web 순위표와 같은 환율 스냅샷. 100엔당 원."""

    krw_per_100_jpy: float
    date_label: str
    bank_ko: str
    bank_ja: str
    source_url: str


class Meta(SnapshotModel):
    checked_date: str
    vat_rate: float


class Area(SnapshotModel):
    id: AreaId | Literal["all"]
    name_ja: str
    name_ko: str
    lat: float
    lng: float


class JpBenchmark(SnapshotModel):
    """일본 비교 병원 한 곳의 세금 포함 공식가."""

    name_ja: str
    name_ko: str
    plan_ja: str
    plan_ko: str
    price_jpy: int
    url: str


class Menu(SnapshotModel):
    id: str
    name_ja: str
    name_ko: str
    short_ja: str
    short_ko: str
    # 일본 주요 병원 공식가 평균(web 순위표의 비교 기준)
    jp_benchmark_jpy: int
    jp_benchmarks: tuple[JpBenchmark, ...]
    vat_applies_in_korea: bool
    lead_time_note_ja: str
    lead_time_note_ko: str
    # web concierge.ts의 값: 겉으로 보이는 회복 기간(최대 일수), 바늘 사용 여부
    downtime_max_days: int
    uses_needle: bool


class Offer(SnapshotModel):
    krw: int
    note_ja: str
    note_ko: str


class Place(SnapshotModel):
    id: str
    area: AreaId
    name_ja: str
    name_ko: str
    brand_ja: str
    address_ko: str
    station_ja: str
    station_ko: str
    lat: float
    lng: float
    source_url: str
    source_type: Literal["official_event", "official_standard"]
    vat_excluded: bool
    japanese_support: bool
    caveat_ja: str
    caveat_ko: str
    prices: dict[str, Offer]
    # web ai-rating.ts가 후기 데이터로 계산한 5점 평점(후기 순 정렬에 씀)
    ai_rating: float


class Catalog(SnapshotModel):
    meta: Meta
    fx: Fx
    areas: tuple[Area, ...]
    menus: tuple[Menu, ...]
    places: tuple[Place, ...]

    def menu(self, menu_id: str) -> Menu:
        for menu in self.menus:
            if menu.id == menu_id:
                return menu
        raise KeyError(menu_id)


def load_snapshot(path: Path = SNAPSHOT_PATH) -> Catalog:
    return Catalog.model_validate_json(path.read_text(encoding="utf-8"))


@lru_cache
def get_catalog() -> Catalog:
    return load_snapshot()
