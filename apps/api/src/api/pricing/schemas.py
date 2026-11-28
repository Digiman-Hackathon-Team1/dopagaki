"""가격 조회 API(`/api/menus`, `/api/clinics`, `/api/fx`)의 요청·응답 형식.

금액은 모두 서버가 `domain/calc.py`로 계산한 값이다. web 순위표와 같은 식(부가세 포함, 일본 주요
병원 공식가 평균 대비 절약액)이고, 환율은 에이전트와 같은 조회기(ADR 0010)에서 읽는다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from api.domain.calc import SortId
from api.domain.catalog import AreaId, ChannelKind
from api.domain.price_tags import PriceTag


class ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, extra="forbid"
    )


class FxRate(ResponseModel):
    """가격 계산에 쓴 환율. 100엔당 원."""

    krw_per_100_jpy: float
    date_label: str
    bank_ko: str
    bank_ja: str
    source_url: str


class JpBenchmarkPrice(ResponseModel):
    """일본 비교 병원 한 곳의 세금 포함 공식가."""

    name_ja: str
    name_ko: str
    plan_ja: str
    plan_ko: str
    price_jpy: int
    url: str


class MenuSummary(ResponseModel):
    id: str
    category: str
    name_ja: str
    name_ko: str
    short_ja: str
    short_ko: str
    jp_benchmark_jpy: int = Field(description="일본 주요 병원 공식가 평균(절약액 기준)")
    jp_benchmarks: list[JpBenchmarkPrice]
    vat_applies_in_korea: bool
    downtime_max_days: int = Field(description="겉으로 보이는 회복 기간(최대 일수)")
    uses_needle: bool
    clinic_count: int = Field(description="이 시술 가격이 있는 병원 수")


class MenuList(ResponseModel):
    items: list[MenuSummary]


class PriceComparison(ResponseModel):
    """병원 한 곳 × 시술 하나의 가격 비교."""

    menu_id: str
    base_krw: int = Field(description="병원 표시 가격")
    vat_krw: int = Field(description="부가세 별도 병원이면 더한 부가세, 아니면 0")
    total_krw: int = Field(description="부가세 포함 가격")
    total_jpy: int = Field(description="부가세 포함 가격의 엔화 환산")
    jp_benchmark_jpy: int
    savings_jpy: int = Field(
        description="일본 평균 대비 절약액(엔). 음수면 일본이 더 싸다"
    )
    savings_pct: int
    note_ja: str
    note_ko: str
    price_tags: list[PriceTag] = Field(
        description="가격 조건 태그(첫 방문가, 이벤트가 등)"
    )


class ClinicSummary(ResponseModel):
    id: str
    area: AreaId
    name_ja: str
    name_ko: str
    brand_ja: str
    station_ja: str
    station_ko: str
    lat: float
    lng: float
    japanese_support: bool
    ai_rating: float


class ClinicPrice(ResponseModel):
    """시술 하나의 병원 순위표 한 줄."""

    clinic: ClinicSummary
    price: PriceComparison
    distance_meters: int = Field(
        description="기준 위치(없으면 서울 시청)에서의 직선거리"
    )
    walk_minutes: int


class ClinicPriceList(ResponseModel):
    menu_id: str
    sort: SortId
    fx: FxRate
    items: list[ClinicPrice]


class ClinicChannel(ResponseModel):
    kind: ChannelKind
    label_ja: str
    label_ko: str
    url_ja: str
    url_ko: str


class ClinicDetail(ResponseModel):
    clinic: ClinicSummary
    address_ko: str
    source_url: str
    source_type: Literal["official_event", "official_standard"]
    vat_excluded: bool
    caveat_ja: str
    caveat_ko: str
    channels: list[ClinicChannel]
    central_channel: bool
    fx: FxRate
    prices: list[PriceComparison] = Field(
        description="이 병원에 가격이 있는 시술(시술 목록 순서)"
    )
