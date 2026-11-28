"""search_bundle 도구: 한 병원 묶음 합계 최저가와 따로 받을 때와의 비교(#43, 설계 문서 FR-03)."""

import sys
from itertools import combinations

import pytest
from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards, read_reply
from api.agent.schemas import BookingCard, BundleCard, BundleCardData, LocationCard
from api.agent.tools import TOOLS
from api.agent.tools.get_booking_options import get_booking_options
from api.agent.tools.search_bundle import search_bundle
from api.domain.catalog import get_catalog
from api.domain.locations import get_locations

# 패키지가 도구 함수를 같은 이름으로 내보내므로 모듈은 sys.modules에서 꺼낸다.
bundle_module = sys.modules["api.agent.tools.search_bundle"]


def call(**args) -> ToolMessage:
    return search_bundle.invoke(
        {"type": "tool_call", "name": "search_bundle", "id": "c1", "args": args}
    )


def cards(message: ToolMessage) -> list[BundleCardData]:
    return [BundleCardData.model_validate(c) for c in message.artifact["cards"]]


def expected_bundles(menu_ids: list[str]) -> list[tuple[str, int, int]]:
    """web 데이터(스냅샷)로 직접 계산한 묶음: (병원 ID, 원 합계, 엔 합계), 엔 합계가 싼 순.

    도구와 같은 환율을 쓰도록 도구 결과의 환율로 다시 계산한다.
    """
    catalog = get_catalog()
    rate = cards(call(menu_ids=menu_ids))[0].krw_per_100_jpy
    vat = catalog.meta.vat_rate
    out = []
    for place in catalog.places:
        if not all(m in place.prices for m in menu_ids):
            continue
        krw = jpy = 0
        for m in menu_ids:
            menu = catalog.menu(m)
            offer = place.prices[m]
            total = offer.krw + (
                int(offer.krw * vat + 0.5)
                if place.vat_excluded and menu.vat_applies_in_korea
                else 0
            )
            krw += total
            jpy += int(total * 100 / rate + 0.5)
        out.append((place.id, krw, jpy))
    return sorted(out, key=lambda x: x[2])


@pytest.mark.parametrize(
    "menu_ids",
    [
        ["shrink-300", "aquapeel"],
        ["rejuran-2cc", "jaw-botox"],
        ["shrink-300", "aquapeel", "lalapeel"],
    ],
)
def test_cheapest_single_clinic_totals_match_direct_calculation(
    menu_ids: list[str],
) -> None:
    result = cards(call(menu_ids=menu_ids))
    expected = expected_bundles(menu_ids)

    assert len(result) == 3
    # 합계가 가장 싼 값이 같다(동률 병원은 순서가 바뀔 수 있어 합계로 비교한다).
    assert [c.total_jpy for c in result] == [e[2] for e in expected[:3]]
    by_id = {e[0]: e for e in expected}
    for card in result:
        _, krw, jpy = by_id[card.place_id]
        assert (card.total_krw, card.total_jpy) == (krw, jpy)
        assert card.total_krw == sum(i.total_krw for i in card.items)
        assert [i.menu_id for i in card.items] == menu_ids
        assert card.card_id == f"{card.place_id}:{'+'.join(menu_ids)}"
        jp_total = sum(get_catalog().menu(m).jp_benchmark_jpy for m in menu_ids)
        assert card.jp_total_jpy == jp_total
        assert card.savings_jpy == jp_total - card.total_jpy


def test_separate_total_compares_with_bundle() -> None:
    menu_ids = ["shrink-300", "aquapeel"]
    result = cards(call(menu_ids=menu_ids))
    first = result[0]
    assert first.separate_total_krw <= first.total_krw
    assert first.extra_vs_separate_krw == first.total_krw - first.separate_total_krw
    assert first.extra_vs_separate_jpy == first.total_jpy - first.separate_total_jpy
    assert all(c.separate_total_krw == first.separate_total_krw for c in result)
    assert 1 <= first.separate_clinic_count <= len(menu_ids)
    # 따로 받을 때 합계는 묶음 최저가보다 크지 않다(한 병원 묶음도 후보 중 하나이므로).
    assert first.separate_total_jpy <= min(c.total_jpy for c in result)


def test_separate_total_is_sum_of_search_clinics_top_rows() -> None:
    from api.domain.calc import rank_rows

    menu_ids = ["rejuran-2cc", "jaw-botox"]
    rate = cards(call(menu_ids=menu_ids))[0].krw_per_100_jpy
    tops = [rank_rows(get_catalog(), m, krw_per_100_jpy=rate)[0] for m in menu_ids]
    card = cards(call(menu_ids=menu_ids))[0]
    assert card.separate_total_krw == sum(r.total_krw for r in tops)
    assert card.separate_total_jpy == sum(r.total_jpy for r in tops)
    assert card.separate_clinic_count == len({r.place.id for r in tops})


def test_budget_is_for_the_whole_bundle() -> None:
    menu_ids = ["shrink-300", "aquapeel"]
    expected = expected_bundles(menu_ids)
    limit_jpy = expected[1][2]  # 두 번째로 싼 합계까지만 들어가는 예산
    message = call(
        menu_ids=menu_ids, budget={"amount": limit_jpy, "currency": "JPY"}, limit=5
    )
    result = cards(message)
    assert result and all(c.total_jpy <= limit_jpy for c in result)
    assert "over budget" in message.content


def test_needle_or_downtime_treatment_blocks_the_bundle() -> None:
    needle = call(menu_ids=["rejuran-2cc", "aquapeel"], avoid_needle=True)
    assert cards(needle) == []
    assert "uses a needle" in needle.content

    downtime = call(menu_ids=["shrink-300", "potenza"], max_downtime_days=1)
    assert cards(downtime) == []
    assert "downtime" in downtime.content


def test_area_and_japanese_filters() -> None:
    result = cards(
        call(
            menu_ids=["shrink-300", "aquapeel"],
            area="gangnam",
            japanese_only=True,
            limit=5,
        )
    )
    assert result
    assert all(c.area == "gangnam" and c.japanese_support for c in result)


def test_distance_sort_needs_origin_and_shows_distance() -> None:
    with pytest.raises(ValueError, match="origin_id"):
        search_bundle.invoke(
            {"menu_ids": ["shrink-300", "aquapeel"], "sort": "distance"}
        )

    origin = "station:홍대입구"
    assert get_locations().get(origin) is not None
    result = cards(
        call(menu_ids=["shrink-300", "aquapeel"], sort="distance", origin_id=origin)
    )
    distances = [c.distance_meters for c in result]
    assert all(d is not None for d in distances)
    assert distances == sorted(distances)


def test_same_treatment_twice_is_rejected() -> None:
    with pytest.raises(ValueError):
        search_bundle.invoke({"menu_ids": ["shrink-300", "shrink-300"]})


def test_no_clinic_with_all_treatments(monkeypatch: pytest.MonkeyPatch) -> None:
    real = bundle_module.rank_rows

    def without_aquapeel(catalog, menu_id, **kwargs):
        return [] if menu_id == "aquapeel" else real(catalog, menu_id, **kwargs)

    monkeypatch.setattr(bundle_module, "rank_rows", without_aquapeel)
    message = call(menu_ids=["shrink-300", "aquapeel"])
    assert cards(message) == []
    assert "No clinic in the data lists all" in message.content


def run(graph, message: str, thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": thread_id}},
    )


def test_graph_returns_bundle_cards_with_reasons_and_keeps_totals() -> None:
    menu_ids = ["shrink-300", "aquapeel"]
    top = cards(call(menu_ids=menu_ids))[0]
    total = f"₩{top.total_krw:,}"
    graph = build_graph(
        fake_model(
            tool_call("search_bundle", {"menu_ids": menu_ids}),
            reply_message(
                f"한 병원에서 받으면 {total}이 가장 싸요.",
                {top.card_id: "두 시술 합계가 가장 싸요"},
            ),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    result = run(graph, "슈링크랑 아쿠아필 한 병원에서 제일 싼 곳")

    bundles = [c for c in read_cards(result) if isinstance(c, BundleCard)]
    assert [c.card_id for c in bundles][:1] == [top.card_id]
    assert bundles[0].reason == "두 시술 합계가 가장 싸요"
    # 묶음 합계는 도구 결과에 있으므로 금액 검증이 지우지 않는다.
    assert total in read_reply(result).answer


def test_bundle_booking_uses_every_treatment() -> None:
    menu_ids = ["shrink-300", "aquapeel"]
    top = cards(call(menu_ids=menu_ids))[0]
    graph = build_graph(
        fake_model(
            tool_call("search_bundle", {"menu_ids": menu_ids}, "c1"),
            reply_message("묶음 3곳이에요"),
            tool_call("get_booking_options", {"place_id": top.card_id}, "c2"),
            reply_message("LINE으로 문의하세요"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    run(graph, "슈링크랑 아쿠아필 같이")
    booking = read_cards(run(graph, "첫 번째 병원 예약 방법"))
    assert len(booking) == 1 and isinstance(booking[0], BookingCard)
    assert booking[0].place_id == top.place_id
    assert booking[0].menu_ids == menu_ids
    assert booking[0].order_total_krw == top.total_krw


def test_bundle_origin_makes_location_card() -> None:
    origin = "station:홍대입구"
    graph = build_graph(
        fake_model(
            tool_call(
                "search_bundle",
                {"menu_ids": ["shrink-300", "aquapeel"], "origin_id": origin},
            ),
            reply_message("가까운 곳이에요"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    found = read_cards(run(graph, "홍대 근처에서 슈링크랑 아쿠아필"))
    assert [c.location_id for c in found if isinstance(c, LocationCard)] == [origin]


def test_safety_stop_drops_bundle_cards() -> None:
    graph = build_graph(
        fake_model(
            tool_call("search_bundle", {"menu_ids": ["shrink-300", "aquapeel"]}),
            reply_message("지금은 권하지 않아요", safety_stop=True),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    found = read_cards(run(graph, "임신 중인데 슈링크랑 아쿠아필"))
    assert not any(isinstance(c, BundleCard) for c in found)


def test_every_pair_has_some_bundle() -> None:
    """데이터의 시술 두 개 조합은 모두 묶음 후보가 있다(데이터가 바뀌면 알 수 있게)."""
    ids = [m.id for m in get_catalog().menus]
    for pair in combinations(ids, 2):
        assert cards(call(menu_ids=list(pair), limit=1)), pair


def test_same_bundle_in_two_areas_is_a_comparison() -> None:
    """같은 묶음을 지역끼리 비교하면 두 검색의 카드가 모두 나간다(ADR 0009와 같은 규칙)."""
    menu_ids = ["shrink-300", "aquapeel"]
    graph = build_graph(
        fake_model(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_bundle",
                        "args": {"menu_ids": menu_ids, "area": "hongdae"},
                        "id": "c1",
                    },
                    {
                        "name": "search_bundle",
                        "args": {"menu_ids": menu_ids, "area": "gangnam"},
                        "id": "c2",
                    },
                ],
            ),
            reply_message("홍대와 강남 비교"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    found = [
        c
        for c in read_cards(run(graph, "홍대랑 강남 비교"))
        if isinstance(c, BundleCard)
    ]
    assert {c.area for c in found} == {"hongdae", "gangnam"}
    assert {c.search_id for c in found} == {"b1", "b2"}


def test_narrowed_same_bundle_keeps_only_last() -> None:
    menu_ids = ["shrink-300", "aquapeel"]
    graph = build_graph(
        fake_model(
            tool_call("search_bundle", {"menu_ids": menu_ids}, "c1"),
            tool_call("search_bundle", {"menu_ids": menu_ids, "area": "gangnam"}, "c2"),
            reply_message("강남만"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    found = [
        c
        for c in read_cards(run(graph, "강남으로 좁혀 줘"))
        if isinstance(c, BundleCard)
    ]
    assert found and all(c.area == "gangnam" and c.search_id == "b2" for c in found)


def test_one_clinic_cheapest_for_every_treatment_counts_as_one() -> None:
    """시술별 최저가가 동률이면 이미 고른 병원을 써서, 따로 받는 병원 수를 1로 센다."""
    message = call(menu_ids=["inmode-fx", "lalapeel"])
    first = cards(message)[0]
    assert first.separate_clinic_count == 1
    assert first.extra_vs_separate_krw == 0
    assert {p.place_id for p in first.separate_picks} == {first.place_id}
    assert "this is the cheapest bundle" in message.content


def test_other_bundles_compare_with_the_cheapest_bundle_when_one_clinic_wins() -> None:
    message = call(menu_ids=["jaw-botox", "re2o-5cc"])
    result = cards(message)
    assert result[0].separate_clinic_count == 1
    second = result[1]
    assert second.extra_vs_separate_krw == second.total_krw - result[0].total_krw
    assert "more than the cheapest bundle" in message.content
    assert "different clinics" not in message.content


def test_separate_picks_name_the_clinics_and_follow_area() -> None:
    message = call(menu_ids=["shrink-300", "aquapeel"], area="gangnam")
    card = cards(message)[0]
    picks = card.separate_picks
    assert [p.menu_id for p in picks] == ["shrink-300", "aquapeel"]
    assert all(get_catalog().place(p.place_id).area == "gangnam" for p in picks)
    assert card.separate_total_krw == sum(p.total_krw for p in picks)
    for p in picks:
        assert p.clinic_name_ko in message.content


def test_budget_in_won() -> None:
    menu_ids = ["shrink-300", "aquapeel"]
    limit = expected_bundles(menu_ids)[0][1]
    result = cards(
        call(menu_ids=menu_ids, budget={"amount": limit, "currency": "KRW"}, limit=5)
    )
    assert result and all(c.total_krw <= limit for c in result)


def test_budget_excludes_everything_gives_cheapest_over_budget() -> None:
    menu_ids = ["shrink-300", "aquapeel"]
    cheapest = expected_bundles(menu_ids)[0]
    message = call(menu_ids=menu_ids, budget={"amount": 1000, "currency": "JPY"})
    assert cards(message) == []
    assert "every bundle below" not in message.content
    assert (
        f"over budget: clinic name in Korean: {get_catalog().place(cheapest[0]).name_ko}"
        in (message.content)
    )
    assert f"₩{cheapest[1]:,}" in message.content


def test_booking_with_three_treatment_bundle_and_too_many() -> None:
    menu_ids = ["shrink-300", "aquapeel", "lalapeel"]
    top = cards(call(menu_ids=menu_ids))[0]
    booking = get_booking_options.invoke(
        {
            "type": "tool_call",
            "name": "get_booking_options",
            "id": "b",
            "args": {"place_id": top.card_id},
        }
    )
    # 예약 카드의 시술 순서는 데이터 순서다(get_booking_options 기존 동작).
    assert set(booking.artifact["booking"]["menu_ids"]) == set(menu_ids)
    assert booking.artifact["booking"]["order_total_krw"] == top.total_krw

    too_many = f"{top.place_id}:shrink-300+aquapeel+lalapeel+jaw-botox"
    with pytest.raises(ValueError, match="At most three"):
        get_booking_options.invoke({"place_id": too_many})


def test_new_clinic_bundle_line_says_rating_is_being_collected() -> None:
    # 신규 개원 지점은 평점 숫자 대신 개원일과 집계 중임을 쓴다(이슈 #101).
    message = search_bundle.invoke(
        {
            "type": "tool_call",
            "name": "search_bundle",
            "id": "c1",
            "args": {"menu_ids": ["rejuran-2cc", "shrink-300"], "limit": 5},
        }
    )
    lines = message.content.splitlines()
    shown = [
        BundleCardData.model_validate(c).place_id for c in message.artifact["cards"]
    ]
    assert "vands-balsan" in shown, "데이터가 바뀌어 신규 개원 지점이 상위 5곳에 없음"

    new = next(line for line in lines if "card_id=vands-balsan" in line)
    assert "new clinic opened 2026.11.25, rating still being collected" in new
    assert "rating 3.7" not in new
