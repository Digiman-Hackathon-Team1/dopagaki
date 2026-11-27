"""예약 연결 흐름(설계 문서 11.2): 병원 검색 → 다음 턴 "첫 번째 병원 예약 방법" → 예약 카드."""

from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards
from api.agent.schemas import BookingCard, RecommendationCard
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog


def run(graph, message: str, thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ja"},
        {"configurable": {"thread_id": thread_id}},
    )


def test_second_turn_booking_returns_only_booking_card() -> None:
    first = rank_rows(get_catalog(), "shrink-300")[0].place
    graph = build_graph(
        fake_model(
            tool_call("search_clinics", {"menu_id": "shrink-300"}, "c1"),
            reply_message("상위 3곳이에요"),
            tool_call(
                "get_booking_options",
                {"place_id": first.id, "menu_ids": ["shrink-300"]},
                "c2",
            ),
            reply_message("LINE으로 문의하세요"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )

    clinics = read_cards(run(graph, "슈링크 싼 곳"))
    assert clinics and all(isinstance(c, RecommendationCard) for c in clinics)
    # 병원 카드에도 예약 채널, 후기 평점, 가격표 증빙이 붙는다(설계 문서 4.7).
    assert [(c.kind, c.url_ja) for c in clinics[0].booking_channels] == [
        (c.kind, c.url_ja) for c in first.channels
    ]
    assert clinics[0].rating == first.ai_rating
    assert clinics[0].proof.url_ko == first.proofs["shrink-300"].url_ko

    cards = read_cards(run(graph, "첫 번째 병원 예약 방법 알려 줘"))

    # 앞 턴의 병원 카드는 다시 나오지 않고, 예약 카드 한 장만 나온다.
    assert len(cards) == 1
    booking = cards[0]
    assert isinstance(booking, BookingCard)
    assert booking.place_id == first.id
    assert [(c.kind, c.url_ja) for c in booking.channels] == [
        (c.kind, c.url_ja) for c in first.channels
    ]


def test_same_clinic_twice_keeps_last_and_other_clinics_stay() -> None:
    places = [r.place.id for r in rank_rows(get_catalog(), "shrink-300")[:2]]
    graph = build_graph(
        fake_model(
            tool_call("get_booking_options", {"place_id": places[0]}, "c1"),
            tool_call("get_booking_options", {"place_id": places[1]}, "c2"),
            tool_call(
                "get_booking_options",
                {"place_id": places[0], "preferred_date": "11/28"},
                "c3",
            ),
            reply_message(),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )

    cards = read_cards(run(graph, "두 곳 예약 방법"))

    assert [c.place_id for c in cards] == places
    assert "11/28" in cards[0].inquiry_ko


def test_booking_card_comes_after_clinic_cards() -> None:
    first = rank_rows(get_catalog(), "rejuran-2cc")[0].place
    graph = build_graph(
        fake_model(
            tool_call("search_clinics", {"menu_id": "rejuran-2cc"}, "c1"),
            tool_call("get_booking_options", {"place_id": first.id}, "c2"),
            reply_message(),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )

    kinds = [c.kind for c in read_cards(run(graph, "리쥬란 제일 싼 곳 예약까지"))]

    assert kinds == ["recommendation"] * 3 + ["booking"]


def test_failed_booking_call_makes_no_card() -> None:
    graph = build_graph(
        fake_model(
            tool_call("get_booking_options", {"place_id": "no-such-clinic"}, "c1"),
            reply_message("어느 병원인지 알려 주세요"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )

    assert read_cards(run(graph, "예약 방법")) == []
