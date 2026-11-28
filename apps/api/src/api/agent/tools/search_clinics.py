"""search_clinics: 시술 하나로 병원을 거르고 정렬해 상위 N곳을 돌려준다(설계 문서 4.7, 9장).

거르기(회복 기간, 바늘, 지역, 일본어, 예산) → 정렬(기본 일본 대비 절약액) → 상위 N곳.
가중치 점수는 쓰지 않는다(ADR 0004). LLM에는 요약 문장을, 화면에는 카드 데이터를 따로 넘긴다.
"""

from collections import Counter
from functools import lru_cache
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from api.agent.config import get_settings
from api.agent.schemas import (
    BookingChannel,
    ExcludeReason,
    ExclusionData,
    JpClinicSavings,
    LocationCardData,
    PriceProof,
    RecommendationCardData,
)
from api.agent.tools.match_jp_treatment import (
    jp_clinic_not_found,
    jp_clinic_price,
    product_note_line,
)
from api.agent.tools.resolve_location import location_card, location_label
from api.domain.calc import (
    PriceRow,
    SortId,
    matches_place,
    rank_rows,
    savings_against,
)
from api.domain.catalog import AreaId, Fx, JpBenchmark, Menu, Place, get_catalog
from api.domain.fx import FxProvider
from api.domain.jp_names import find_jp_clinic
from api.domain.locations import Location, get_locations
from api.domain.price_tags import price_tags

MenuId = Literal[
    "rejuran-2cc",
    "shrink-300",
    "potenza",
    "jaw-botox",
    "juvelook-2cc",
    "re2o-5cc",
    "inmode-fx",
    "lalapeel",
    "aquapeel",
]

TOOL_NAME = "search_clinics"

EXCLUDE_LABEL: dict[ExcludeReason, str] = {
    "needle": "the treatment uses a needle",
    "downtime": "the treatment's downtime is too long",
    "area": "outside the requested area",
    "japanese": "no Japanese support",
    "budget": "over budget",
}


class Budget(BaseModel):
    amount: int = Field(gt=0, description="예산 금액")
    currency: Literal["JPY", "KRW"] = Field(
        description="사용자가 말한 통화. 엔이면 JPY, 원이면 KRW"
    )


class SearchClinicsInput(BaseModel):
    menu_id: MenuId = Field(description="시술 ID. 도구 설명의 시술 목록에서 고른다")
    sort: SortId = Field(
        default="savings",
        description=(
            "정렬 기준. 기본 savings(일본 대비 절약액 큰 순). "
            "사용자가 원할 때만 distance(가까운 순, origin_id 필요) 또는 rating(후기 평점 순)"
        ),
    )
    budget: Budget | None = Field(
        default=None, description="1회 시술 예산. 말하지 않았으면 null(제한 없음)"
    )
    max_downtime_days: int | None = Field(
        default=None,
        ge=0,
        description="허용하는 회복 기간(일). 말하지 않았으면 null. 예: 내일 귀국이면 0",
    )
    avoid_needle: bool = Field(
        default=False, description="사용자가 바늘 시술을 피하고 싶다고 했을 때만 true"
    )
    japanese_only: bool = Field(
        default=False,
        description=(
            "일본어 상담 가능 병원만 찾는다. 사용자가 일본어 상담이 되는 곳만 원한다고 말했을 때만 true. "
            "기본 false: 일본어 상담 여부와 상관없이 절약액 순으로 찾고, 카드와 결과 문장에 일본어 상담 "
            "가능 여부가 표시된다"
        ),
    )
    area: AreaId | None = Field(
        default=None, description="이 지역 병원만. 사용자가 지역을 정했을 때만"
    )
    location: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description=(
            "거리 계산 기준 위치(숙소·사는 곳·역)로 사용자가 말한 장소 표현 그대로 또는 그 안의 장소 이름. "
            "예: '홍대 숙소', '江南駅'. 도구가 위치를 찾아 거리를 계산하므로 resolve_location을 따로 부르지 "
            "않는다. 가타카나 읽기(ホンデ 등)는 한국어나 한자 이름으로 바꿔 넣는다. 장소가 여러 곳이면 "
            "한 곳씩 따로 부른다. 이미 location_id를 알면 origin_id를 쓰고 이 칸은 null"
        ),
    )
    origin_id: str | None = Field(
        default=None,
        max_length=40,
        description=(
            "거리 계산 기준 위치의 위치 ID. 앞 결과(resolve_location, search_clinics 등)의 location_id를 "
            "그대로 넣는다(예: area:hongdae, station:홍대입구). ID를 지어내지 않는다. 처음 말한 장소는 "
            "location에 넣는다. 둘 다 null이면 거리는 표시되지 않는다"
        ),
    )
    clinic_name: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "특정 병원·지점을 찾을 때만 쓴다. 병원 이름·브랜드·주소·역 이름의 단어를 공백으로 "
            "나눠 적으면 모든 단어가 들어간 지점만 찾는다. 예: '밴스 홍대', 'バンス 明洞', '블리비'. "
            "지점끼리 비교할 때는 지점마다 따로 부른다. 지점을 말하지 않았으면 null"
        ),
    )
    jp_clinic: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "사용자가 다니던 일본 병원과 직접 비교할 때만 쓴다. match_jp_treatment 결과의 jp_clinic 이름을 "
            "그대로 넣거나, 사용자가 말한 일본 병원 이름을 지점 이름 없이 넣는다(예: '品川', 'SBC'). 그 병원의 이 시술 "
            "가격이 데이터에 있으면 카드마다 그 가격 대비 절약액이 붙는다. 말하지 않았으면 null"
        ),
    )
    limit: int = Field(default=3, ge=1, le=10, description="돌려줄 병원 수. 기본 3")
    offset: int = Field(
        default=0,
        ge=0,
        le=200,
        description=(
            "순위표 앞에서 건너뛸 수. 기본 0. 사용자가 다른 병원·더 보여 달라고 하면 같은 입력으로 "
            "이미 보여 준 수(3, 그다음 6)를 넣는다. 조건(시술·지역·예산·정렬 등)을 바꾸면 0"
        ),
    )


class SearchTarget(BaseModel):
    """검색이 무엇을 대상으로 했는지. 같은 턴의 검색이 비교인지 재검색인지 가릴 때 쓴다(ADR 0009)."""

    area: AreaId | None = None
    # 기준 위치 ID. 같은 ID는 항상 같은 좌표이므로 ID로 같은 위치인지 판정한다.
    origin_id: str | None = None
    clinic_tokens: list[str] = Field(default_factory=list)
    # 지점 이름으로 찾은 병원 ID(시술과 무관). 이름은 단어가 아니라 찾은 병원으로 비교한다.
    clinic_ids: list[str] = Field(default_factory=list)

    def _narrows(self, other: "SearchTarget") -> bool:
        """self가 other의 조건을 모두 지키면서 더 좁거나 같은 검색인지."""
        if other.area and self.area != other.area:
            return False
        if other.origin_id and self.origin_id != other.origin_id:
            return False
        if other.clinic_tokens:
            return bool(self.clinic_tokens) and set(self.clinic_ids) <= set(
                other.clinic_ids
            )
        return True

    def compares_with(self, other: "SearchTarget") -> bool:
        """비교 검색이면 True, 범위를 좁히거나 넓힌 재검색이면 False.

        - 재검색: 한쪽이 다른 쪽의 조건을 모두 지키는 경우. 예: 전체 → 강남, "밴스" → "밴스 홍대"
          (밴스 홍대로 찾은 지점이 밴스로 찾은 지점 안에 있음), "밴스 홍대" → "밴스".
        - 비교: 그 밖의 경우. 예: 홍대와 강남, "밴스 홍대"와 "블리비 명동", 강남과 "밴스"처럼
          종류가 다른 대상.
        - 지점 이름으로 아무 병원도 찾지 못한 검색은 앞 검색을 지우지 않도록 비교로 본다.
        """
        if (self.clinic_tokens and not self.clinic_ids) or (
            other.clinic_tokens and not other.clinic_ids
        ):
            return True
        return not (self._narrows(other) or other._narrows(self))


class SearchClinicsArtifact(BaseModel):
    """화면용 결과(LLM에는 보내지 않음). finalize가 이걸로 카드를 조립한다."""

    menu_id: str
    # 검색을 구분하는 값. 그래프가 이번 턴의 검색 순서로 채운다(s1, s2, ...).
    search_id: str = ""
    target: SearchTarget = Field(default_factory=SearchTarget)
    cards: list[RecommendationCardData]
    exclusions: list[ExclusionData]
    # location 입력으로 찾은 기준 위치(사용자 표현 포함). resolve_location 결과와 같이 위치 카드·기억에 쓴다.
    location: LocationCardData | None = None


def _artifact(
    menu_id: str,
    target: SearchTarget,
    cards: list[RecommendationCardData],
    excluded: Counter[ExcludeReason],
    location: LocationCardData | None = None,
) -> dict:
    # 대화 기억(체크포인터)에 저장되므로 일반 데이터로 둔다.
    # 빠진 병원이 없는 사유는 내보내지 않는다.
    exclusions = [
        ExclusionData(menu_id=menu_id, reason=reason, clinic_count=count)
        for reason, count in excluded.items()
        if count > 0
    ]
    return SearchClinicsArtifact(
        menu_id=menu_id,
        target=target,
        cards=cards,
        exclusions=exclusions,
        location=location,
    ).model_dump()


def _menu_lines() -> str:
    return "\n".join(
        f"- {m.id}: {m.name_ko} / {m.name_ja} "
        f"(회복 최대 {m.downtime_max_days}일, 바늘 {'사용' if m.uses_needle else '없음'})"
        for m in get_catalog().menus
    )


DESCRIPTION = f"""\
서울 병원 가격 데이터에서 시술 하나를 받는 병원을 찾아 상위 몇 곳을 돌려준다.
가격·엔화 환산·일본 대비 절약액은 web 순위표와 같은 계산이다. 답변의 금액은 이 결과만 쓴다.
필수 입력: menu_id. 기본값: 절약액 순, 상위 3곳, 예산·회복 기간·지역·일본어 상담 제한 없음.
일본어 상담 가능 여부는 병원마다 결과에 표시되므로 거르지 않고 보여 준다. 사용자가 일본어 상담이 되는 곳만
원한다고 했을 때만 japanese_only=true.
사용자가 숙소·사는 곳·역을 말했으면 그 장소 표현을 location에 넣는다. resolve_location을 먼저 부르지 않는다.
결과 문장이 위치를 찾았는지 알려 준다. 못 찾았거나 여러 곳이면 그 문장의 안내대로 사용자에게 묻는다.
결과의 병원은 모두 카드로 화면에 나간다. card_reasons에 card_id마다 추천 이유 한 줄을 쓴다.
같은 턴에 같은 시술을 범위만 좁히거나 넓혀 다시 부르면 마지막 결과의 카드만 나간다. 지역·기준 위치·
지점(clinic_name)이 서로 다른 대상을 부르면 비교로 보고 모두 나간다. 비교할 때는 대상마다 한 번씩 부른다.
사용자가 다른 병원·더 보여 달라고 하면 앞 검색과 같은 입력으로 다시 부르고 offset에 이미 보여 준 수(3, 그다음 6)를
넣는다. 카드의 순위는 4위부터 이어진다. 조건을 바꿔 다시 찾을 때는 offset=0. 한 번에 더 많이 보려면
offset 대신 limit을 늘려 한 번만 부른다.
쓰면 안 되는 경우: 아래 목록에 없는 시술(필러, 실 리프팅 등)이나 시술을 아직 정하지 않았을 때.
시술명 없이 고민만 말했으면 recommend_treatments를 먼저 부른다. 아래 목록과 다른 일본 시술명(ハイフ, HIFU,
ブナジュ, 하이후 등)으로 물었으면 match_jp_treatment를 먼저 부르고, menu_id를 직접 짐작하지 않는다.
시술 목록:
{_menu_lines()}"""


@lru_cache
def get_fx_provider() -> FxProvider:
    """배포된 web의 환율 파일을 읽는 조회기(ADR 0010). 설정이 없으면 스냅샷 환율만 쓴다."""
    return FxProvider(get_settings().web_fx_url, get_catalog().fx)


def _jp_clinic_savings(benchmark: JpBenchmark, row: PriceRow) -> JpClinicSavings:
    savings_jpy, savings_pct = savings_against(benchmark.price_jpy, row.total_jpy)
    return JpClinicSavings(
        **jp_clinic_price(benchmark).model_dump(),
        savings_jpy=savings_jpy,
        savings_pct=savings_pct,
    )


def _card(
    row: PriceRow,
    show_distance: bool,
    fx: Fx,
    jp_clinic: JpBenchmark | None,
    rank: int,
) -> RecommendationCardData:
    jp_prices = [b.price_jpy for b in row.menu.jp_benchmarks]
    return RecommendationCardData(
        card_id=f"{row.place.id}:{row.menu.id}",
        rank=rank,
        place_id=row.place.id,
        menu_id=row.menu.id,
        clinic_name_ja=row.place.name_ja,
        clinic_name_ko=row.place.name_ko,
        menu_name_ja=row.menu.name_ja,
        menu_name_ko=row.menu.name_ko,
        area=row.place.area,
        station_ja=row.place.station_ja,
        station_ko=row.place.station_ko,
        japanese_support=row.place.japanese_support,
        base_krw=row.base_krw,
        vat_krw=row.vat_krw,
        total_krw=row.total_krw,
        total_jpy=row.total_jpy,
        krw_per_100_jpy=fx.krw_per_100_jpy,
        fx_date_label=fx.date_label,
        jp_average_jpy=row.jp_benchmark_jpy,
        jp_min_jpy=min(jp_prices),
        jp_max_jpy=max(jp_prices),
        jp_clinic_count=len(jp_prices),
        savings_jpy=row.savings_jpy,
        savings_pct=row.savings_pct,
        jp_clinic=_jp_clinic_savings(jp_clinic, row) if jp_clinic else None,
        distance_meters=row.distance_meters if show_distance else None,
        walk_minutes=row.walk_minutes if show_distance else None,
        price_tags=price_tags(row.place, row.menu.id, row.offer),
        price_condition_ja=row.place.caveat_ja,
        price_condition_ko=row.place.caveat_ko,
        price_note_ja=row.offer.note_ja,
        price_note_ko=row.offer.note_ko,
        source_url=row.place.source_url,
        rating=row.place.ai_rating,
        rating_scope=row.place.rating_scope,
        rating_tier=row.place.rating_tier,
        booking_channels=booking_channels(row.place),
        central_channel=row.place.central_channel,
        proof=_proof(row.place, row.menu.id),
    )


def booking_channels(place: Place) -> list[BookingChannel]:
    return [BookingChannel(**c.model_dump()) for c in place.channels]


def _proof(place: Place, menu_id: str) -> PriceProof:
    proof = place.proofs[menu_id]
    return PriceProof(
        url_ja=proof.url_ja,
        url_ko=proof.url_ko,
        domain_ja=proof.domain_ja,
        domain_ko=proof.domain_ko,
        captured_date=proof.captured_date,
        verified=proof.has_screenshot,
        matched_text=proof.matched_text,
    )


CHANNEL_NAMES = {
    "kakao": "KakaoTalk",
    "line": "LINE",
    "instagram": "Instagram",
    "naver_booking": "Naver Booking",
    "direct_reserve": "official web booking",
}


def channel_names(place: Place) -> str:
    """LLM용 채널 이름 목록. 링크는 LLM에 보내지 않는다(카드에만 있음)."""
    return ", ".join(CHANNEL_NAMES[c.kind] for c in place.channels) or "none in data"


def rating_text(rating: float, rating_tier: str, place: Place) -> str:
    """결과 문장의 평점 부분. 신규 개원 지점은 평점 대신 개원일과 집계 중임을 쓴다(이슈 #101).

    후기가 쌓이기 전의 평점은 보수적인 기준치라, 숫자를 보여 주면 Claude가 낮은 평점으로 설명한다.
    """
    if place.opened_date:
        return (
            f"new clinic opened {place.opened_date}, rating still being collected "
            "(do not describe its rating as low)"
        )
    return f"rating {rating}" + (
        " (few verified reviews)" if rating_tier == "limited" else ""
    )


def _summary_line(rank: int, card: RecommendationCardData, place: Place) -> str:
    parts = [
        f"{rank}. card_id={card.card_id}",
        f"{card.clinic_name_ko} / {card.clinic_name_ja} ({card.area})",
        f"₩{card.total_krw:,} incl. VAT ≈ ¥{card.total_jpy:,}",
        f"saves ¥{card.savings_jpy:,} ({card.savings_pct}%) vs Japan average",
        f"Japanese support: {'yes' if card.japanese_support else 'no'}",
        rating_text(card.rating, card.rating_tier, place),
        f"booking channels: {channel_names(place)}",
    ]
    if card.jp_clinic:
        parts.insert(
            4,
            f"saves ¥{card.jp_clinic.savings_jpy:,} ({card.jp_clinic.savings_pct}%) "
            f"vs {card.jp_clinic.name_ja}",
        )
    if card.distance_meters is not None:
        parts.append(f"{card.distance_meters:,} m (walk ~{card.walk_minutes} min)")
    if card.price_tags:
        parts.append(f"price tags: {', '.join(card.price_tags)}")
    if card.price_condition_ko:
        parts.append(
            f"price condition: {card.price_condition_ko} / {card.price_condition_ja}"
        )
    return " | ".join(parts)


@tool(
    TOOL_NAME,
    description=DESCRIPTION,
    args_schema=SearchClinicsInput,
    response_format="content_and_artifact",
)
def search_clinics(
    menu_id: MenuId,
    sort: SortId = "savings",
    budget: Budget | None = None,
    max_downtime_days: int | None = None,
    avoid_needle: bool = False,
    japanese_only: bool = False,
    area: AreaId | None = None,
    location: str | None = None,
    origin_id: str | None = None,
    clinic_name: str | None = None,
    jp_clinic: str | None = None,
    limit: int = 3,
    offset: int = 0,
) -> tuple[str, dict]:
    catalog = get_catalog()
    fx = get_fx_provider().current()
    menu = catalog.menu(menu_id)
    name = f"{menu.name_ko} / {menu.name_ja}"
    base = get_locations().get(origin_id) if origin_id else None
    if origin_id and base is None:
        raise ValueError(
            f"Unknown origin_id {origin_id!r}. Use a location_id from an earlier result as is, "
            "or pass the place the user gave as location."
        )
    found: LocationCardData | None = None
    location_note = ""
    if base is None and location and location.strip():
        base, location_note = _resolve_base(location)
        found = location_card(base, location) if base else None
    origin = base.point if base else None
    has_origin = origin is not None
    tokens = (clinic_name or "").strip().lower().split()
    target = SearchTarget(
        area=area,
        origin_id=base.id if base else None,
        clinic_tokens=tokens,
        clinic_ids=sorted(p.id for p in catalog.places if matches_place(p, tokens))
        if tokens
        else [],
    )

    # 시술 단위 거르기: 맞지 않으면 지역·일본어·지점 조건에 맞는 이 시술 병원이 모두 빠진다.
    def offered() -> int:
        return len(
            rank_rows(
                catalog,
                menu_id,
                japanese_only=japanese_only,
                area=area,
                search_query=clinic_name,
            )
        )

    def early(reason: ExcludeReason, message: str) -> tuple[str, dict]:
        count = offered()
        if count == 0 and tokens:
            message += " " + _no_clinic_note(clinic_name or "", tokens, menu_id)
        if location_note:
            message += " " + location_note
        return message, _artifact(menu_id, target, [], Counter({reason: count}), found)

    if avoid_needle and menu.uses_needle:
        return early(
            "needle", f"{name} uses a needle, so it is excluded. No clinics returned."
        )
    if max_downtime_days is not None and menu.downtime_max_days > max_downtime_days:
        return early(
            "downtime",
            f"{name} can show visible downtime for up to {menu.downtime_max_days} days, "
            f"more than the allowed {max_downtime_days}. No clinics returned.",
        )
    if sort == "distance" and not has_origin:
        raise ValueError(
            "sort=distance needs a base location. Pass the place the user gave (hotel, home "
            "area or station) as location. If the user has not given a place, or it was not "
            "found, ask for it once and search with sort=savings meanwhile. "
            + location_note
        )

    # 병원 단위 거르기. 정렬된 순서를 유지하며 처음 걸린 이유를 센다.
    excluded: Counter[ExcludeReason] = Counter()
    kept: list[PriceRow] = []
    # 지점 이름은 거르기 조건이 아니라 찾을 대상이므로, 이름에 맞지 않는 병원은 제외 수에 넣지 않는다.
    rows = rank_rows(
        catalog,
        menu_id,
        sort=sort,
        origin=origin,
        krw_per_100_jpy=fx.krw_per_100_jpy,
        search_query=clinic_name,
    )
    for row in rows:
        if area is not None and row.place.area != area:
            excluded["area"] += 1
        elif japanese_only and not row.place.japanese_support:
            excluded["japanese"] += 1
        elif budget is not None and budget.amount < (
            row.total_jpy if budget.currency == "JPY" else row.total_krw
        ):
            excluded["budget"] += 1
        else:
            kept.append(row)

    top = kept[offset : offset + limit]
    reference, reference_note = _jp_reference(menu, jp_clinic)
    cards = [
        _card(row, has_origin, fx, reference, rank)
        for rank, row in enumerate(top, start=offset + 1)
    ]
    jp_prices = [b.price_jpy for b in menu.jp_benchmarks]
    benchmark = (
        f"Japan benchmark: average of {len(jp_prices)} major Japanese clinics "
        f"(tax incl.) ¥{menu.jp_benchmark_jpy:,} "
        f"(range ¥{min(jp_prices):,}–¥{max(jp_prices):,})."
    )
    if reference_note:
        benchmark += " " + reference_note
    matched = f"{len(kept)} clinics matched"
    if excluded:
        matched += "; excluded: " + ", ".join(
            f"{n} {EXCLUDE_LABEL[r]}" for r, n in excluded.items()
        )
    lines = [
        f"search_clinics: {name}, sort={sort}"
        + (f", clinic_name={clinic_name!r}" if tokens else "")
        + (f", distances from {base.name_ko} / {base.name_ja}" if base else "")
        + ".",
        *([location_note] if location_note else []),
        benchmark,
        f"FX: ₩{fx.krw_per_100_jpy} per ¥100 ({fx.bank_ko}, {fx.date_label}).",
        matched + ".",
    ]
    if not rows and tokens:
        lines.append(_no_clinic_note(clinic_name or "", tokens, menu_id))
    if not top and offset and kept:
        lines.append(
            f"No more clinics: all {len(kept)} matching clinics were already shown "
            f"(offset={offset}). Say so and offer to change the conditions."
        )
    elif not top:
        lines.append("No clinics returned.")
    else:
        lines.append(
            f"Ranks {offset + 1}–{offset + len(top)} of {len(kept)} (ranks 1–{offset} "
            "were shown before):"
            if offset
            else f"Top {len(top)}:"
        )
        lines += [
            _summary_line(card.rank, card, row.place)
            for card, row in zip(cards, top, strict=True)
        ]
    return "\n".join(lines), _artifact(menu_id, target, cards, excluded, found)


def _resolve_base(query: str) -> tuple[Location | None, str]:
    """location 입력을 기준 위치로 바꾼다. 규칙은 resolve_location과 같다(ADR 0011, 이슈 #72).

    찾으면 그 위치로 거리를 계산하고, 못 찾았거나 여러 곳이면 거리 없이 검색하고 LLM이 물을 수 있게 알린다.
    """
    result = get_locations().resolve(query)
    head = f"Location {query!r}"
    if result.status == "found":
        found = result.locations[0]
        return found, (
            f"{head} → found {location_label(found)}. Distances are straight-line distances "
            "from this point. Use this location_id as origin_id when you search again from it."
        )
    if result.status == "ambiguous":
        listed = "; ".join(location_label(loc) for loc in result.locations)
        return None, (
            f"{head} names several places: {listed}. Searched without distances. If the user is "
            "comparing them, call search_clinics once per place with its location_id as "
            "origin_id. Otherwise ask which one is their base in the last sentence of answer."
        )
    return None, (
        f"{head} → unknown. No area or station in the data matches; searched without "
        "distances. Do not guess a location. Ask the user once, in the last sentence of answer, "
        "for the nearest subway station or one of the areas listed in resolve_location."
    )


def _jp_reference(menu: Menu, jp_clinic: str | None) -> tuple[JpBenchmark | None, str]:
    """사용자가 다니던 일본 병원(설계 4.7). 이 시술 가격이 있으면 그 병원과, 없으면 일본 평균과 비교한다."""
    if not jp_clinic or not jp_clinic.strip():
        return None, ""
    result = find_jp_clinic(menu, jp_clinic)
    if result.status == "found":
        b = result.benchmarks[0]
        return b, (
            f"Direct comparison: the user's Japanese clinic {b.name_ja} / {b.name_ko}, "
            f"{b.plan_ja} ¥{b.price_jpy:,} (tax incl.). Each card also shows savings against it. "
            f"Its unit may differ from {menu.name_ko}; if so, say so."
            + product_note_line(b)
        )
    if result.status == "ambiguous":
        listed = "; ".join(f"{b.name_ja} ({b.plan_ja})" for b in result.benchmarks)
        return None, (
            f"jp_clinic {jp_clinic!r} matches several Japanese clinics: {listed}. No direct "
            "comparison; ask which one and compare with the Japan average meanwhile."
        )
    return None, jp_clinic_not_found(menu, jp_clinic)


def _no_clinic_note(clinic_name: str, tokens: list[str], menu_id: str) -> str:
    """지점 이름으로 찾았는데 이 시술 가격이 없을 때, 이름에 맞는 지점과 가격이 있는 시술을 알려 준다."""
    catalog = get_catalog()
    found = [p for p in catalog.places if matches_place(p, tokens)]
    if not found:
        return (
            f"No clinic in the data matches clinic_name {clinic_name!r}. "
            "Try fewer words, such as the brand only. Do not search outside the data."
        )
    listed = "; ".join(
        f"{p.name_ko} / {p.name_ja}: "
        + ", ".join(catalog.menu(m).short_ko for m in p.prices)
        for p in found[:5]
    )
    return (
        f"Clinics matching {clinic_name!r} have no price for {menu_id} in the data. "
        f"They list prices for: {listed}."
    )
