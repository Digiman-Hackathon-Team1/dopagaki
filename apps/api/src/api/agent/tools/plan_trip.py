"""plan_trip: 시술을 언제 받을지 날짜·순서로 정리한다(설계 문서 FR-11, 9장, #45, ADR 0017).

- 여행자: 병원에 갈 수 있는 첫날(1일째)부터 귀국일(trip_days일째) 안에서, 귀국일까지 회복되는 시술만
  날짜별로 둔다. 기본은 시술마다 가장 싼 병원이고, 회복이 긴 방문을 앞날에 두며 하루에 병원 한 곳씩
  배치한다. 사용자가 한 병원에서 같이 받고 싶다고 하면(one_clinic) search_bundle과 같은 계산으로 합계가
  가장 싼 병원 한 곳을 1일째에 둔다.
- 거주자: 다음 약속(얼굴이 깨끗해야 하는 날)과 예산 기준으로 지금 받을 시술과 나중에 받을 시술(회복 기간
  때문에 약속 뒤, 예산 때문에 다음 달)을 나눈다. 병원은 기준 위치 3km 안(없는 시술은 6km)에서 고른다.
  버튼식 상담(web concierge.ts `planConcierge`)의 거주자 결과와 같은 기준이다.

회복 기간은 "시술 날부터 귀국·약속 날까지 날 수"와 비교한다(2박 3일 첫날 시술이면 2일, 내일 약속이면 1일).
금액은 search_clinics·search_bundle과 같은 calc.py 계산이다. 영업시간·요일 한정가는 데이터에 없어 쓰지 않는다
(설계 문서 9장 데이터 공백).
"""

from collections.abc import Sequence
from itertools import combinations

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator, model_validator

from api.agent.schemas import (
    PlanAlternative,
    PlanBudget,
    PlanDeferred,
    PlanItem,
    PlanOption,
    PlanReason,
    PlanVisit,
    TripPlanCardData,
    Visitor,
)
from api.agent.tools.recommend_treatments import plan_treatments
from api.agent.tools.search_bundle import _item, separate_picks
from api.agent.tools.search_clinics import (
    Budget,
    MenuId,
    booking_channels,
    get_fx_provider,
)
from api.domain.calc import PriceRow, rank_rows, savings_against
from api.domain.catalog import ConcernId, Fx, Menu, get_catalog
from api.domain.locations import Location, get_locations

TOOL_NAME = "plan_trip"

# 거주자 "가까운 병원" 기준(web concierge.ts NEAR_RADIUS_M, WIDE_RADIUS_M)
NEAR_RADIUS_M = 3000
WIDE_RADIUS_M = 6000
# 한 병원에서 같이 받는 시술 수 상한(search_bundle·예약 문의 문장과 같음)
ONE_CLINIC_MAX = 3


class PlanTripInput(BaseModel):
    visitor: Visitor = Field(
        description="traveler 여행자, resident 서울 거주자. 사용자가 서울에 산다고 하지 않았으면 traveler"
    )
    menu_ids: list[MenuId] | None = Field(
        default=None,
        min_length=1,
        max_length=5,
        description="사용자가 말한 시술 ID 1~5개(말한 순서). 시술을 말하지 않고 고민만 말했으면 null",
    )
    concerns: list[ConcernId] | None = Field(
        default=None,
        min_length=1,
        max_length=3,
        description=(
            "시술 대신 고민만 말했을 때 recommend_treatments와 같은 고민 분류(신경 쓰는 순). "
            "코드가 고민별 시술표로 후보를 정한다. menu_ids를 넣었으면 null"
        ),
    )
    trip_days: int | None = Field(
        default=None,
        ge=1,
        le=30,
        description=(
            "여행자만: 병원에 갈 수 있는 첫날(도착일, 이미 서울이면 오늘)부터 귀국일까지 날 수, 두 날 모두 "
            "센다. 2박 3일 → 3, 오늘 기준 모레 귀국 → 3, 내일 귀국 → 2, 오늘 귀국 → 1. 거주자는 null"
        ),
    )
    days_until_event: int | None = Field(
        default=None,
        ge=0,
        le=60,
        description=(
            "거주자만: 오늘부터 다음 약속(얼굴을 보여야 하는 날)까지 날 수. 오늘 → 0, 내일 → 1, 모레 → 2. "
            "약속이 없거나 말하지 않았으면 null. 여행자는 null"
        ),
    )
    budget: Budget | None = Field(
        default=None,
        description=(
            "이번 일정 전체 예산(거주자는 이번 달 예산). 말하지 않았으면 null(제한 없음)"
        ),
    )
    one_clinic: bool = Field(
        default=False,
        description=(
            "사용자가 시술을 한 병원에서 같이(한 번 방문으로) 받고 싶다고 했을 때만 true. "
            "한 병원에서는 3개까지 같이 받는다"
        ),
    )
    avoid_needle: bool = Field(
        default=False, description="사용자가 바늘 시술을 피하고 싶다고 했을 때만 true"
    )
    japanese_only: bool = Field(
        default=False,
        description="사용자가 일본어 상담이 되는 곳만 원한다고 말했을 때만 true",
    )
    origin_id: str | None = Field(
        default=None,
        max_length=40,
        description=(
            "숙소(여행자)나 사는 동네(거주자)의 위치 ID. resolve_location 결과의 location_id를 그대로 "
            "넣는다. 거주자는 이 위치에서 가까운 병원만 고른다. 모르면 null"
        ),
    )

    @field_validator("menu_ids")
    @classmethod
    def _distinct_menus(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("menu_ids must be different treatments")
        return value

    @model_validator(mode="after")
    def _visitor_fields(self) -> "PlanTripInput":
        if (self.menu_ids is None) == (self.concerns is None):
            raise ValueError(
                "Pass exactly one of menu_ids (treatments the user named) or concerns"
            )
        if self.visitor == "traveler":
            if self.trip_days is None:
                raise ValueError(
                    "A traveler plan needs trip_days. If the user has not said how long they "
                    "stay or when they go home, ask first."
                )
            if self.days_until_event is not None:
                raise ValueError("days_until_event is for residents; use trip_days")
        elif self.trip_days is not None:
            raise ValueError(
                "trip_days is for travelers; for a resident pass days_until_event"
            )
        return self


class PlanTripArtifact(BaseModel):
    """화면용 결과(LLM에는 보내지 않음). finalize가 이걸로 일정 카드를 만든다."""

    origin_id: str | None = None
    plan: TripPlanCardData


def _menu_lines() -> str:
    return "\n".join(
        f"- {m.id}: {m.name_ko} / {m.name_ja}, visible downtime up to "
        f"{m.downtime_max_days} days{', needle' if m.uses_needle else ''}"
        for m in get_catalog().menus
    )


DESCRIPTION = f"""\
시술을 언제 받을지 일정으로 정리한다(일정 카드). 금액·날짜·순서는 코드가 정한다.
- 여행자(visitor=traveler): 1일째(도착일 또는 오늘)부터 귀국일(trip_days일째) 안에서 귀국일까지 회복되는 시술만
  날짜별로 둔다. 기본은 시술마다 가장 싼 병원이고, 회복이 긴 시술을 앞날에, 하루에 병원 한 곳씩 둔다.
  귀국일에는 회복 기간이 없는 시술만 둔다. 사용자가 한 병원에서 같이 받고 싶다고 하면 one_clinic=true.
- 거주자(visitor=resident): 다음 약속과 예산 기준으로 지금 받을 것과 나중에 받을 것(회복 기간 때문에 약속 뒤,
  예산 때문에 다음 달)을 나눈다. origin_id가 있으면 그 위치 3km 안(없으면 6km)의 병원만 고른다.
- 예산이 있으면 예산 안에서 일본 대비 절약액이 가장 큰 조합을 고르고, 남은 시술은 예산 초과로 뺀다.
사용자가 일정·스케줄을 짜 달라고 하거나("2박 3일 동안 짜 줘"), 여러 시술을 언제 받을지, 지금 받을 것과 나중에
받을 것을 물으면 쓴다. 고민만 말했으면 recommend_treatments를 부르지 말고 concerns를 바로 넣는다.
필수 입력: visitor, menu_ids 또는 concerns 중 하나, 여행자는 trip_days.
기본값: 예산 제한 없음, 바늘 상관없음, 일본어 상담 거르지 않음, 시술마다 가장 싼 병원.
사용자가 숙소·사는 동네를 말했으면 먼저 resolve_location을 부르고 location_id를 origin_id에 넣는다.
결과의 일정은 카드로 화면에 나간다. 답변에는 날짜(또는 순서)별로 무엇을 어디서 받는지와, 넣지 않은 시술의 이유를
쓴다. 금액은 결과 그대로 쓴다. 방문마다 card_id가 있고 예약 방법을 물으면 get_booking_options에 그대로 넣는다.
쓰면 안 되는 경우: 여행자가 체류 기간(귀국일)을 말하지 않았을 때(먼저 묻는다), 병원 가격만 물을 때
(search_clinics), 한 병원 합계만 물을 때(search_bundle), 아래 목록에 없는 시술.
시술 목록:
{_menu_lines()}"""


def _plan_item(row: PriceRow) -> PlanItem:
    return PlanItem(
        **_item(row).model_dump(), downtime_max_days=row.menu.downtime_max_days
    )


def _visit(rows: list[PriceRow], day: int | None, show_distance: bool) -> PlanVisit:
    place = rows[0].place
    return PlanVisit(
        card_id=f"{place.id}:{'+'.join(r.menu.id for r in rows)}",
        day=day,
        place_id=place.id,
        clinic_name_ja=place.name_ja,
        clinic_name_ko=place.name_ko,
        area=place.area,
        station_ja=place.station_ja,
        station_ko=place.station_ko,
        japanese_support=place.japanese_support,
        items=[_plan_item(r) for r in rows],
        total_krw=sum(r.total_krw for r in rows),
        total_jpy=sum(r.total_jpy for r in rows),
        downtime_max_days=max(r.menu.downtime_max_days for r in rows),
        distance_meters=rows[0].distance_meters if show_distance else None,
        walk_minutes=rows[0].walk_minutes if show_distance else None,
        rating=place.ai_rating,
        rating_scope=place.rating_scope,
        rating_tier=place.rating_tier,
        booking_channels=booking_channels(place),
        central_channel=place.central_channel,
        price_condition_ja=place.caveat_ja,
        price_condition_ko=place.caveat_ko,
    )


def _option(row: PriceRow, show_distance: bool) -> PlanOption:
    return PlanOption(
        place_id=row.place.id,
        clinic_name_ja=row.place.name_ja,
        clinic_name_ko=row.place.name_ko,
        total_krw=row.total_krw,
        total_jpy=row.total_jpy,
        distance_meters=row.distance_meters if show_distance else None,
    )


def _one_clinic(pools: Sequence[list[PriceRow]]) -> list[PriceRow] | None:
    """모든 시술을 가격표에 올린 병원 중 엔 합계가 가장 싼 한 곳(search_bundle 정렬과 같음)."""
    by_place = [{r.place.id: r for r in rows} for rows in pools]
    best: list[PriceRow] | None = None
    for place_id in by_place[0]:
        if not all(place_id in rows for rows in by_place):
            continue
        rows = [rows[place_id] for rows in by_place]
        key = (sum(r.total_jpy for r in rows), rows[0].distance_meters)
        if best is None or key < (
            sum(r.total_jpy for r in best),
            best[0].distance_meters,
        ):
            best = rows
    return best


def _group(rows: list[PriceRow]) -> list[list[PriceRow]]:
    """같은 병원 시술을 한 방문으로 묶는다. 한 방문은 3개까지다(예약 문의 문장 상한, get_booking_options).

    3개를 넘으면 회복이 긴 시술부터 3개씩 나눠 방문을 따로 만든다.
    """
    by_place: dict[str, list[PriceRow]] = {}
    for row in rows:
        by_place.setdefault(row.place.id, []).append(row)
    visits = []
    for place_rows in by_place.values():
        ordered = sorted(place_rows, key=lambda r: -r.menu.downtime_max_days)
        visits += [
            ordered[i : i + ONE_CLINIC_MAX]
            for i in range(0, len(ordered), ONE_CLINIC_MAX)
        ]
    return visits


def _downtime(visit: list[PriceRow]) -> int:
    return max(r.menu.downtime_max_days for r in visit)


def _schedule_traveler(
    rows: list[PriceRow], trip_days: int
) -> list[tuple[int, list[PriceRow]]]:
    """방문을 날짜에 둔다. 회복이 긴 방문이 앞날이고, 하루에 한 곳씩, 날이 모자라면 같은 날 여러 곳.

    방문 날짜는 그 방문의 가장 긴 회복 기간으로 정한 마지막 날(귀국일 - 회복 일수)을 넘지 않는다.
    마지막 날에 걸려 앞 방문과 같은 날이 되면 다음 방문은 비어 있는 다음 날부터 채운다.
    """
    ordered = sorted(
        enumerate(_group(rows)),
        key=lambda iv: (trip_days - _downtime(iv[1]), -_downtime(iv[1]), iv[0]),
    )
    out = []
    cursor = 1
    for _, visit in ordered:
        day = min(cursor, trip_days - _downtime(visit))
        if day == cursor:
            cursor += 1
        out.append((day, visit))
    return sorted(out, key=lambda dv: dv[0])


def _resident_visits(rows: list[PriceRow]) -> list[list[PriceRow]]:
    """거주자 방문 순서: 회복이 긴 방문부터(약속 전에 먼저 받아 둔다)."""
    return [
        v
        for _, v in sorted(
            enumerate(_group(rows)), key=lambda iv: (-_downtime(iv[1]), iv[0])
        )
    ]


def _name(menu: Menu) -> str:
    return f"{menu.name_ko} / {menu.name_ja}"


def _clinic(row: PriceRow) -> str:
    return (
        f"clinic name in Korean: {row.place.name_ko}, in Japanese: {row.place.name_ja}"
    )


def _price(krw: int, jpy: int) -> str:
    return f"₩{krw:,} incl. VAT ≈ ¥{jpy:,}"


REASON_TEXT: dict[Visitor, dict[PlanReason, str]] = {
    "traveler": {
        "downtime": "not recovered by the return day",
        "budget": "over the trip budget",
        "needle": "uses a needle and the user avoids needles",
        "far": "no clinic nearby",
        "no_clinic": "no clinic in the data meets the conditions",
        "one_clinic": "no single clinic offers it together with the planned treatments "
        "(or more than 3 at one clinic)",
    },
    "resident": {
        "downtime": "after the event",
        "budget": "next month (over this month's budget)",
        "needle": "uses a needle and the user avoids needles",
        "far": f"no clinic within {WIDE_RADIUS_M // 1000} km of the base location",
        "no_clinic": "no clinic in the data meets the conditions",
        "one_clinic": "no single clinic offers it together with the planned treatments "
        "(or more than 3 at one clinic)",
    },
}


@tool(
    TOOL_NAME,
    description=DESCRIPTION,
    args_schema=PlanTripInput,
    response_format="content_and_artifact",
)
def plan_trip(
    visitor: Visitor,
    menu_ids: list[MenuId] | None = None,
    concerns: list[ConcernId] | None = None,
    trip_days: int | None = None,
    days_until_event: int | None = None,
    budget: Budget | None = None,
    one_clinic: bool = False,
    avoid_needle: bool = False,
    japanese_only: bool = False,
    origin_id: str | None = None,
) -> tuple[str, dict]:
    catalog = get_catalog()
    fx = get_fx_provider().current()
    location = get_locations().get(origin_id) if origin_id else None
    if origin_id and location is None:
        raise ValueError(
            f"Unknown origin_id {origin_id!r}. Use the location_id from a resolve_location "
            "result as is. If resolve_location did not find the place, plan with origin_id=null."
        )
    if concerns is not None:
        menus = [
            c.menu for c in plan_treatments(list(dict.fromkeys(concerns))).candidates
        ]
    else:
        menus = [catalog.menu(m) for m in menu_ids or []]
    resident = visitor == "resident"
    near = resident and location is not None

    # 시술마다 보낼 수 있는 병원(절약액 순). 거주자는 기준 위치 3km, 없으면 6km 안.
    pools: dict[str, list[PriceRow]] = {}
    radii: dict[str, int] = {}
    deferred: dict[str, list[PlanReason]] = {}
    for m in menus:
        rows = rank_rows(
            catalog,
            m.id,
            origin=location.point if location else None,
            japanese_only=japanese_only,
            krw_per_100_jpy=fx.krw_per_100_jpy,
        )
        pool = rows
        if near:
            radii[m.id] = NEAR_RADIUS_M
            pool = [r for r in rows if r.distance_meters <= NEAR_RADIUS_M]
            if not pool:
                radii[m.id] = WIDE_RADIUS_M
                pool = [r for r in rows if r.distance_meters <= WIDE_RADIUS_M]
        if not pool:
            deferred[m.id] = ["far" if rows else "no_clinic"]
            continue
        pools[m.id] = pool

    def spent(rows: Sequence[PriceRow]) -> int:
        if budget is None:
            return 0
        if budget.currency == "JPY":
            return sum(r.total_jpy for r in rows)
        return sum(r.total_krw for r in rows)

    # 시술 단위 거르기. 바늘은 일정에서 뺀다. 회복 기간·예산은 거주자면 나중으로 미룬다.
    allowed = trip_days - 1 if trip_days is not None else days_until_event
    eligible: list[str] = []
    for m in menus:
        if m.id in deferred:
            continue
        reasons: list[PlanReason] = []
        if avoid_needle and m.uses_needle:
            reasons.append("needle")
        else:
            if allowed is not None and m.downtime_max_days > allowed:
                reasons.append("downtime")
            if budget is not None and spent(pools[m.id][:1]) > budget.amount:
                reasons.append("budget")
        if reasons:
            deferred[m.id] = reasons
        else:
            eligible.append(m.id)

    def arrange(ids: Sequence[str]) -> list[PriceRow] | None:
        if one_clinic and len(ids) > 1:
            return _one_clinic([pools[i] for i in ids])
        return separate_picks([pools[i] for i in ids])

    # 이번에 받을 조합. 예산이 없으면 전부, 있으면 예산 안에서 절약액이 가장 큰 조합.
    # 한 병원이면 3개까지이고, 모두 파는 병원이 있어야 한다.
    chosen: list[str] = []
    rows: list[PriceRow] = []
    if eligible and budget is None and not one_clinic:
        chosen, rows = eligible, arrange(eligible) or []
    elif eligible:
        best_key: tuple[int, ...] | None = None
        top = min(len(eligible), ONE_CLINIC_MAX) if one_clinic else len(eligible)
        for size in range(1, top + 1):
            for subset in combinations(eligible, size):
                picked = arrange(subset)
                if picked is None:
                    continue
                cost = spent(picked)
                if budget is not None and cost > budget.amount:
                    continue
                savings = sum(r.savings_jpy for r in picked)
                # 예산이 있으면 절약액, 없으면(한 병원) 시술 수가 먼저다.
                key = (
                    (savings, size, -cost)
                    if budget is not None
                    else (size, savings, -cost)
                )
                if best_key is None or key > best_key:
                    best_key, chosen, rows = key, list(subset), picked
    # 고르지 못한 시술의 이유: 더하면 예산을 넘으면 budget, 한 병원에서 같이 받을 수 없으면 one_clinic.
    for menu_id in eligible:
        if menu_id in chosen:
            continue
        added = (
            arrange([*chosen, menu_id])
            if not one_clinic or len(chosen) < ONE_CLINIC_MAX
            else None
        )
        over = added is not None and budget is not None and spent(added) > budget.amount
        deferred[menu_id] = ["budget" if over or not one_clinic else "one_clinic"]
    row_of = {r.menu.id: r for r in rows}
    rows = [row_of[i] for i in chosen]
    # 거주자 거리 기준: 이번에 받을 시술에 쓴 반경 중 가장 넓은 것(모두 3km면 3km)
    radius = max((radii[i] for i in chosen), default=None) if near else None

    # 날짜·순서
    show_distance = location is not None
    if visitor == "traveler":
        assert trip_days is not None
        if one_clinic and rows:
            schedule = [(1, rows)]
        else:
            schedule = _schedule_traveler(rows, trip_days)
        visits = [_visit(v, day, show_distance) for day, v in schedule]
    else:
        visits = [_visit(v, None, show_distance) for v in _resident_visits(rows)]

    # 같은 시술을 다른 방식으로 받을 때
    alternative: PlanAlternative | None = None
    total_krw = sum(r.total_krw for r in rows)
    total_jpy = sum(r.total_jpy for r in rows)
    if len(rows) >= 2:
        other = (
            separate_picks([pools[i] for i in chosen])
            if one_clinic
            else (_one_clinic([pools[i] for i in chosen]) if len(rows) <= 3 else None)
        )
        places = {r.place.id for r in other} if other else set()
        # 다른 방식도 지금 일정과 같은 병원 한 곳이면 비교할 것이 없다.
        if other and places != {v.place_id for v in visits}:
            alt_krw = sum(r.total_krw for r in other)
            alt_jpy = sum(r.total_jpy for r in other)
            single = len(places) == 1
            alternative = PlanAlternative(
                one_clinic=not one_clinic,
                clinic_count=len(places),
                place_id=other[0].place.id if single else None,
                clinic_name_ja=other[0].place.name_ja if single else None,
                clinic_name_ko=other[0].place.name_ko if single else None,
                total_krw=alt_krw,
                total_jpy=alt_jpy,
                extra_krw=alt_krw - total_krw,
                extra_jpy=alt_jpy - total_jpy,
            )

    jp_total = sum(r.jp_benchmark_jpy for r in rows)
    savings_jpy, savings_pct = savings_against(jp_total, total_jpy)
    plan_budget = (
        PlanBudget(
            currency=budget.currency,
            amount=budget.amount,
            left=budget.amount - spent(rows),
        )
        if budget is not None
        else None
    )
    by_id = {m.id: m for m in menus}
    deferred_cards = [
        PlanDeferred(
            menu_id=m.id,
            menu_name_ja=m.name_ja,
            menu_name_ko=m.name_ko,
            downtime_max_days=m.downtime_max_days,
            reasons=deferred[m.id],
            cheapest=_option(pools[m.id][0], show_distance) if m.id in pools else None,
        )
        for m in menus
        if m.id in deferred
    ]
    plan = TripPlanCardData(
        visitor=visitor,
        trip_days=trip_days,
        days_until_event=days_until_event,
        one_clinic=one_clinic,
        radius_meters=radius,
        visits=visits,
        total_krw=total_krw,
        total_jpy=total_jpy,
        jp_total_jpy=jp_total,
        savings_jpy=savings_jpy,
        savings_pct=savings_pct,
        budget=plan_budget,
        alternative=alternative,
        deferred=deferred_cards,
        krw_per_100_jpy=fx.krw_per_100_jpy,
        fx_date_label=fx.date_label,
    )
    text = _summary(plan, by_id, location, fx, pools)
    artifact = PlanTripArtifact(
        origin_id=location.id if location else None, plan=plan
    ).model_dump()
    return text, artifact


def _summary(
    plan: TripPlanCardData,
    menus: dict[str, Menu],
    location: Location | None,
    fx: Fx,
    pools: dict[str, list[PriceRow]],
) -> str:
    traveler = plan.visitor == "traveler"
    if traveler:
        head = (
            f"plan_trip: traveler, {plan.trip_days} day(s): day 1 = first day they can visit a "
            f"clinic, day {plan.trip_days} = return day. A treatment fits when its visible downtime "
            "is at most the days from the treatment day to the return day."
        )
    else:
        event = (
            f"next event in {plan.days_until_event} day(s)"
            if plan.days_until_event is not None
            else "no event given (no downtime limit)"
        )
        head = f"plan_trip: resident, {event}."
    if plan.one_clinic:
        head += " All planned treatments at one clinic."
    lines = [head]
    if location is not None:
        where = f"Distances from {location.name_ko} / {location.name_ja}"
        if plan.radius_meters:
            where += f"; only clinics within {plan.radius_meters // 1000} km"
        lines.append(where + ".")
    elif not traveler:
        lines.append("Home area not given: clinics anywhere in Seoul.")
    lines.append(f"FX: ₩{fx.krw_per_100_jpy} per ¥100 ({fx.bank_ko}, {fx.date_label}).")
    if plan.budget is not None:
        symbol = "¥" if plan.budget.currency == "JPY" else "₩"
        lines.append(
            f"Budget {symbol}{plan.budget.amount:,} for the whole plan"
            + (" (this month)" if not traveler else "")
            + f"; left after the plan {symbol}{plan.budget.left:,}."
        )

    if plan.visits:
        lines.append(
            "Plan, by day:"
            if traveler
            else "Get now (before the event), in this order:"
        )
        for v in plan.visits:
            items = "; ".join(
                f"{i.menu_name_ko} / {i.menu_name_ja} {_price(i.total_krw, i.total_jpy)} "
                + (
                    f"(downtime up to {i.downtime_max_days} days)"
                    if i.downtime_max_days
                    else "(no downtime)"
                )
                for i in v.items
            )
            label = f"Day {v.day}" if traveler else "-"
            if traveler and v.day == plan.trip_days:
                label += " (return day: go before the flight)"
            line = (
                f"{label}: clinic name in Korean: {v.clinic_name_ko}, in Japanese: "
                f"{v.clinic_name_ja} ({v.area}) | {items}"
            )
            if len(v.items) > 1:
                line += f" | visit total {_price(v.total_krw, v.total_jpy)}"
            if v.distance_meters is not None:
                line += f" | {v.distance_meters:,} m (walk ~{v.walk_minutes} min)"
            line += f" | Japanese support: {'yes' if v.japanese_support else 'no'}"
            tags = sorted({t for i in v.items for t in i.price_tags})
            if tags:
                line += f" | price tags: {', '.join(tags)}"
            if v.price_condition_ko:
                line += f" | price condition: {v.price_condition_ko} / {v.price_condition_ja}"
            line += f" | card_id={v.card_id}"
            lines.append(line)
        if traveler:
            for day in sorted({v.day for v in plan.visits if v.day is not None}):
                same = [v for v in plan.visits if v.day == day]
                if len(same) > 1:
                    lines.append(
                        f"Day {day} total ({len(same)} clinics): "
                        + _price(
                            sum(v.total_krw for v in same),
                            sum(v.total_jpy for v in same),
                        )
                        + "."
                    )
        vs_japan = (
            f"saves ¥{plan.savings_jpy:,} ({plan.savings_pct}%)"
            if plan.savings_jpy >= 0
            else f"costs ¥{-plan.savings_jpy:,} ({-plan.savings_pct}%) more"
        )
        lines.append(
            f"Plan total {_price(plan.total_krw, plan.total_jpy)}; {vs_japan} vs Japan "
            f"average total ¥{plan.jp_total_jpy:,}."
        )
        alt = plan.alternative
        if alt is not None:
            if alt.one_clinic:
                way = (
                    f"All at one clinic (clinic name in Korean: {alt.clinic_name_ko}, in "
                    f"Japanese: {alt.clinic_name_ja})"
                )
            else:
                way = f"Each treatment at its cheapest clinic ({alt.clinic_count} clinic(s))"
            diff = (
                f"₩{alt.extra_krw:,} (¥{alt.extra_jpy:,}) more"
                if alt.extra_krw > 0
                else f"₩{-alt.extra_krw:,} (¥{-alt.extra_jpy:,}) less"
                if alt.extra_krw < 0
                else "the same total"
            )
            lines.append(
                f"For comparison: {way}: {_price(alt.total_krw, alt.total_jpy)}, {diff} than this plan."
            )
    else:
        lines.append("Nothing can be planned with these conditions.")

    if plan.deferred:
        lines.append(
            "Not in this trip's plan:" if traveler else "Later or not planned:"
        )
        for d in plan.deferred:
            menu = menus[d.menu_id]
            why = "; ".join(REASON_TEXT[plan.visitor][r] for r in d.reasons)
            if "downtime" in d.reasons:
                why += f" (downtime up to {menu.downtime_max_days} days"
                why += (
                    f", only {max(plan.trip_days - 1, 0)} day(s) from day 1 to the return day)"
                    if traveler and plan.trip_days is not None
                    else f"; the event is in {plan.days_until_event} day(s); get it when "
                    f"there is no event for {menu.downtime_max_days} days)"
                )
            line = f"- {_name(menu)}: {why}"
            if d.menu_id in pools:
                row = pools[d.menu_id][0]
                line += f". Cheapest option: {_clinic(row)} {_price(row.total_krw, row.total_jpy)}"
            lines.append(line + ".")
    # 답 뒤에 덧붙일 질문은 하나다(설계 4.2). 결과를 가장 많이 바꾸는 것부터.
    if not traveler and location is None:
        lines.append(
            "After the answer, ask only this one question: where they live (the plan would "
            "keep clinics near home)."
        )
    elif not traveler and plan.days_until_event is None:
        lines.append(
            "After the answer, ask only this one question: when their next event that needs "
            "a clean face is."
        )
    lines.append(
        "The plan is shown as a card. Explain it day by day (or in order), then why each "
        "treatment is not planned now. Copy amounts as written; do not add them up. Opening hours "
        "and weekday-only prices are not in the data: tell the user to check opening hours when "
        "booking. For booking, pass a visit's card_id to get_booking_options."
    )
    return "\n".join(lines)
