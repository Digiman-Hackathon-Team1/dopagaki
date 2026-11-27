"""안전 정지(설계 문서 8장 safety_check, 12장, FR-06). 가짜 LLM이라 멈출지는 테스트가 정한다.

Claude가 문맥으로 멈추는지는 test_agent_live.py의 안전 시나리오가 확인한다.
"""

import pytest
from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards, read_reply
from api.agent.safety import find_topics, strip_health_conditions
from api.agent.tools import TOOLS


def run(graph, message: str, thread_id: str = "t1", lang: str = "ko") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": lang},
        {"configurable": {"thread_id": thread_id}},
    )


def notes(message: HumanMessage) -> list[str]:
    return [block["text"] for block in message.content[1:]]


@pytest.mark.parametrize(
    ("text", "topics"),
    [
        ("임신 중인데 리쥬란 받아도 돼요?", ["pregnancy"]),
        (
            "妊娠予定はありません",
            ["pregnancy"],
        ),  # 부정도 표시는 한다. 멈출지는 Claude가 정한다
        ("今授乳中です", ["breastfeeding"]),
        ("持病があって薬を飲んでいます", ["disease", "medication"]),
        ("아스피린 먹고 있어요", ["medication"]),
        ("수유역 근처 숙소예요", []),
        ("미앤미의원 수유점 리쥬란 가격", []),  # 실제 병원 지점 이름
        ("지금 수유 중이에요", ["breastfeeding"]),
        ("産後3ヶ月です", ["breastfeeding"]),
        ("ピルを飲んでいます", ["medication"]),
        ("예약 먹통이에요", []),
        ("피부가 간질간질해요", []),
        ("리쥬란 예약하고 싶어요", []),
        ("병원 추천해 줘", []),
    ],
)
def test_find_topics(text: str, topics: list[str]) -> None:
    assert find_topics(text) == topics


def test_data_names_are_not_health_keywords() -> None:
    """병원·역·지역·시술 이름이 건강 정보로 걸리면 그 병원 카드의 이유와 위치 조건이 늘 지워진다."""
    import json
    from pathlib import Path

    data = Path(__file__).parents[1] / "src/api/domain/data"
    texts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            texts.append(value)

    snapshot = json.loads((data / "web-snapshot.json").read_text())
    for key in ("places", "menus", "areas"):
        walk(snapshot[key])
    walk(json.loads((data / "stations.json").read_text()))

    assert [t for t in texts if find_topics(t)] == []


def test_strip_health_conditions_keeps_other_conditions() -> None:
    assert strip_health_conditions("3万円、弘大、妊娠予定なし") == "3万円、弘大"
    assert strip_health_conditions("예산 3만 엔 · 임신 계획 없음") == "예산 3만 엔"
    assert strip_health_conditions("리쥬란, 홍대") == "리쥬란, 홍대"


def test_note_is_added_only_when_keywords_found() -> None:
    model = fake_model(reply_message(), reply_message())
    graph = build_graph(model, checkpointer=InMemorySaver())

    run(graph, "리쥬란 싼 곳", thread_id="plain")
    run(graph, "임신 중인데 리쥬란 받아도 돼요?", thread_id="pregnant")

    plain = model.calls[0][-1]
    pregnant = model.calls[1][-1]
    assert len(notes(plain)) == 1  # 응답 언어 안내만 있다
    # 안전 안내는 언어 안내 앞이다. 시스템 프롬프트가 마지막 블록을 언어 안내로 읽는다.
    assert "pregnancy" in notes(pregnant)[0]
    assert "Korean" in notes(pregnant)[-1]


def test_note_stays_on_later_turns() -> None:
    """앞 턴에 임신을 말했으면 다음 턴에 시술만 물어도 안내가 붙는다(대화 기억으로 정지를 유지)."""
    model = fake_model(reply_message(safety_stop=True), reply_message())
    graph = build_graph(model, checkpointer=InMemorySaver())

    run(graph, "지금 임신 중이에요")
    run(graph, "그럼 리쥬란 싼 곳은?")

    assert "pregnancy" in notes(model.calls[1][-1])[0]
    # 앞 턴 메시지는 바뀌지 않는다(덧붙이기만 함).
    assert model.calls[1][1].content == model.calls[0][1].content


def test_safety_stop_drops_cards() -> None:
    model = fake_model(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc", "limit": 1}),
        reply_message("임신 중에는 권하지 않아요.", safety_stop=True),
    )
    graph = build_graph(model, tools=TOOLS, checkpointer=InMemorySaver())

    result = run(graph, "임신 중인데 리쥬란 싼 곳")

    assert read_reply(result).safety_stop is True
    assert read_cards(result) == []
    assert result["exclusions"] == []


def test_no_stop_keeps_cards() -> None:
    model = fake_model(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc", "limit": 1}),
        reply_message("가장 싼 곳이에요"),
    )
    graph = build_graph(model, tools=TOOLS, checkpointer=InMemorySaver())

    result = run(graph, "임신 계획은 없어요. 리쥬란 싼 곳")

    assert len(read_cards(result)) == 1


def test_health_information_is_filtered_from_screen_slots() -> None:
    model = fake_model(
        reply_message(
            "임신 계획이 없으시니 리쥬란을 볼게요.",
            condition_summary="리쥬란, 임신 계획 없음",
            cautions=["임신 중이면 병원에 알려 주세요", "자국이 3일 남을 수 있어요"],
            suggestions=[
                "임신 중에도 되는 시술",
                "가까운 곳 순으로",
                "후기 좋은 순으로",
            ],
        )
    )
    graph = build_graph(model, checkpointer=InMemorySaver())

    reply = read_reply(run(graph, "임신 계획은 없어요. 리쥬란"))

    assert reply.condition_summary == "리쥬란"
    assert reply.cautions == ["자국이 3일 남을 수 있어요"]
    assert reply.suggestions == ["가까운 곳 순으로", "후기 좋은 순으로"]
    # 답변은 거르지 않는다(추천을 멈춘 이유를 답변에 쓰기 때문).
    assert reply.answer.startswith("임신 계획이")
