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
from api.agent.schemas import ExcludeReason, Exclusion, RecommendationCardData
from api.domain.calc import (
    PriceRow,
    SortId,
    is_point_in_south_korea,
    matches_place,
    rank_rows,
)
from api.domain.catalog import AreaId, Fx, GeoPoint, get_catalog
from api.domain.fx import FxProvider
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
            "사용자가 원할 때만 distance(가까운 순, origin 필요) 또는 rating(후기 평점 순)"
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
        description="일본어 상담 가능 병원만. 여행자는 일본어가 필요하다고 보고 true",
    )
    area: AreaId | None = Field(
        default=None, description="이 지역 병원만. 사용자가 지역을 정했을 때만"
    )
    origin: GeoPoint | None = Field(
        default=None,
        description=(
            "거리 계산 기준 좌표(숙소 등). 위치를 좌표로 바꾸는 도구의 결과만 쓰고 좌표를 "
            "지어내지 않는다. 그런 도구가 없으면 null로 둔다. null이면 거리는 표시되지 않는다"
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
    limit: int = Field(default=3, ge=1, le=10, description="돌려줄 병원 수. 기본 3")


class SearchTarget(BaseModel):
    """검색이 무엇을 대상으로 했는지. 같은 턴의 검색이 비교인지 재검색인지 가릴 때 쓴다(ADR 0009)."""

    area: AreaId | None = None
    origin: GeoPoint | None = None
    clinic_tokens: list[str] = Field(default_factory=list)

    def compares_with(self, other: "SearchTarget") -> bool:
        """서로 다른 지역·기준 위치·지점을 지정했으면 비교 검색이다.

        한쪽만 지정했거나(전체 → 강남) 지점 이름이 더 좁아졌으면(밴스 → 밴스 홍대) 재검색이다.
        """
        if self.area and other.area and self.area != other.area:
            return True
        if self.origin and other.origin and self.origin != other.origin:
            return True
        mine, theirs = set(self.clinic_tokens), set(other.clinic_tokens)
        return bool(mine and theirs and not (mine <= theirs or theirs <= mine))


class SearchClinicsArtifact(BaseModel):
    """화면용 결과(LLM에는 보내지 않음). finalize가 이걸로 카드를 조립한다."""

    menu_id: str
    # 검색을 구분하는 값. 그래프가 도구 호출 ID로 채운다.
    search_id: str = ""
    target: SearchTarget = Field(default_factory=SearchTarget)
    cards: list[RecommendationCardData]
    exclusions: list[Exclusion]


def _artifact(
    menu_id: str,
    target: SearchTarget,
    cards: list[RecommendationCardData],
    excluded: Counter[ExcludeReason],
) -> dict:
    # 대화 기억(체크포인터)에 저장되므로 일반 데이터로 둔다.
    exclusions = [
        Exclusion(menu_id=menu_id, reason=reason, clinic_count=count)
        for reason, count in excluded.items()
    ]
    return SearchClinicsArtifact(
        menu_id=menu_id, target=target, cards=cards, exclusions=exclusions
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
필수 입력: menu_id. 기본값: 절약액 순, 상위 3곳, 예산·회복 기간·지역 제한 없음.
결과의 병원은 모두 카드로 화면에 나간다. card_reasons에 card_id마다 추천 이유 한 줄을 쓴다.
같은 턴에 같은 시술을 다시 부르면 마지막 결과의 카드만 나간다. 단, 지역·기준 위치·지점(clinic_name)을
서로 다르게 지정한 검색은 비교로 보고 모두 나간다. 지점이나 지역을 비교할 때는 대상마다 한 번씩 부른다.
쓰면 안 되는 경우: 아래 목록에 없는 시술(필러, 실 리프팅 등)이나 시술을 아직 정하지 않았을 때.
시술 목록:
{_menu_lines()}"""


@lru_cache
def get_fx_provider() -> FxProvider:
    """배포된 web의 환율 파일을 읽는 조회기(ADR 0010). 설정이 없으면 스냅샷 환율만 쓴다."""
    return FxProvider(get_settings().web_fx_url, get_catalog().fx)


def _card(row: PriceRow, show_distance: bool, fx: Fx) -> RecommendationCardData:
    jp_prices = [b.price_jpy for b in row.menu.jp_benchmarks]
    return RecommendationCardData(
        card_id=f"{row.place.id}:{row.menu.id}",
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
        distance_meters=row.distance_meters if show_distance else None,
        walk_minutes=row.walk_minutes if show_distance else None,
        price_tags=price_tags(row.place, row.menu.id, row.offer),
        price_condition_ja=row.place.caveat_ja,
        price_condition_ko=row.place.caveat_ko,
        price_note_ja=row.offer.note_ja,
        price_note_ko=row.offer.note_ko,
        source_url=row.place.source_url,
    )


def _summary_line(rank: int, card: RecommendationCardData, rating: float) -> str:
    parts = [
        f"{rank}. card_id={card.card_id}",
        f"{card.clinic_name_ko} / {card.clinic_name_ja} ({card.area})",
        f"₩{card.total_krw:,} incl. VAT ≈ ¥{card.total_jpy:,}",
        f"saves ¥{card.savings_jpy:,} ({card.savings_pct}%) vs Japan average",
        f"Japanese support: {'yes' if card.japanese_support else 'no'}",
        f"rating {rating}",
    ]
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
    origin: GeoPoint | None = None,
    clinic_name: str | None = None,
    limit: int = 3,
) -> tuple[str, dict]:
    catalog = get_catalog()
    fx = get_fx_provider().current()
    menu = catalog.menu(menu_id)
    name = f"{menu.name_ko} / {menu.name_ja}"
    has_origin = is_point_in_south_korea(origin)
    tokens = (clinic_name or "").strip().lower().split()
    target = SearchTarget(
        area=area, origin=origin if has_origin else None, clinic_tokens=tokens
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

    if avoid_needle and menu.uses_needle:
        message = f"{name} uses a needle, so it is excluded. No clinics returned."
        return message, _artifact(menu_id, target, [], Counter(needle=offered()))
    if max_downtime_days is not None and menu.downtime_max_days > max_downtime_days:
        reason = (
            f"{name} can show visible downtime for up to {menu.downtime_max_days} days, "
            f"more than the allowed {max_downtime_days}. No clinics returned."
        )
        return reason, _artifact(menu_id, target, [], Counter(downtime=offered()))
    if sort == "distance" and not has_origin:
        raise ValueError(
            "sort=distance needs an origin inside South Korea from a location tool. "
            "Do not guess coordinates. If no location tool is available, search with "
            "sort=savings and tell the user that sorting by distance is not available yet."
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

    top = kept[:limit]
    cards = [_card(row, has_origin, fx) for row in top]
    jp_prices = [b.price_jpy for b in menu.jp_benchmarks]
    benchmark = (
        f"Japan benchmark: average of {len(jp_prices)} major Japanese clinics "
        f"(tax incl.) ¥{menu.jp_benchmark_jpy:,} "
        f"(range ¥{min(jp_prices):,}–¥{max(jp_prices):,})."
    )
    matched = f"{len(kept)} clinics matched"
    if excluded:
        matched += "; excluded: " + ", ".join(
            f"{n} {EXCLUDE_LABEL[r]}" for r, n in excluded.items()
        )
    lines = [
        f"search_clinics: {name}, sort={sort}"
        + (f", clinic_name={clinic_name!r}" if tokens else "")
        + ".",
        benchmark,
        f"FX: ₩{fx.krw_per_100_jpy} per ¥100 ({fx.bank_ko}, {fx.date_label}).",
        matched + ".",
    ]
    if origin is not None and not has_origin:
        lines.append(
            "origin ignored: it is outside South Korea, so no distances are shown."
        )
    if not rows and tokens:
        lines.append(_no_clinic_note(clinic_name or "", tokens, menu_id))
    if not top:
        lines.append("No clinics returned.")
    else:
        lines.append(f"Top {len(top)}:")
        lines += [
            _summary_line(i, card, row.place.ai_rating)
            for i, (card, row) in enumerate(zip(cards, top, strict=True), start=1)
        ]
    return "\n".join(lines), _artifact(menu_id, target, cards, excluded)


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
