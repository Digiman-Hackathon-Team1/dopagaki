"""search_clinics 도구: 거르기·정렬과, LLM용 요약·화면용 카드 데이터."""

import pytest
from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph
from api.agent.schemas import RecommendationCardData
from api.agent.tools import TOOLS
from api.agent.tools.search_clinics import MenuId, search_clinics
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog

HONGDAE = {"lat": 37.5563, "lng": 126.9228}


def call(**args) -> ToolMessage:
    return search_clinics.invoke(
        {"type": "tool_call", "name": "search_clinics", "id": "c1", "args": args}
    )


def call_in_graph(**args) -> ToolMessage:
    """가짜 LLM으로 그래프를 돌려 도구를 부른다. 도구 예외는 오류 메시지로 모델에 돌아간다."""
    model = fake_model(tool_call("search_clinics", args), reply_message())
    graph = build_graph(model, tools=TOOLS, checkpointer=InMemorySaver())
    result = graph.invoke(
        {"messages": [HumanMessage("찾아 줘")], "lang": "ko"},
        {"configurable": {"thread_id": "t"}},
    )
    return next(m for m in result["messages"] if isinstance(m, ToolMessage))


def cards(message: ToolMessage) -> list[RecommendationCardData]:
    return [RecommendationCardData.model_validate(c) for c in message.artifact["cards"]]


def test_menu_ids_match_catalog() -> None:
    assert set(MenuId.__args__) == {m.id for m in get_catalog().menus}


def test_default_returns_top3_by_savings_with_web_prices() -> None:
    result = cards(call(menu_id="rejuran-2cc"))

    expected = rank_rows(get_catalog(), "rejuran-2cc")[:3]
    assert [c.place_id for c in result] == [r.place.id for r in expected]
    for card, row in zip(result, expected, strict=True):
        assert card.card_id == f"{row.place.id}:rejuran-2cc"
        assert (card.total_krw, card.total_jpy, card.savings_jpy) == (
            row.total_krw,
            row.total_jpy,
            row.savings_jpy,
        )
        # 일본 주요 5곳 평균과 범위(설계 문서 4.7)
        assert (card.jp_average_jpy, card.jp_min_jpy, card.jp_max_jpy) == (
            69420,
            49500,
            99000,
        )
        assert card.jp_clinic_count == 5
        # 기준 위치가 없으면 거리를 내보내지 않는다(web도 내 위치일 때만 거리 표시).
        assert card.distance_meters is None


def test_summary_carries_same_amounts_as_cards() -> None:
    message = call(menu_id="rejuran-2cc")

    for card in cards(message):
        assert f"card_id={card.card_id}" in message.content
        assert f"₩{card.total_krw:,}" in message.content
        assert f"¥{card.savings_jpy:,}" in message.content
    assert "¥69,420" in message.content


def test_filters_japanese_area_and_budget() -> None:
    message = call(
        menu_id="rejuran-2cc",
        japanese_only=True,
        area="myeongdong",
        budget={"amount": 20000, "currency": "JPY"},
        limit=10,
    )
    result = cards(message)

    assert result
    for card in result:
        assert card.japanese_support
        assert card.area == "myeongdong"
        assert card.total_jpy <= 20000
    assert "excluded:" in message.content


def test_budget_in_krw_compares_vat_included_price() -> None:
    result = cards(
        call(menu_id="rejuran-2cc", budget={"amount": 100000, "currency": "KRW"})
    )

    assert result
    assert all(c.total_krw <= 100000 for c in result)


@pytest.mark.parametrize(
    "args",
    [
        {"menu_id": "rejuran-2cc", "avoid_needle": True},  # 바늘 시술
        {"menu_id": "inmode-fx", "max_downtime_days": 2},  # 회복 최대 5일
    ],
)
def test_treatment_level_exclusion_returns_no_cards(args: dict) -> None:
    message = call(**args)

    assert message.artifact == {"menu_id": args["menu_id"], "cards": []}
    assert "No clinics returned" in message.content


def test_no_needle_treatment_passes_needle_filter() -> None:
    assert cards(call(menu_id="shrink-300", avoid_needle=True, max_downtime_days=0))


def test_distance_sort_with_origin() -> None:
    result = cards(call(menu_id="shrink-300", sort="distance", origin=HONGDAE))

    distances = [c.distance_meters for c in result]
    assert None not in distances
    assert distances == sorted(distances)


def test_origin_outside_korea_is_ignored_and_reported() -> None:
    message = call(menu_id="shrink-300", origin={"lat": 35.6812, "lng": 139.7671})

    assert all(c.distance_meters is None for c in cards(message))
    assert "origin ignored" in message.content


def test_distance_sort_without_origin_is_an_error_for_the_model() -> None:
    message = call_in_graph(menu_id="shrink-300", sort="distance")

    assert message.status == "error"
    assert "resolve_location" in message.content


def test_rating_sort_follows_web_rating() -> None:
    result = cards(call(menu_id="rejuran-2cc", sort="rating"))

    expected = rank_rows(get_catalog(), "rejuran-2cc", sort="rating")[:3]
    assert [c.place_id for c in result] == [r.place.id for r in expected]


def test_unknown_menu_is_rejected() -> None:
    message = call_in_graph(menu_id="filler")

    assert message.status == "error"
    assert "menu_id" in message.content
