"""finalize의 카드 조립(ADR 0009): 카드는 도구 결과 그대로, 추천 이유만 LLM."""

from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards, read_exclusions
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog

TOP3 = rank_rows(get_catalog(), "rejuran-2cc")[:3]
IDS = [f"{r.place.id}:rejuran-2cc" for r in TOP3]
REJURAN = {"menu_id": "rejuran-2cc"}


@tool(response_format="content_and_artifact")
def other_tool(text: str) -> tuple[str, dict]:
    """테스트용: 모양이 다른 artifact를 돌려주는 도구(나중의 jpMatch·booking 같은)."""
    return "ok", {"kind": "booking", "url": "https://example.com"}


def run(graph, message: str = "리쥬란 싼 곳", thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": thread_id}},
    )


def graph_with(*responses, tools=TOOLS):
    return build_graph(
        fake_model(*responses), tools=tools, checkpointer=InMemorySaver()
    )


def test_cards_take_amounts_from_tool_and_reasons_from_llm() -> None:
    graph = graph_with(
        tool_call("search_clinics", REJURAN),
        reply_message("세 곳이에요", {i: f"이유 {n}" for n, i in enumerate(IDS)}),
    )

    cards = read_cards(run(graph))

    assert [c.card_id for c in cards] == IDS
    assert [c.reason for c in cards] == ["이유 0", "이유 1", "이유 2"]
    assert [c.total_krw for c in cards] == [r.total_krw for r in TOP3]
    assert [c.savings_jpy for c in cards] == [r.savings_jpy for r in TOP3]
    assert all(c.kind == "recommendation" for c in cards)
    assert all(c.fx_date_label == get_catalog().fx.date_label for c in cards)


def test_all_tool_cards_are_shown_in_tool_order_even_without_reasons() -> None:
    # LLM이 순서를 바꾸고 하나를 빼고, 도구 결과에 없는 카드 ID를 지어내도
    # 도구 결과의 카드가 코드가 정한 순서대로 모두 나간다(설계 문서 4.7).
    graph = graph_with(
        tool_call("search_clinics", REJURAN),
        reply_message(
            "세 곳",
            {IDS[2]: "셋째", "made-up:rejuran-2cc": "가짜", IDS[0]: "첫째"},
        ),
    )

    cards = read_cards(run(graph))

    assert [c.card_id for c in cards] == IDS
    assert [c.reason for c in cards] == ["첫째", None, "셋째"]


def test_same_treatment_searched_again_uses_last_result() -> None:
    graph = graph_with(
        tool_call("search_clinics", REJURAN, "c1"),
        tool_call(
            "search_clinics",
            {**REJURAN, "japanese_only": True, "area": "myeongdong", "limit": 2},
            "c2",
        ),
        reply_message("명동"),
    )

    cards = read_cards(run(graph))

    assert len(cards) == 2
    assert all(c.area == "myeongdong" and c.japanese_support for c in cards)


def test_different_treatments_are_both_shown() -> None:
    graph = graph_with(
        tool_call("search_clinics", REJURAN, "c1"),
        tool_call("search_clinics", {"menu_id": "shrink-300", "limit": 2}, "c2"),
        reply_message("비교"),
    )

    cards = read_cards(run(graph))

    assert [c.menu_id for c in cards] == ["rejuran-2cc"] * 3 + ["shrink-300"] * 2


def test_other_tool_artifacts_are_ignored() -> None:
    graph = graph_with(
        tool_call("other_tool", {"text": "x"}, "c1"),
        tool_call("search_clinics", REJURAN, "c2"),
        reply_message("끝"),
        tools=[*TOOLS, other_tool],
    )

    assert [c.card_id for c in read_cards(run(graph))] == IDS


def test_cards_from_previous_turn_are_not_repeated() -> None:
    graph = graph_with(
        tool_call("search_clinics", REJURAN),
        reply_message("첫 턴", {IDS[0]: "이유"}),
        # 2턴째: 도구를 부르지 않고 앞 턴 카드 ID를 다시 써도 카드는 나가지 않는다.
        reply_message("둘째 턴", {IDS[0]: "이유"}),
    )

    assert len(read_cards(run(graph))) == 3
    assert read_cards(run(graph, "고마워")) == []


def test_failed_tool_call_produces_no_cards() -> None:
    graph = graph_with(
        tool_call("search_clinics", {"menu_id": "shrink-300", "sort": "distance"}),
        reply_message("위치를 알려 주세요", {"x:shrink-300": "이유"}),
    )

    assert read_cards(run(graph)) == []


def test_exclusions_follow_the_last_search_per_treatment() -> None:
    graph = graph_with(
        tool_call("search_clinics", {**REJURAN, "japanese_only": True}, "c1"),
        tool_call("search_clinics", {**REJURAN, "area": "gangnam"}, "c2"),
        tool_call("search_clinics", {"menu_id": "potenza", "avoid_needle": True}, "c3"),
        reply_message("끝"),
    )

    exclusions = read_exclusions(run(graph))

    # 리쥬란은 마지막 검색(강남만)의 제외 사유만, 포텐자는 바늘로 시술 전체가 빠진다.
    assert [(e.menu_id, e.reason) for e in exclusions] == [
        ("rejuran-2cc", "area"),
        ("potenza", "needle"),
    ]


def test_area_comparison_keeps_both_searches() -> None:
    graph = graph_with(
        tool_call("search_clinics", {**REJURAN, "area": "hongdae"}, "c1"),
        tool_call("search_clinics", {**REJURAN, "area": "gangnam"}, "c2"),
        reply_message("비교"),
    )

    result = run(graph)
    cards = read_cards(result)

    assert [c.area for c in cards] == ["hongdae"] * 3 + ["gangnam"] * 3
    assert [c.search_id for c in cards] == ["c1"] * 3 + ["c2"] * 3
    assert {e.search_id for e in read_exclusions(result)} == {"c1", "c2"}


def test_branch_comparison_keeps_both_branches() -> None:
    graph = graph_with(
        tool_call("search_clinics", {**REJURAN, "clinic_name": "밴스 홍대"}, "c1"),
        tool_call("search_clinics", {**REJURAN, "clinic_name": "블리비 명동"}, "c2"),
        reply_message("비교"),
    )

    cards = read_cards(run(graph))

    assert [c.place_id for c in cards] == ["vands-hongdae", "velyb-myeongdong"]


def test_narrowing_the_branch_name_replaces_the_search() -> None:
    graph = graph_with(
        tool_call("search_clinics", {**REJURAN, "clinic_name": "밴스"}, "c1"),
        tool_call("search_clinics", {**REJURAN, "clinic_name": "밴스 홍대"}, "c2"),
        reply_message("홍대점"),
    )

    assert [c.place_id for c in read_cards(run(graph))] == ["vands-hongdae"]


def test_overlapping_comparison_shows_each_clinic_once() -> None:
    # "밴스"와 "홍대입구" 검색 모두에 밴스의원 홍대점이 들어갈 수 있다.
    graph = graph_with(
        tool_call(
            "search_clinics", {**REJURAN, "clinic_name": "밴스", "limit": 10}, "c1"
        ),
        tool_call(
            "search_clinics", {**REJURAN, "clinic_name": "홍대입구", "limit": 10}, "c2"
        ),
        reply_message("비교"),
    )

    ids = [c.card_id for c in read_cards(run(graph))]

    assert "vands-hongdae:rejuran-2cc" in ids
    assert len(ids) == len(set(ids))
