"""'다른 병원 보여 줘'에 다음 순위를 보여 주기(#88): search_clinics·search_bundle의 offset과 카드 rank."""

import re

from agent_fakes import fake_model, reply_message, tool_call
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_cards
from api.agent.schemas import BundleCardData, RecommendationCard, RecommendationCardData
from api.agent.tools import TOOLS
from api.agent.tools.search_bundle import search_bundle
from api.agent.tools.search_clinics import search_clinics

BUNDLE = ["shrink-300", "aquapeel"]


def clinics(**args) -> ToolMessage:
    return search_clinics.invoke(
        {"type": "tool_call", "name": "search_clinics", "id": "c1", "args": args}
    )


def bundles(**args) -> ToolMessage:
    return search_bundle.invoke(
        {"type": "tool_call", "name": "search_bundle", "id": "c1", "args": args}
    )


def clinic_cards(message: ToolMessage) -> list[RecommendationCardData]:
    return [RecommendationCardData.model_validate(c) for c in message.artifact["cards"]]


def bundle_cards(message: ToolMessage) -> list[BundleCardData]:
    return [BundleCardData.model_validate(c) for c in message.artifact["cards"]]


def test_offset_returns_the_next_ranks_of_the_same_ranking() -> None:
    ranking = clinic_cards(clinics(menu_id="rejuran-2cc", limit=9))

    first = clinics(menu_id="rejuran-2cc")
    second = clinics(menu_id="rejuran-2cc", offset=3)

    assert [c.rank for c in clinic_cards(first)] == [1, 2, 3]
    assert [c.rank for c in clinic_cards(second)] == [4, 5, 6]
    assert [c.place_id for c in clinic_cards(second)] == [
        c.place_id for c in ranking[3:6]
    ]
    # LLM용 결과 문장도 실제 순위로 적어, 4위를 1위라고 부르지 않게 한다.
    assert "Ranks 4–6 of" in second.content
    assert f"\n4. card_id={ranking[3].card_id}" in second.content
    assert "Top 3:" in first.content


def test_offset_keeps_filters() -> None:
    ranking = clinic_cards(clinics(menu_id="rejuran-2cc", japanese_only=True, limit=6))

    second = clinic_cards(clinics(menu_id="rejuran-2cc", japanese_only=True, offset=3))

    assert [c.card_id for c in second] == [c.card_id for c in ranking[3:6]]
    assert all(c.japanese_support for c in second)


def matched(message: ToolMessage) -> int:
    """결과 문장의 "N clinics matched"(거르기를 지나 순위표에 오른 병원 수)."""
    return int(re.search(r"(\d+) clinics matched", message.content).group(1))


def test_offset_past_the_end_says_there_are_no_more() -> None:
    total = matched(clinics(menu_id="rejuran-2cc", area="hongdae"))

    last = clinics(menu_id="rejuran-2cc", area="hongdae", offset=total - 1)
    past = clinics(menu_id="rejuran-2cc", area="hongdae", offset=total)

    assert [c.rank for c in clinic_cards(last)] == [total]
    assert clinic_cards(past) == []
    assert f"No more clinics: all {total} matching clinics" in past.content


def test_bundle_offset_returns_the_next_ranks() -> None:
    ranking = bundle_cards(bundles(menu_ids=BUNDLE, limit=5))

    first = bundle_cards(bundles(menu_ids=BUNDLE, limit=2))
    second = bundles(menu_ids=BUNDLE, limit=2, offset=2)

    assert [c.rank for c in first] == [1, 2]
    assert [c.rank for c in bundle_cards(second)] == [3, 4]
    assert [c.card_id for c in bundle_cards(second)] == [
        c.card_id for c in ranking[2:4]
    ]
    assert "Ranks 3–4 of" in second.content


def test_bundle_offset_past_the_end_says_there_are_no_more() -> None:
    # 5곳씩 넘겨 순위표 끝을 찾고, 그다음 offset이면 더 없다고 알린다.
    args = {"menu_ids": BUNDLE, "area": "hongdae", "limit": 5}
    total = 0
    while page := bundle_cards(bundles(**args, offset=total)):
        assert [c.rank for c in page] == list(range(total + 1, total + len(page) + 1))
        total += len(page)

    result = bundles(**args, offset=total)

    assert total > 0
    assert f"No more bundles: all {total} matching clinics" in result.content


def test_other_clinics_turn_shows_new_clinics_from_rank_4() -> None:
    graph = build_graph(
        fake_model(
            tool_call("search_clinics", {"menu_id": "rejuran-2cc"}, "c1"),
            reply_message("세 곳이에요"),
            tool_call("search_clinics", {"menu_id": "rejuran-2cc", "offset": 3}, "c2"),
            reply_message("다음 세 곳이에요"),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "t1"}}

    def turn(message: str) -> list[RecommendationCard]:
        state = graph.invoke(
            {"messages": [HumanMessage(message)], "lang": "ko"}, config
        )
        return [c for c in read_cards(state) if isinstance(c, RecommendationCard)]

    first = turn("리쥬란 싼 곳")
    second = turn("다른 병원도 보여 줘")

    assert [c.rank for c in first] == [1, 2, 3]
    assert [c.rank for c in second] == [4, 5, 6]
    assert not {c.place_id for c in first} & {c.place_id for c in second}
