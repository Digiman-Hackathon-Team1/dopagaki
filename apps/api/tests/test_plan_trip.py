"""plan_trip 도구: 여행자 체류 기간 배치와 거주자 지금·나중 계획(#45, 설계 문서 FR-11, ADR 0017)."""

from itertools import combinations

import pytest
from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import AMOUNT_CAUTION, build_graph, read_cards, read_reply
from api.agent.schemas import (
    BookingCard,
    LocationCard,
    TripPlanCard,
    TripPlanCardData,
)
from api.agent.tools import TOOLS
from api.agent.tools.plan_trip import plan_trip
from api.agent.tools.search_bundle import search_bundle
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
from api.domain.locations import get_locations
from api.main import app

ALL_MENUS = [m.id for m in get_catalog().menus]


def call(**args) -> ToolMessage:
    return plan_trip.invoke(
        {"type": "tool_call", "name": "plan_trip", "id": "c1", "args": args}
    )


def plan(**args) -> TripPlanCardData:
    return TripPlanCardData.model_validate(call(**args).artifact["plan"])


def planned(p: TripPlanCardData) -> list[str]:
    return [i.menu_id for v in p.visits for i in v.items]


def reasons(p: TripPlanCardData) -> dict[str, list[str]]:
    return {d.menu_id: list(d.reasons) for d in p.deferred}


def cheapest(menu_id: str, origin_id: str | None = None):
    """search_clinics 기본 정렬(절약액 순)의 1위와 같은 행."""
    location = get_locations().get(origin_id) if origin_id else None
    rate = plan(visitor="traveler", menu_ids=[menu_id], trip_days=30).krw_per_100_jpy
    return rank_rows(
        get_catalog(),
        menu_id,
        origin=location.point if location else None,
        krw_per_100_jpy=rate,
    )[0]


# 거주자 시나리오: 신촌·5만 원·내일 약속·고민 모공+윤기
RESIDENT = {
    "visitor": "resident",
    "concerns": ["pores", "glow"],
    "days_until_event": 1,
    "budget": {"amount": 50000, "currency": "KRW"},
    "origin_id": "area:sinchon",
}


def test_resident_matches_button_consult_now_and_later() -> None:
    """아쿠아필 근처 ₩3,850, 아쿠아필 + 라라필 ₩35,750, 포텐자는 나중에(회복 기간)."""
    p = plan(**RESIDENT)

    assert sorted(planned(p)) == ["aquapeel", "lalapeel"]
    items = {i.menu_id: i for v in p.visits for i in v.items}
    assert items["aquapeel"].total_krw == 3850
    assert p.total_krw == 35750
    assert p.budget is not None and p.budget.left == 50000 - 35750
    assert p.radius_meters == 3000
    assert all(
        v.distance_meters is not None and v.distance_meters <= 3000 for v in p.visits
    )
    assert all(v.day is None for v in p.visits)

    # 버튼식 상담의 "다음 추천"(nextPlan)은 미룬 시술 중 첫 번째다.
    assert p.deferred[0].menu_id == "potenza"
    assert "downtime" in p.deferred[0].reasons
    later = reasons(p)
    assert set(later) == {"potenza", "juvelook-2cc", "rejuran-2cc"}
    assert all("downtime" in r for r in later.values())
    # 포텐자는 근처 최저가(₩53,900)도 이번 달 예산(₩50,000)을 넘는다.
    assert p.deferred[0].cheapest is not None
    assert p.deferred[0].cheapest.total_krw == 53900
    assert later["potenza"] == ["downtime", "budget"]


def test_resident_summary_says_later_and_next_month() -> None:
    text = call(**RESIDENT).content
    assert "after the event" in text
    assert "next month" in text
    assert "₩35,750" in text and "₩14,250" in text


def test_resident_budget_only_goes_to_next_month() -> None:
    p = plan(
        **{
            **RESIDENT,
            "days_until_event": None,
            "concerns": None,
            "menu_ids": ["aquapeel", "rejuran-2cc"],
        }
    )
    assert planned(p) == ["aquapeel"]
    assert reasons(p) == {"rejuran-2cc": ["budget"]}


def test_resident_without_event_has_no_downtime_limit() -> None:
    p = plan(
        visitor="resident", menu_ids=["potenza", "aquapeel"], origin_id="area:sinchon"
    )
    assert sorted(planned(p)) == ["aquapeel", "potenza"]
    # 회복이 긴 방문부터 받는다.
    assert p.visits[0].items[0].menu_id == "potenza"
    assert p.deferred == []


def test_resident_widens_to_6km_and_marks_far() -> None:
    """3km 안에 없으면 6km, 그래도 없으면 far(가까운 병원 없음)."""
    catalog = get_catalog()
    for location_id in get_locations().locations:
        origin = get_locations().get(location_id)
        assert origin is not None
        for m in catalog.menus:
            rows = rank_rows(catalog, m.id, origin=origin.point)
            if any(r.distance_meters <= 3000 for r in rows):
                continue
            p = plan(visitor="resident", menu_ids=[m.id], origin_id=location_id)
            if any(r.distance_meters <= 6000 for r in rows):
                assert p.radius_meters == 6000
                assert planned(p) == [m.id]
                assert p.visits[0].distance_meters <= 6000
            else:
                assert reasons(p) == {m.id: ["far"]}
                assert p.deferred[0].cheapest is None
            return
    pytest.skip("모든 위치에서 모든 시술이 3km 안에 있다")


def test_traveler_two_nights_drops_long_downtime_and_places_by_day() -> None:
    """2박 3일(모레 귀국): 회복이 긴 시술은 빠지고 남은 시술이 날짜별로 배치된다."""
    p = plan(
        visitor="traveler",
        menu_ids=["rejuran-2cc", "juvelook-2cc", "shrink-300", "aquapeel", "inmode-fx"],
        trip_days=3,
    )

    assert reasons(p) == {"rejuran-2cc": ["downtime"], "inmode-fx": ["downtime"]}
    days = {i.menu_id: v.day for v in p.visits for i in v.items}
    assert days == {"juvelook-2cc": 1, "shrink-300": 2, "aquapeel": 3}
    for v in p.visits:
        assert v.day is not None and v.day + v.downtime_max_days <= 3
    # 금액은 search_clinics 1위와 같다.
    for v in p.visits:
        for item in v.items:
            top = cheapest(item.menu_id)
            assert (v.place_id, item.total_krw, item.total_jpy) == (
                top.place.id,
                top.total_krw,
                top.total_jpy,
            )
    assert p.total_krw == 132550
    # 비교: 한 병원에서 같이 받으면 search_bundle 1위와 같다.
    bundle = search_bundle.invoke(
        {"menu_ids": ["juvelook-2cc", "shrink-300", "aquapeel"]}
    )
    assert p.alternative is not None and p.alternative.one_clinic
    assert p.alternative.total_krw == 231000
    assert p.alternative.extra_krw == 231000 - 132550
    assert "₩231,000" in bundle


@pytest.mark.parametrize("trip_days", [1, 2, 3, 4, 5, 6])
def test_traveler_never_places_unrecovered_treatment(trip_days: int) -> None:
    p = plan(visitor="traveler", menu_ids=ALL_MENUS[:5], trip_days=trip_days)
    q = plan(visitor="traveler", menu_ids=ALL_MENUS[5:], trip_days=trip_days)
    for result, count in ((p, 5), (q, 4)):
        for v in result.visits:
            assert v.day is not None and 1 <= v.day <= trip_days
            assert v.day + v.downtime_max_days <= trip_days
        for d in result.deferred:
            assert d.reasons == ["downtime"]
            assert d.downtime_max_days > trip_days - 1
        assert len(planned(result)) + len(result.deferred) == count


def test_traveler_one_visit_per_day_while_days_last() -> None:
    p = plan(
        visitor="traveler", menu_ids=["shrink-300", "aquapeel", "lalapeel"], trip_days=2
    )
    days = [v.day for v in p.visits]
    assert days == sorted(days)
    assert days[:2] == [1, 2]
    assert all(d <= 2 for d in days)


def test_traveler_one_clinic_uses_cheapest_bundle_on_day_one() -> None:
    menu_ids = ["juvelook-2cc", "shrink-300", "aquapeel"]
    p = plan(
        visitor="traveler",
        menu_ids=[*menu_ids, "rejuran-2cc"],
        trip_days=3,
        one_clinic=True,
    )
    bundle = search_bundle.invoke(
        {
            "type": "tool_call",
            "name": "search_bundle",
            "id": "b",
            "args": {"menu_ids": menu_ids},
        }
    ).artifact["cards"][0]

    assert len(p.visits) == 1 and p.visits[0].day == 1
    assert p.visits[0].place_id == bundle["place_id"]
    assert p.visits[0].total_krw == bundle["total_krw"]
    assert p.visits[0].card_id == bundle["card_id"]
    assert reasons(p) == {"rejuran-2cc": ["downtime"]}
    # 비교: 시술마다 가장 싼 병원에 따로
    assert p.alternative is not None and not p.alternative.one_clinic
    assert p.alternative.total_krw == bundle["separate_total_krw"]
    assert p.alternative.clinic_count == bundle["separate_clinic_count"]


def test_one_clinic_takes_at_most_three() -> None:
    p = plan(
        visitor="traveler",
        menu_ids=["shrink-300", "aquapeel", "lalapeel", "jaw-botox"],
        trip_days=5,
        one_clinic=True,
    )
    assert len(planned(p)) == 3
    assert len({v.place_id for v in p.visits}) == 1
    assert [r for r in reasons(p).values()] == [["one_clinic"]]


@pytest.mark.parametrize("amount", [500, 3000, 7000, 15000, 30000])
def test_traveler_budget_picks_biggest_savings_within_budget(amount: int) -> None:
    menu_ids = ["shrink-300", "aquapeel", "lalapeel", "juvelook-2cc"]
    p = plan(
        visitor="traveler",
        menu_ids=menu_ids,
        trip_days=3,
        budget={"amount": amount, "currency": "JPY"},
    )
    assert p.total_jpy <= amount
    assert p.budget is not None and p.budget.left == amount - p.total_jpy
    # 직접 계산: 예산 안에서 절약액이 가장 큰 조합
    rows = {m: cheapest(m) for m in menu_ids}
    best = max(
        (
            sum(rows[m].savings_jpy for m in subset)
            for size in range(len(menu_ids) + 1)
            for subset in combinations(menu_ids, size)
            if sum(rows[m].total_jpy for m in subset) <= amount
        ),
    )
    assert p.savings_jpy == best
    for d in p.deferred:
        assert d.reasons == ["budget"]


def test_needle_is_left_out_not_deferred() -> None:
    p = plan(
        **{
            **RESIDENT,
            "concerns": None,
            "menu_ids": ["jaw-botox", "aquapeel"],
            "avoid_needle": True,
        }
    )
    assert planned(p) == ["aquapeel"]
    assert reasons(p) == {"jaw-botox": ["needle"]}


def test_concerns_use_concern_table() -> None:
    """고민 처짐 → 고민별 시술표의 슈링크·인모드. 회복이 긴 인모드(최대 5일)가 1일째다."""
    p = plan(visitor="traveler", concerns=["lift"], trip_days=10)
    assert planned(p) == ["inmode-fx", "shrink-300"]
    assert [v.day for v in p.visits] == [1, 2]
    assert p.deferred == []


@pytest.mark.parametrize(
    "args",
    [
        {"visitor": "traveler", "menu_ids": ["aquapeel"]},
        {
            "visitor": "traveler",
            "menu_ids": ["aquapeel"],
            "concerns": ["glow"],
            "trip_days": 3,
        },
        {"visitor": "traveler", "trip_days": 3},
        {"visitor": "resident", "menu_ids": ["aquapeel"], "trip_days": 3},
        {
            "visitor": "traveler",
            "menu_ids": ["aquapeel"],
            "trip_days": 3,
            "days_until_event": 1,
        },
        {"visitor": "traveler", "menu_ids": ["aquapeel", "aquapeel"], "trip_days": 3},
    ],
)
def test_invalid_inputs_are_rejected(args: dict) -> None:
    with pytest.raises(ValueError):
        plan_trip.invoke(args)


def test_unknown_origin_is_an_error() -> None:
    with pytest.raises(ValueError, match="Unknown origin_id"):
        plan_trip.invoke(
            {
                "visitor": "resident",
                "menu_ids": ["aquapeel"],
                "origin_id": "area:nowhere",
            }
        )


def test_summary_mentions_return_day_and_opening_hours() -> None:
    text = call(
        visitor="traveler", menu_ids=["shrink-300", "aquapeel"], trip_days=2
    ).content
    assert "Day 2 (return day" in text
    assert "Opening hours" in text


def run(graph, message: str, thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": thread_id}},
    )


def test_graph_returns_plan_card_and_keeps_plan_amounts() -> None:
    graph = build_graph(
        fake_model(
            tool_call("plan_trip", RESIDENT),
            reply_message(
                "지금은 아쿠아필 ₩3,850과 라라필 ₩31,900, 합계 ₩35,750이에요. 남은 예산은 ₩14,250이에요."
            ),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    result = run(graph, "신촌 살아요. 5만 원, 내일 약속 있어요. 모공이랑 윤기")

    found = read_cards(result)
    plans = [c for c in found if isinstance(c, TripPlanCard)]
    assert len(plans) == 1 and plans[0].total_krw == 35750
    assert [c.location_id for c in found if isinstance(c, LocationCard)] == [
        "area:sinchon"
    ]
    reply = read_reply(result)
    assert "₩35,750" in reply.answer and "₩14,250" in reply.answer
    assert AMOUNT_CAUTION["ko"] not in reply.cautions


def test_graph_keeps_only_last_plan_of_the_turn() -> None:
    graph = build_graph(
        fake_model(
            tool_call(
                "plan_trip",
                {
                    "visitor": "traveler",
                    "menu_ids": ["shrink-300", "aquapeel"],
                    "trip_days": 3,
                },
                "c1",
            ),
            tool_call(
                "plan_trip",
                {
                    "visitor": "traveler",
                    "menu_ids": ["shrink-300", "aquapeel"],
                    "trip_days": 3,
                    "one_clinic": True,
                },
                "c2",
            ),
            reply_message("한 병원 일정이에요"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    plans = [
        c for c in read_cards(run(graph, "2박 3일")) if isinstance(c, TripPlanCard)
    ]
    assert len(plans) == 1 and plans[0].one_clinic


def test_plan_visit_card_id_books_every_treatment() -> None:
    args = {
        "visitor": "traveler",
        "menu_ids": ["shrink-300", "aquapeel"],
        "trip_days": 3,
        "one_clinic": True,
    }
    visit = plan(**args).visits[0]
    graph = build_graph(
        fake_model(
            tool_call("plan_trip", args, "c1"),
            reply_message("일정이에요"),
            tool_call("get_booking_options", {"place_id": visit.card_id}, "c2"),
            reply_message("LINE으로 문의하세요"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    run(graph, "2박 3일, 슈링크랑 아쿠아필 한 병원에서")
    booking = read_cards(run(graph, "1일째 병원 예약 방법"))
    assert len(booking) == 1 and isinstance(booking[0], BookingCard)
    assert booking[0].place_id == visit.place_id
    assert sorted(booking[0].menu_ids) == ["aquapeel", "shrink-300"]
    assert booking[0].order_total_krw == visit.total_krw


def test_safety_stop_drops_plan_card() -> None:
    graph = build_graph(
        fake_model(
            tool_call("plan_trip", RESIDENT),
            reply_message("지금은 권하지 않아요", safety_stop=True),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    found = read_cards(run(graph, "임신 중인데 일정 짜 줘"))
    assert not any(isinstance(c, TripPlanCard) for c in found)


def test_openapi_exposes_trip_plan_card() -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert "TripPlanCard" in schemas
    assert schemas["TripPlanCard"]["properties"]["kind"]["const"] == "trip_plan"
    assert {"visits", "deferred", "alternative"} <= set(
        schemas["TripPlanCard"]["properties"]
    )


def test_traveler_fills_the_next_free_day_after_shared_days() -> None:
    """리뷰 반영: 회복이 긴 두 방문이 1일째를 함께 쓰면 다음 방문은 비어 있는 2일째로 간다."""
    p = plan(
        visitor="traveler",
        menu_ids=["rejuran-2cc", "re2o-5cc", "shrink-300", "aquapeel"],
        trip_days=4,
    )
    days = {i.menu_id: v.day for v in p.visits for i in v.items}
    assert days == {"rejuran-2cc": 1, "re2o-5cc": 1, "shrink-300": 2, "aquapeel": 3}
    text = call(
        visitor="traveler",
        menu_ids=["rejuran-2cc", "re2o-5cc", "shrink-300", "aquapeel"],
        trip_days=4,
    ).content
    assert "Day 1 total (2 clinics)" in text


@pytest.mark.parametrize(
    "args",
    [
        {
            "visitor": "resident",
            "menu_ids": ["rejuran-2cc", "shrink-300", "potenza", "jaw-botox"],
            "origin_id": "area:sinchon",
        },
        {"visitor": "resident", "menu_ids": ALL_MENUS[:5], "origin_id": "area:hongdae"},
        {"visitor": "traveler", "menu_ids": ALL_MENUS[:5], "trip_days": 10},
    ],
)
def test_every_visit_card_id_can_be_booked(args: dict) -> None:
    """리뷰 반영: 한 방문은 3개까지라 방문 card_id를 예약 도구에 그대로 넣을 수 있다."""
    from api.agent.tools.get_booking_options import get_booking_options

    p = plan(**args)
    for visit in p.visits:
        assert len(visit.items) <= 3
        booking = get_booking_options.invoke(
            {
                "type": "tool_call",
                "name": "get_booking_options",
                "id": "b",
                "args": {"place_id": visit.card_id},
            }
        ).artifact["booking"]
        assert sorted(booking["menu_ids"]) == sorted(i.menu_id for i in visit.items)
        assert booking["order_total_krw"] == visit.total_krw


def test_radius_counts_only_planned_treatments() -> None:
    """3km 안에 없어 6km로 넓힌 시술이 일정에서 빠지면 카드의 반경은 3km다."""
    catalog = get_catalog()
    for location_id in get_locations().locations:
        origin = get_locations().get(location_id)
        assert origin is not None
        wide = [
            m.id
            for m in catalog.menus
            if m.uses_needle
            and not any(
                r.distance_meters <= 3000
                for r in rank_rows(catalog, m.id, origin=origin.point)
            )
            and any(
                r.distance_meters <= 6000
                for r in rank_rows(catalog, m.id, origin=origin.point)
            )
        ]
        if not wide:
            continue
        p = plan(
            visitor="resident",
            menu_ids=[wide[0], "aquapeel"],
            origin_id=location_id,
            avoid_needle=True,
        )
        if planned(p) == ["aquapeel"] and p.visits[0].distance_meters <= 3000:
            assert p.radius_meters == 3000
            return
    pytest.skip("6km로 넓히는 바늘 시술이 있는 위치가 없다")


def test_resident_asks_one_question_home_first_then_event() -> None:
    no_home = call(visitor="resident", menu_ids=["aquapeel"]).content
    assert "ask only this one question: where they live" in no_home
    no_event = call(
        visitor="resident", menu_ids=["aquapeel"], origin_id="area:sinchon"
    ).content
    assert "ask only this one question: when their next event" in no_event
    both = call(
        visitor="resident",
        menu_ids=["aquapeel"],
        origin_id="area:sinchon",
        days_until_event=2,
    ).content
    assert "ask only this one question" not in both


def test_resident_event_today_allows_only_no_downtime() -> None:
    p = plan(
        visitor="resident",
        menu_ids=["juvelook-2cc", "aquapeel"],
        origin_id="area:sinchon",
        days_until_event=0,
    )
    assert planned(p) == ["aquapeel"]
    assert reasons(p) == {"juvelook-2cc": ["downtime"]}
