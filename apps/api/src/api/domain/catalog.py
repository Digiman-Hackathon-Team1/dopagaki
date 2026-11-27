"""병원·가격 데이터 조회 계층(ADR 0001).

web 데이터를 내보낸 JSON 스냅샷(`data/web-snapshot.json`)이 원본이다. 스냅샷은
`pnpm --filter @repo/api export:web-data`로 다시 만든다. `CATALOG_SOURCE=db`면 스냅샷을 넣어 둔
백엔드 DB(`api.db`, `python -m api.db.seed`)에서 읽는다. 어느 쪽이든 도구와 가격 계산은 `Catalog`를
그대로 쓴다.
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

# 고민 분류 6개(web concierge.ts의 ConciergeConcernId). 설계 문서 8장 "정해진 고민 분류 6개".
ConcernId = Literal["pores", "glow", "lift", "dry", "jaw", "skinbase"]


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


class Concern(SnapshotModel):
    """고민별 시술표 한 줄(web concierge.ts의 CONCERN_MENUS). 시술은 가장 직접적인 순이다."""

    id: ConcernId
    menu_ids: tuple[str, ...]


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
    category: str
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


ChannelKind = Literal["kakao", "line", "instagram", "naver_booking", "direct_reserve"]


class Channel(SnapshotModel):
    """예약 채널 하나(web clinic-channels.ts의 getClinicChannels 결과). 링크는 언어별로 다를 수 있다."""

    kind: ChannelKind
    label_ja: str
    label_ko: str
    url_ja: str
    url_ko: str


class Proof(SnapshotModel):
    """가격표 증빙(web official-proofs.ts의 getOfficialMenuProof 결과)."""

    url_ja: str
    url_ko: str
    domain_ja: str
    domain_ko: str
    captured_date: str
    # 공식 페이지 스크린샷에서 이 가격을 확인했는지. web은 이때만 스크린샷을 보여 준다.
    has_screenshot: bool
    matched_text: str | None


class Place(SnapshotModel):
    id: str
    category: str
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
    # 평점 근거. branch 지점 후기, brand 같은 브랜드 후기 참고.
    # jp_kr_verified 일본·한국 후기 확인, kr_verified 한국 후기 확인, limited 확인된 후기가 적음
    rating_scope: Literal["branch", "brand"]
    rating_tier: Literal["jp_kr_verified", "kr_verified", "limited"]
    channels: tuple[Channel, ...]
    # 브랜드 공통 예약 창구라 지점을 직접 골라 예약해야 하는지
    central_channel: bool
    # 시술 ID → 가격표 증빙. prices에 있는 시술마다 있다.
    proofs: dict[str, Proof]


class Catalog(SnapshotModel):
    # 스냅샷을 만든 web 원본 파일의 SHA-256. 원본이 바뀌었는지 테스트가 비교한다.
    sources: dict[str, str]
    meta: Meta
    fx: Fx
    areas: tuple[Area, ...]
    concerns: tuple[Concern, ...]
    menus: tuple[Menu, ...]
    places: tuple[Place, ...]

    def menu(self, menu_id: str) -> Menu:
        for menu in self.menus:
            if menu.id == menu_id:
                return menu
        raise KeyError(menu_id)

    def place(self, place_id: str) -> Place:
        for place in self.places:
            if place.id == place_id:
                return place
        raise KeyError(place_id)

    def concern(self, concern_id: str) -> Concern:
        for concern in self.concerns:
            if concern.id == concern_id:
                return concern
        raise KeyError(concern_id)


def load_snapshot(path: Path = SNAPSHOT_PATH) -> Catalog:
    return Catalog.model_validate_json(path.read_text(encoding="utf-8"))


@lru_cache
def get_catalog() -> Catalog:
    """병원·가격 데이터. `CATALOG_SOURCE=db`면 DB(`api.db`), 아니면 스냅샷에서 읽는다.

    처음 한 번 읽고 프로세스가 끝날 때까지 같은 값을 쓴다. DB 데이터를 다시 넣었으면 서버를 다시 띄운다.
    """
    # api.db가 이 모듈의 모델을 쓰므로 함수 안에서 가져온다(순환 import 방지).
    from api.db.engine import DbSettings

    if DbSettings().catalog_source == "db":
        from api.db.catalog_store import load_catalog_from_db

        return load_catalog_from_db()
    return load_snapshot()
