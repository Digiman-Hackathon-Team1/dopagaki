"""일본 시술명 흐름(설계 문서 9장, 11.2): match_jp_treatment → search_clinics.

가짜 LLM이 정해진 도구 호출을 내고, 그래프가 대응 카드와 병원 카드를 조립하는지 본다.
"""

from uuid import uuid4

from agent_fakes import fake_model, reply_message, tool_call
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards
from api.agent.router import get_agent
from api.agent.schemas import JpMatchCard, LocationCard, RecommendationCard
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
from api.main import app


def run(graph, message: str, thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": thread_id}},
    )


def graph_with(*responses):
    return build_graph(
        fake_model(*responses), tools=TOOLS, checkpointer=InMemorySaver()
    )


def test_hifu_question_returns_match_card_then_clinic_cards() -> None:
    """이슈 #7 완료 기준의 흐름: 하이후 → 슈링크 대응과 이유 → 병원 카드."""
    graph = graph_with(
        tool_call("match_jp_treatment", {"query": "하이후"}, "c1"),
        tool_call("search_clinics", {"menu_id": "shrink-300"}, "c2"),
        reply_message("한국에서는 슈링크라고 불러요"),
    )

    match, *clinics = read_cards(run(graph, "하이후 하는 병원 있어?"))

    assert isinstance(match, JpMatchCard)
    assert (match.query, match.menu_id) == ("하이후", "shrink-300")
    assert "기기 이름(슈링크)" in match.reason_ko
    expected = rank_rows(get_catalog(), "shrink-300")[:3]
    assert all(isinstance(c, RecommendationCard) for c in clinics)
    assert [c.place_id for c in clinics] == [r.place.id for r in expected]
    assert all(c.jp_clinic is None for c in clinics)


def test_jp_clinic_flow_shows_clinic_price_and_direct_savings() -> None:
    graph = graph_with(
        tool_call("match_jp_treatment", {"query": "ハイフ", "jp_clinic": "品川"}, "c1"),
        tool_call(
            "search_clinics",
            {"menu_id": "shrink-300", "jp_clinic": "品川美容外科"},
            "c2",
        ),
        reply_message("品川美容外科より安いです"),
    )

    match, *clinics = read_cards(run(graph, "品川でハイフを受けていました"))

    assert match.jp_clinic is not None
    assert (match.jp_clinic.name_ja, match.jp_clinic.price_jpy) == (
        "品川美容外科",
        27500,
    )
    for card in clinics:
        assert card.jp_clinic.savings_jpy == 27500 - card.total_jpy


def test_ambiguous_match_makes_no_card() -> None:
    graph = graph_with(
        tool_call("match_jp_treatment", {"query": "ピーリング"}, "c1"),
        reply_message("ララピールとアクアピール、どちらですか?"),
    )

    assert read_cards(run(graph, "ピーリングしたい")) == []


def test_unknown_match_makes_no_card() -> None:
    graph = graph_with(
        tool_call("match_jp_treatment", {"query": "ダーマペン"}, "c1"),
        reply_message("対応する施術はありません"),
    )

    assert read_cards(run(graph, "ダーマペンある?")) == []


def test_same_menu_matched_twice_makes_one_card_from_last_result() -> None:
    graph = graph_with(
        tool_call("match_jp_treatment", {"query": "ハイフ"}, "c1"),
        tool_call("match_jp_treatment", {"query": "HIFU", "jp_clinic": "SBC"}, "c2"),
        reply_message("답변"),
    )

    [match] = read_cards(run(graph, "ハイフ, SBC"))

    assert match.query == "HIFU"
    assert match.jp_clinic.name_ja == "湘南美容クリニック (SBC)"


def test_match_card_comes_before_location_card_and_is_not_repeated_next_turn() -> None:
    graph = graph_with(
        tool_call("match_jp_treatment", {"query": "ハイフ"}, "c1"),
        tool_call("resolve_location", {"query": "홍대 숙소"}, "c2"),
        tool_call(
            "search_clinics",
            {"menu_id": "shrink-300", "origin_id": "area:hongdae", "limit": 1},
            "c3",
        ),
        reply_message("답변"),
        tool_call("search_clinics", {"menu_id": "shrink-300", "sort": "rating"}, "c4"),
        reply_message("후기 순이에요"),
    )

    first = read_cards(run(graph, "홍대 숙소, ハイフ"))
    assert [type(c) for c in first] == [JpMatchCard, LocationCard, RecommendationCard]

    second = read_cards(run(graph, "후기 순으로"))
    assert all(isinstance(c, RecommendationCard) for c in second)


def test_api_returns_jp_match_card_in_camel_case() -> None:
    graph = graph_with(
        tool_call(
            "match_jp_treatment", {"query": "ブナジュ", "jp_clinic": "SBC"}, "c1"
        ),
        reply_message("韓国ではリトゥオと呼びます"),
    )
    app.dependency_overrides[get_agent] = lambda: graph
    try:
        response = TestClient(app).post(
            "/api/agent/chat",
            json={"userId": str(uuid4()), "message": "ブナジュ", "lang": "ja"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    [card] = response.json()["cards"]
    assert card["kind"] == "jp_match"
    assert card["menuId"] == "re2o-5cc"
    assert card["unitNoteJa"]
    assert card["jpClinic"]["nameJa"] == "SBC NEO Skin Clinic"
    assert card["jpClinic"]["priceJpy"] == 66000
