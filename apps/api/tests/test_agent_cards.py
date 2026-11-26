"""finalize의 카드 조립(ADR 0009): 금액은 도구 결과, 이유와 노출 여부는 LLM."""

from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog

TOP3 = rank_rows(get_catalog(), "rejuran-2cc")[:3]
IDS = [f"{r.place.id}:rejuran-2cc" for r in TOP3]


def run(graph, message: str = "리쥬란 싼 곳", thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": thread_id}},
    )


def graph_with(*responses):
    return build_graph(
        fake_model(*responses), tools=TOOLS, checkpointer=InMemorySaver()
    )


def test_cards_take_amounts_from_tool_and_reasons_from_llm() -> None:
    graph = graph_with(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc"}),
        reply_message("세 곳이에요", {i: f"이유 {n}" for n, i in enumerate(IDS)}),
    )

    cards = read_cards(run(graph))

    assert [c.card_id for c in cards] == IDS
    assert [c.reason for c in cards] == ["이유 0", "이유 1", "이유 2"]
    assert [c.total_krw for c in cards] == [r.total_krw for r in TOP3]
    assert [c.savings_jpy for c in cards] == [r.savings_jpy for r in TOP3]
    assert all(c.kind == "recommendation" for c in cards)


def test_only_cards_with_reasons_are_shown_in_tool_order() -> None:
    # LLM이 순서를 바꾸고 하나를 빼고, 도구 결과에 없는 카드 ID를 지어내도
    # 코드가 정한 순서로 도구 결과에 있는 카드만 나간다.
    graph = graph_with(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc"}),
        reply_message(
            "두 곳",
            {IDS[2]: "셋째", "made-up:rejuran-2cc": "가짜", IDS[0]: "첫째"},
        ),
    )

    cards = read_cards(run(graph))

    assert [c.card_id for c in cards] == [IDS[0], IDS[2]]


def test_no_reasons_means_no_cards() -> None:
    graph = graph_with(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc"}), reply_message("요약만")
    )

    assert read_cards(run(graph)) == []


def test_cards_from_previous_turn_are_not_repeated() -> None:
    graph = graph_with(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc"}),
        reply_message("첫 턴", {IDS[0]: "이유"}),
        # 2턴째: 도구를 부르지 않고 앞 턴 카드 ID를 다시 써도 카드는 나가지 않는다.
        reply_message("둘째 턴", {IDS[0]: "이유"}),
    )

    assert len(read_cards(run(graph))) == 1
    assert read_cards(run(graph, "고마워")) == []


def test_failed_tool_call_produces_no_cards() -> None:
    graph = graph_with(
        tool_call("search_clinics", {"menu_id": "shrink-300", "sort": "distance"}),
        reply_message("위치를 알려 주세요", {"x:shrink-300": "이유"}),
    )

    assert read_cards(run(graph)) == []
