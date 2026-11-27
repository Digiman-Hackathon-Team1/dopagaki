"""search_bundle: 시술 여러 개를 한 병원에서 받을 때 합계가 싼 병원을 찾는다(설계 문서 FR-03, #43).

시술 하나씩의 최저가 병원과 묶음 최저가 병원은 다를 수 있다. 여행자는 한 번 방문으로 끝내고 싶어 하므로
모든 시술을 가격표에 올린 병원만 후보로 두고 합계(부가세 포함)를 비교한다. 같은 조건에서 시술마다 가장
싼 병원에 따로 갈 때의 합계도 함께 계산해 차이를 보여 준다. 금액은 search_clinics와 같은 calc.py 계산이다.
"""

from collections import Counter
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from api.agent.schemas import BundleCardData, BundleItem, ExcludeReason, SeparatePick
from api.agent.tools.search_clinics import (
    EXCLUDE_LABEL,
    Budget,
    MenuId,
    SearchTarget,
    _proof,
    booking_channels,
    channel_names,
    get_fx_provider,
)
from api.domain.calc import PriceRow, rank_rows, savings_against
from api.domain.catalog import AreaId, Fx, get_catalog
from api.domain.locations import get_locations
from api.domain.price_tags import price_tags

TOOL_NAME = "search_bundle"

BundleSort = Literal["savings", "distance"]


class SearchBundleInput(BaseModel):
    menu_ids: list[MenuId] = Field(
        min_length=2,
        max_length=3,
        description="한 병원에서 같이 받을 시술 ID 2~3개. 사용자가 말한 순서로 넣는다",
    )
    sort: BundleSort = Field(
        default="savings",
        description=(
            "정렬 기준. 기본 savings(합계가 싼 순 = 일본 대비 절약액 큰 순). 사용자가 원할 때만 "
            "distance(가까운 순, origin_id 필요)"
        ),
    )
    budget: Budget | None = Field(
        default=None,
        description="묶음 합계 예산(한 번 방문에 쓸 돈). 말하지 않았으면 null(제한 없음)",
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
        description="사용자가 일본어 상담이 되는 곳만 원한다고 말했을 때만 true",
    )
    area: AreaId | None = Field(
        default=None, description="이 지역 병원만. 사용자가 지역을 정했을 때만"
    )
    origin_id: str | None = Field(
        default=None,
        max_length=40,
        description=(
            "거리 계산 기준 위치의 위치 ID. resolve_location 결과의 location_id를 그대로 넣는다. "
            "ID를 지어내지 않는다. 위치를 모르면 null"
        ),
    )
    limit: int = Field(default=3, ge=1, le=5, description="돌려줄 병원 수. 기본 3")

    @field_validator("menu_ids")
    @classmethod
    def _distinct(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("menu_ids must be different treatments")
        return value


class SearchBundleArtifact(BaseModel):
    """화면용 결과(LLM에는 보내지 않음). finalize가 이걸로 묶음 카드를 만든다."""

    menu_ids: list[str]
    # 묶음 검색을 구분하는 값. 그래프가 이번 턴의 묶음 검색 순서로 채운다(b1, b2, ...).
    search_id: str = ""
    # 같은 턴의 같은 묶음 검색이 비교인지 재검색인지 가릴 때 쓴다(search_clinics와 같은 규칙, ADR 0009).
    target: SearchTarget = Field(default_factory=SearchTarget)
    cards: list[BundleCardData]

    @property
    def origin_id(self) -> str | None:
        return self.target.origin_id


def _artifact(
    menu_ids: list[str], target: SearchTarget, cards: list[BundleCardData]
) -> dict:
    # 대화 기억(체크포인터)에 저장되므로 일반 데이터로 둔다.
    return SearchBundleArtifact(
        menu_ids=menu_ids, target=target, cards=cards
    ).model_dump()


def _menu_lines() -> str:
    return "\n".join(
        f"- {m.id}: {m.name_ko} / {m.name_ja}" for m in get_catalog().menus
    )


DESCRIPTION = f"""\
시술 2~3개를 한 병원에서 같이 받을 때 합계(부가세 포함)가 싼 병원을 찾는다. 모든 시술을 가격표에 올린 병원만
후보이고, 같은 조건에서 시술마다 가장 싼 병원에 따로 갈 때의 합계와 차이도 함께 돌려준다.
사용자가 여러 시술을 한 번에(한 병원에서, 한 번 방문으로) 받고 싶다고 하거나 여러 시술의 합계를 물으면 쓴다.
금액·합계·절약액은 web 순위표와 같은 계산이다. 답변의 금액은 이 결과만 쓰고 직접 더하지 않는다.
필수 입력: menu_ids(2~3개). 기본값: 합계가 싼 순, 상위 3곳, 예산·회복 기간·지역·일본어 상담 제한 없음.
예산은 묶음 합계 기준이다. 사용자가 숙소·역을 말했으면 먼저 resolve_location을 부르고 location_id를 origin_id에 넣는다.
결과의 병원은 묶음 카드로 화면에 나간다. card_reasons에 card_id마다 이유 한 줄을 쓴다.
예약 방법을 물으면 get_booking_options에 묶음 card_id를 그대로 넣는다(시술이 모두 문의 문장에 들어간다).
같은 묶음을 지역·기준 위치끼리 비교할 때는 대상마다 한 번씩 부른다(결과가 모두 카드로 나간다).
쓰면 안 되는 경우: 시술이 하나일 때(search_clinics를 쓴다), 4개 이상일 때(3개로 줄일지 묻거나 시술마다
search_clinics로 보여 주고 합계는 말하지 않는다), 아래 목록에 없는 시술, 시술을 아직 정하지 않았을 때.
시술 목록:
{_menu_lines()}"""


def _item(row: PriceRow) -> BundleItem:
    return BundleItem(
        menu_id=row.menu.id,
        menu_name_ja=row.menu.name_ja,
        menu_name_ko=row.menu.name_ko,
        base_krw=row.base_krw,
        vat_krw=row.vat_krw,
        total_krw=row.total_krw,
        total_jpy=row.total_jpy,
        jp_average_jpy=row.jp_benchmark_jpy,
        price_tags=price_tags(row.place, row.menu.id, row.offer),
        price_note_ja=row.offer.note_ja,
        price_note_ko=row.offer.note_ko,
        proof=_proof(row.place, row.menu.id),
    )


def _card(
    rows: list[PriceRow],
    separate: list[PriceRow],
    show_distance: bool,
    fx: Fx,
) -> BundleCardData:
    place = rows[0].place
    total_krw = sum(r.total_krw for r in rows)
    total_jpy = sum(r.total_jpy for r in rows)
    jp_total_jpy = sum(r.jp_benchmark_jpy for r in rows)
    savings_jpy, savings_pct = savings_against(jp_total_jpy, total_jpy)
    separate_krw = sum(r.total_krw for r in separate)
    separate_jpy = sum(r.total_jpy for r in separate)
    picks = [
        SeparatePick(
            menu_id=r.menu.id,
            menu_name_ja=r.menu.name_ja,
            menu_name_ko=r.menu.name_ko,
            place_id=r.place.id,
            clinic_name_ja=r.place.name_ja,
            clinic_name_ko=r.place.name_ko,
            total_krw=r.total_krw,
            total_jpy=r.total_jpy,
        )
        for r in separate
    ]
    return BundleCardData(
        card_id=f"{place.id}:{'+'.join(r.menu.id for r in rows)}",
        place_id=place.id,
        clinic_name_ja=place.name_ja,
        clinic_name_ko=place.name_ko,
        area=place.area,
        station_ja=place.station_ja,
        station_ko=place.station_ko,
        japanese_support=place.japanese_support,
        items=[_item(r) for r in rows],
        total_krw=total_krw,
        total_jpy=total_jpy,
        jp_total_jpy=jp_total_jpy,
        savings_jpy=savings_jpy,
        savings_pct=savings_pct,
        separate_total_krw=separate_krw,
        separate_total_jpy=separate_jpy,
        separate_clinic_count=len({r.place.id for r in separate}),
        separate_picks=picks,
        extra_vs_separate_krw=total_krw - separate_krw,
        extra_vs_separate_jpy=total_jpy - separate_jpy,
        krw_per_100_jpy=fx.krw_per_100_jpy,
        fx_date_label=fx.date_label,
        distance_meters=rows[0].distance_meters if show_distance else None,
        walk_minutes=rows[0].walk_minutes if show_distance else None,
        rating=place.ai_rating,
        rating_scope=place.rating_scope,
        rating_tier=place.rating_tier,
        booking_channels=booking_channels(place),
        central_channel=place.central_channel,
        price_condition_ja=place.caveat_ja,
        price_condition_ko=place.caveat_ko,
        source_url=place.source_url,
    )


def _summary_line(rank: int, card: BundleCardData) -> str:
    items = "; ".join(
        f"{i.menu_name_ko} / {i.menu_name_ja} ₩{i.total_krw:,} incl. VAT ≈ ¥{i.total_jpy:,}"
        for i in card.items
    )
    extra = card.extra_vs_separate_krw
    if card.separate_clinic_count == 1:
        # 시술마다 최저가 병원이 한 곳이면 그곳이 가장 싼 묶음이다. 따로 가는 쪽이 싸다고 쓰지 않는다.
        separate = (
            "this is the cheapest bundle"
            if extra == 0
            else f"₩{extra:,} (¥{card.extra_vs_separate_jpy:,}) more than the cheapest bundle "
            f"(clinic {card.separate_picks[0].place_id})"
        )
    elif extra > 0:
        separate = (
            f"₩{extra:,} (¥{card.extra_vs_separate_jpy:,}) more than going to "
            f"{card.separate_clinic_count} different clinics, each the cheapest for one treatment"
        )
    elif extra == 0:
        separate = "same total as going to each treatment's cheapest clinic separately"
    else:
        separate = (
            f"₩{-extra:,} (¥{-card.extra_vs_separate_jpy:,}) less than going to each "
            "treatment's cheapest clinic separately"
        )
    parts = [
        f"{rank}. card_id={card.card_id}",
        (
            f"clinic name in Korean: {card.clinic_name_ko}, in Japanese: {card.clinic_name_ja} "
            f"({card.area})"
        ),
        f"total ₩{card.total_krw:,} incl. VAT ≈ ¥{card.total_jpy:,}",
        (
            f"saves ¥{card.savings_jpy:,} ({card.savings_pct}%) vs Japan average total "
            f"¥{card.jp_total_jpy:,}"
        ),
        f"items: {items}",
        separate,
        f"Japanese support: {'yes' if card.japanese_support else 'no'}",
        f"rating {card.rating}"
        + (" (few verified reviews)" if card.rating_tier == "limited" else ""),
    ]
    if card.distance_meters is not None:
        parts.append(f"{card.distance_meters:,} m (walk ~{card.walk_minutes} min)")
    tags = sorted({t for i in card.items for t in i.price_tags})
    if tags:
        parts.append(f"price tags: {', '.join(tags)}")
    if card.price_condition_ko:
        parts.append(
            f"price condition: {card.price_condition_ko} / {card.price_condition_ja}"
        )
    return " | ".join(parts)


@tool(
    TOOL_NAME,
    description=DESCRIPTION,
    args_schema=SearchBundleInput,
    response_format="content_and_artifact",
)
def search_bundle(
    menu_ids: list[MenuId],
    sort: BundleSort = "savings",
    budget: Budget | None = None,
    max_downtime_days: int | None = None,
    avoid_needle: bool = False,
    japanese_only: bool = False,
    area: AreaId | None = None,
    origin_id: str | None = None,
    limit: int = 3,
) -> tuple[str, dict]:
    catalog = get_catalog()
    fx = get_fx_provider().current()
    menus = [catalog.menu(m) for m in menu_ids]
    names = " + ".join(f"{m.name_ko} / {m.name_ja}" for m in menus)
    location = get_locations().get(origin_id) if origin_id else None
    if origin_id and location is None:
        raise ValueError(
            f"Unknown origin_id {origin_id!r}. Use the location_id from a resolve_location "
            "result as is. If resolve_location did not find the place, search with origin_id=null."
        )
    if sort == "distance" and location is None:
        raise ValueError(
            "sort=distance needs origin_id. Call resolve_location with the place the user gave "
            "and pass its location_id. If the user has not given a place, ask once and search "
            "with sort=savings meanwhile."
        )
    target = SearchTarget(area=area, origin_id=origin_id if location else None)

    # 시술 단위 거르기: 하나라도 맞지 않으면 묶음을 만들지 않고 어느 시술 때문인지 알린다.
    blocked = []
    for m in menus:
        if avoid_needle and m.uses_needle:
            blocked.append(f"{m.name_ko} / {m.name_ja} uses a needle")
        elif max_downtime_days is not None and m.downtime_max_days > max_downtime_days:
            blocked.append(
                f"{m.name_ko} / {m.name_ja} can show downtime for up to "
                f"{m.downtime_max_days} days, more than the allowed {max_downtime_days}"
            )
    if blocked:
        return (
            f"search_bundle: {names}. No bundle returned: "
            + "; ".join(blocked)
            + ". Tell the user, and search again without that treatment if they agree "
            "(use search_clinics if only one treatment remains).",
            _artifact(list(menu_ids), target, []),
        )

    origin = location.point if location else None
    by_menu = {
        m.id: rank_rows(
            catalog,
            m.id,
            origin=origin,
            krw_per_100_jpy=fx.krw_per_100_jpy,
        )
        for m in menus
    }

    def allowed(row: PriceRow) -> ExcludeReason | None:
        if area is not None and row.place.area != area:
            return "area"
        if japanese_only and not row.place.japanese_support:
            return "japanese"
        return None

    # 따로 받을 때: 같은 지역·일본어 조건에서 시술마다 가장 싼(절약액 큰) 병원. 예산은 보지 않는다.
    # 절약액이 같은 병원이 여럿이면, 다른 시술에서도 최저가인 병원을 먼저 골라 병원 수를 줄인다.
    ties: list[list[PriceRow]] = []
    for m in menus:
        rows = [r for r in by_menu[m.id] if allowed(r) is None]
        if rows:
            ties.append([r for r in rows if r.savings_jpy == rows[0].savings_jpy])
    tie_places = [{r.place.id for r in t} for t in ties]
    separate: list[PriceRow] = []
    for t in ties:
        chosen = {r.place.id for r in separate}
        separate.append(
            min(
                t,
                key=lambda r: (
                    r.place.id not in chosen,
                    -sum(r.place.id in ids for ids in tie_places),
                ),
            )
        )

    rows_by_place = {m.id: {r.place.id: r for r in by_menu[m.id]} for m in menus}
    candidates = [
        [rows_by_place[m.id][place_id] for m in menus]
        for place_id in rows_by_place[menus[0].id]
        if all(place_id in rows_by_place[m.id] for m in menus)
    ]
    excluded: Counter[ExcludeReason] = Counter()
    kept: list[list[PriceRow]] = []
    for rows in candidates:
        reason = allowed(rows[0])
        total_jpy = sum(r.total_jpy for r in rows)
        total_krw = sum(r.total_krw for r in rows)
        if reason is None and budget is not None:
            spent = total_jpy if budget.currency == "JPY" else total_krw
            if budget.amount < spent:
                reason = "budget"
        if reason:
            excluded[reason] += 1
        else:
            kept.append(rows)

    if sort == "distance":
        kept.sort(
            key=lambda rows: (rows[0].distance_meters, sum(r.total_krw for r in rows))
        )
    else:
        # 같은 시술 묶음이면 일본 평균 합계가 같으므로 합계가 싼 순 = 절약액 큰 순이다.
        kept.sort(
            key=lambda rows: (sum(r.total_jpy for r in rows), rows[0].distance_meters)
        )

    complete_separate = len(separate) == len(menus)
    top = kept[:limit]
    cards = (
        [_card(rows, separate, location is not None, fx) for rows in top]
        if complete_separate
        else []
    )

    jp_total = sum(m.jp_benchmark_jpy for m in menus)
    matched = f"{len(candidates)} clinics list all {len(menus)} treatments"
    if excluded:
        matched += "; excluded: " + ", ".join(
            f"{n} {EXCLUDE_LABEL[r]}" for r, n in excluded.items()
        )
    lines = [
        f"search_bundle: {names}, sort={sort}"
        + (
            f", distances from {location.name_ko} / {location.name_ja}"
            if location
            else ""
        )
        + ".",
        f"Japan benchmark: sum of each treatment's Japan average (tax incl.) ¥{jp_total:,}.",
        f"FX: ₩{fx.krw_per_100_jpy} per ¥100 ({fx.bank_ko}, {fx.date_label}).",
        matched + ".",
    ]
    if budget is not None:
        symbol = "¥" if budget.currency == "JPY" else "₩"
        lines.append(
            f"Budget {symbol}{budget.amount:,} is for the bundle total"
            + ("; every bundle below is within it." if cards else ".")
        )
    if complete_separate:
        picks = "; ".join(
            f"{r.menu.name_ko} / {r.menu.name_ja} at clinic name in Korean: {r.place.name_ko}, "
            f"in Japanese: {r.place.name_ja} ₩{r.total_krw:,} ≈ ¥{r.total_jpy:,}"
            for r in separate
        )
        lines.append(
            "Separately, each at its cheapest clinic (budget not applied): ₩"
            f"{sum(r.total_krw for r in separate):,} incl. VAT ≈ ¥"
            f"{sum(r.total_jpy for r in separate):,} across "
            f"{len({r.place.id for r in separate})} clinic(s): {picks}. This is not a "
            "single-clinic total."
        )
    over_budget = [
        rows
        for rows in candidates
        if allowed(rows[0]) is None
        and budget is not None
        and budget.amount
        < sum(r.total_jpy if budget.currency == "JPY" else r.total_krw for r in rows)
    ]
    if not cards and not kept and over_budget:
        cheapest = min(over_budget, key=lambda rows: sum(r.total_jpy for r in rows))
        lines.append(
            "Cheapest single-clinic bundle, over budget: clinic name in Korean: "
            f"{cheapest[0].place.name_ko}, in Japanese: {cheapest[0].place.name_ja}, ₩"
            f"{sum(r.total_krw for r in cheapest):,} incl. VAT ≈ ¥"
            f"{sum(r.total_jpy for r in cheapest):,}. Tell the user how much over budget "
            "it is only by quoting these amounts."
        )
    if not cards:
        if not candidates:
            lines.append(
                "No clinic in the data lists all of these treatments. Say so, and offer to "
                "search each treatment separately with search_clinics."
            )
        lines.append("No bundles returned.")
    else:
        lines.append(f"Top {len(cards)} single-clinic bundles:")
        lines += [_summary_line(i, c) for i, c in enumerate(cards, start=1)]
        lines.append(
            "Booking channels of the first clinic: "
            + channel_names(catalog.place(cards[0].place_id))
        )
    return "\n".join(lines), _artifact(list(menu_ids), target, cards)
