"""finalize의 질문 묶음 정리(#80): 답한 턴에는 질문 묶음을 내보내지 않는다(설계 문서 4.2·4.8)."""

import json

import pytest
from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards, read_reply
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog

QUESTIONS = [
    {
        "question": "귀국일이 언제예요?",
        "options": ["내일", "모레"],
        "multi_select": False,
    },
    {
        "question": "숙소는 어디예요?",
        "options": ["홍대", "강남"],
        "multi_select": False,
    },
]
PLACE_ID = rank_rows(get_catalog(), "rejuran-2cc")[0].place.id
RESIDENT = {
    "visitor": "resident",
    "concerns": ["pores", "glow"],
    "days_until_event": 1,
    "budget": {"amount": 50000, "currency": "KRW"},
    "origin_id": "area:sinchon",
}


def asking(answer: str) -> AIMessage:
    """질문 묶음을 채운 응답 칸. Claude가 답하면서 질문 묶음도 함께 내는 경우를 흉내 낸다."""
    reply = json.loads(reply_message(answer).content)
    return AIMessage(
        content=json.dumps({**reply, "questions": QUESTIONS}, ensure_ascii=False)
    )


def run(*responses: AIMessage, message: str = "리쥬란 싼 곳") -> dict:
    graph = build_graph(
        fake_model(*responses), tools=TOOLS, checkpointer=InMemorySaver()
    )
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": "t1"}},
    )


@pytest.mark.parametrize(
    ("kind", "name", "args"),
    [
        ("recommendation", "search_clinics", {"menu_id": "rejuran-2cc"}),
        ("bundle", "search_bundle", {"menu_ids": ["shrink-300", "aquapeel"]}),
        ("trip_plan", "plan_trip", RESIDENT),
        ("booking", "get_booking_options", {"place_id": PLACE_ID}),
    ],
)
def test_questions_are_dropped_when_answer_cards_are_shown(
    kind: str, name: str, args: dict
) -> None:
    result = run(tool_call(name, args), asking("찾았어요. 귀국일을 알려 주세요."))

    assert kind in {c.kind for c in read_cards(result)}
    reply = read_reply(result)
    assert reply.questions == []
    # 질문 묶음만 비우고 답변 끝의 덧붙인 질문은 그대로 둔다(설계 문서 4.2).
    assert reply.answer == "찾았어요. 귀국일을 알려 주세요."


def test_questions_are_kept_without_cards() -> None:
    result = run(asking("고민을 알려 주세요."), message="피부 고민이 있어요")

    assert read_cards(result) == []
    assert len(read_reply(result).questions) == 2


def test_questions_are_kept_with_only_a_location_card() -> None:
    result = run(
        tool_call("resolve_location", {"query": "홍대 숙소"}),
        asking("홍대 기준으로 볼게요. 어떤 시술을 찾으세요?"),
        message="홍대 숙소예요",
    )

    assert [c.kind for c in read_cards(result)] == ["location"]
    assert len(read_reply(result).questions) == 2
