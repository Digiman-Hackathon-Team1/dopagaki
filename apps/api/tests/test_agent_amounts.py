"""답변 속 금액 대조(설계 문서 6장, 12장 숫자 검증, FR-03)."""

import pytest
from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.amounts import drop_unknown_amounts, find_money
from api.agent.graph import AMOUNT_CAUTION, build_graph, read_cards, read_reply
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog


@pytest.mark.parametrize(
    ("text", "amounts"),
    [
        ("₩139,000 incl. VAT ≈ ¥15,358", [("KRW", 139000), ("JPY", 15358)]),
        ("約¥21,000以上安くなります", [("JPY", 21000)]),
        ("2万1,111円", [("JPY", 21111)]),
        ("28만 4천 원", [("KRW", 284000)]),
        ("3만 엔(약 28만 원)", [("JPY", 30000), ("KRW", 280000)]),
        ("日本では26,000〜66,000円", [("JPY", 26000), ("JPY", 66000)]),
        ("(range ¥26,000–¥66,000)", [("JPY", 26000), ("JPY", 66000)]),
        ("300샷, 50% 저렴, 3곳", []),
    ],
)
def test_find_money(text: str, amounts: list[tuple[str, int]]) -> None:
    assert [(m.currency, m.value) for m in find_money(text)] == amounts


def test_drop_removes_sentence_parenthetical_and_table_row() -> None:
    known = {("JPY", 21111), ("JPY", 30000)}
    text = (
        "가장 싼 곳은 ¥21,111이에요. 시나가와보다 약 ¥21,000 싸요. 일본어 상담도 돼요.\n"
        "| 병원 | 가격 |\n| A | ¥21,111 |\n| B | ¥9,999 |\n"
        "3만 엔(약 28만 원) 조건으로 찾았어요"
    )

    kept, dropped = drop_unknown_amounts(text, known)

    assert kept == (
        "가장 싼 곳은 ¥21,111이에요. 일본어 상담도 돼요.\n"
        "| 병원 | 가격 |\n| A | ¥21,111 |\n"
        "3만 엔 조건으로 찾았어요"
    )
    assert [(m.currency, m.value) for m in dropped] == [
        ("JPY", 21000),
        ("JPY", 9999),
        ("KRW", 280000),
    ]


def test_drop_japanese_sentences() -> None:
    kept, _ = drop_unknown_amounts(
        "最安は¥21,111です。品川美容外科より約¥21,000以上安くなります。",
        {("JPY", 21111)},
    )
    assert kept == "最安は¥21,111です。"


def search_graph(answer: str, reasons: dict[str, str] | None = None):
    model = fake_model(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc", "limit": 1}),
        reply_message(answer, reasons),
    )
    return build_graph(model, tools=TOOLS, checkpointer=InMemorySaver())


def run(graph, message: str, lang: str = "ko") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": lang},
        {"configurable": {"thread_id": "t1"}},
    )


def test_amounts_from_cards_and_user_are_kept() -> None:
    top = rank_rows(get_catalog(), "rejuran-2cc")[0]
    answer = (
        f"₩{top.total_krw:,}(¥{top.total_jpy:,})이고 일본 평균보다 ¥{top.savings_jpy:,} 싸요. "
        "예산 3만 엔 안이에요."
    )
    graph = search_graph(answer)

    reply = read_reply(run(graph, "예산 3만 엔, 리쥬란 싼 곳"))

    assert reply.answer == answer
    assert AMOUNT_CAUTION["ko"] not in reply.cautions


def test_amount_not_in_tool_results_is_filtered() -> None:
    """완료 기준: 답변 속 금액이 카드와 다르면 걸러진다. Haiku가 반올림한 금액을 쓴 사례."""
    top = rank_rows(get_catalog(), "rejuran-2cc")[0]
    card_id = f"{top.place.id}:rejuran-2cc"
    rounded = round(top.savings_jpy, -3) + 1000
    graph = search_graph(
        f"最安は¥{top.total_jpy:,}です。日本平均より約¥{rounded:,}以上安くなります。",
        {card_id: f"一番安い。約¥{rounded:,}お得"},
    )

    result = run(graph, "リジュラン安いところ", lang="ja")
    reply = read_reply(result)

    assert reply.answer == f"最安は¥{top.total_jpy:,}です。"
    assert reply.cautions == [AMOUNT_CAUTION["ja"]]
    [card] = read_cards(result)
    # 이유가 전부 걸러지면 null. 카드 금액은 도구 결과 그대로다.
    assert card.reason == "一番安い。"
    assert card.total_jpy == top.total_jpy


def test_answer_made_only_of_wrong_amounts_becomes_caution() -> None:
    graph = search_graph("¥1,234 싸요.")

    reply = read_reply(run(graph, "리쥬란"))

    assert reply.answer == AMOUNT_CAUTION["ko"]
