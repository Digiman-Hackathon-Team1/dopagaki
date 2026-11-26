"""시술 추천 흐름(설계 문서 9장, 11.1): recommend_treatments → resolve_location → search_clinics.

가짜 LLM이 정해진 도구 호출을 내고, 그래프가 위치 카드와 병원 카드를 조립하는지 본다.
"""

from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards, read_exclusions
from api.agent.schemas import LocationCard, RecommendationCard
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
from api.domain.locations import get_locations

HONGDAE = get_locations().get("area:hongdae")


def run(graph, message: str, thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": thread_id}},
    )


def graph_with(*responses):
    return build_graph(
        fake_model(*responses), tools=TOOLS, checkpointer=InMemorySaver()
    )


def test_sagging_two_nights_hongdae_flow() -> None:
    """이슈 #6 완료 기준의 흐름: 슈링크 추천, 인모드는 회복 기간으로 제외, 홍대 기준 거리."""
    graph = graph_with(
        tool_call(
            "recommend_treatments", {"concerns": ["lift"], "max_downtime_days": 2}, "c1"
        ),
        tool_call("resolve_location", {"query": "홍대 숙소"}, "c2"),
        tool_call(
            "search_clinics",
            {
                "menu_id": "shrink-300",
                "max_downtime_days": 2,
                "origin_id": "area:hongdae",
            },
            "c3",
        ),
        reply_message("슈링크를 추천해요"),
    )

    state = run(graph, "얼굴 처짐, 2박 3일, 홍대 숙소")

    tools = {m.name: m for m in state["messages"] if isinstance(m, ToolMessage)}
    assert "1. shrink-300" in tools["recommend_treatments"].content
    assert "inmode-fx" in tools["recommend_treatments"].content
    assert "more than the allowed 2 days" in tools["recommend_treatments"].content

    cards = read_cards(state)
    location, *clinics = cards
    assert isinstance(location, LocationCard)
    assert (location.location_id, location.lat, location.lng) == (
        "area:hongdae",
        HONGDAE.lat,
        HONGDAE.lng,
    )
    expected = rank_rows(get_catalog(), "shrink-300", origin=HONGDAE.point)[:3]
    assert all(isinstance(c, RecommendationCard) for c in clinics)
    assert [c.place_id for c in clinics] == [r.place.id for r in expected]
    assert [c.distance_meters for c in clinics] == [r.distance_meters for r in expected]
    assert read_exclusions(state) == []


def test_unknown_location_makes_no_card() -> None:
    graph = graph_with(
        tool_call("resolve_location", {"query": "이태원"}, "c1"),
        tool_call("search_clinics", {"menu_id": "shrink-300"}, "c2"),
        reply_message("이태원 근처 역을 알려 주세요"),
    )

    cards = read_cards(run(graph, "이태원 숙소, 슈링크"))

    assert all(c.kind == "recommendation" for c in cards)
    assert all(c.distance_meters is None for c in cards)


def test_same_location_twice_is_one_card_and_two_places_are_two() -> None:
    graph = graph_with(
        tool_call("resolve_location", {"query": "홍대 숙소"}, "c1"),
        tool_call("resolve_location", {"query": "홍대 근처"}, "c2"),
        tool_call("resolve_location", {"query": "강남역"}, "c3"),
        reply_message("두 곳"),
    )

    cards = read_cards(run(graph, "홍대랑 강남역 비교"))

    assert [(c.kind, c.location_id, c.query) for c in cards] == [
        ("location", "area:hongdae", "홍대 숙소"),
        ("location", "station:강남", "강남역"),
    ]


def test_location_card_is_only_for_this_turn() -> None:
    graph = graph_with(
        tool_call("resolve_location", {"query": "홍대 숙소"}, "c1"),
        reply_message("홍대 기준으로 볼게요"),
        reply_message("다른 질문의 답"),
    )

    run(graph, "홍대 숙소예요")
    second = run(graph, "고마워요")

    assert read_cards(second) == []


def test_previous_turn_location_is_shown_when_search_uses_it() -> None:
    """앞 턴에 찾은 위치로 이번 턴에 검색하면, 그 위치 카드를 앞 턴의 사용자 표현과 함께 낸다."""
    graph = graph_with(
        tool_call("resolve_location", {"query": "홍대 숙소"}, "c1"),
        reply_message("홍대 기준으로 볼게요"),
        tool_call(
            "search_clinics",
            {"menu_id": "shrink-300", "sort": "distance", "origin_id": "area:hongdae"},
            "c2",
        ),
        reply_message("가까운 순"),
    )

    run(graph, "홍대 숙소예요")
    cards = read_cards(run(graph, "슈링크 가까운 순으로 보여 줘"))

    assert (cards[0].kind, cards[0].location_id, cards[0].query) == (
        "location",
        "area:hongdae",
        "홍대 숙소",
    )
    assert all(c.distance_meters is not None for c in cards[1:])


def test_location_not_used_by_this_turn_search_is_not_shown() -> None:
    """위치를 찾았는데 검색에 쓰지 않았으면 위치 카드를 내지 않는다(거리 없는 병원 카드와 어긋나지 않게)."""
    graph = graph_with(
        tool_call("resolve_location", {"query": "홍대 숙소"}, "c1"),
        tool_call("search_clinics", {"menu_id": "shrink-300"}, "c2"),
        reply_message("슈링크"),
    )

    cards = read_cards(run(graph, "홍대 숙소, 슈링크"))

    assert cards and all(c.kind == "recommendation" for c in cards)


def test_search_with_two_origins_is_a_comparison() -> None:
    """ADR 0009: 기준 위치 ID가 다르면 비교로 보고 두 검색의 카드를 모두 낸다."""
    graph = graph_with(
        tool_call(
            "search_clinics",
            {"menu_id": "shrink-300", "origin_id": "area:hongdae"},
            "c1",
        ),
        tool_call(
            "search_clinics",
            {"menu_id": "shrink-300", "origin_id": "station:강남"},
            "c2",
        ),
        reply_message("비교"),
    )

    cards = read_cards(run(graph, "홍대와 강남역 기준 비교"))

    # 두 검색이 쓴 기준 위치가 카드로 나간다. resolve_location을 거치지 않았으므로 query는 null이다.
    assert [(c.kind, c.location_id, c.query) for c in cards[:2]] == [
        ("location", "area:hongdae", None),
        ("location", "station:강남", None),
    ]
    assert {c.search_id for c in cards[2:]} == {"s1", "s2"}
