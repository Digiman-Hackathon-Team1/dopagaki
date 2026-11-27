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
from api.domain.locations import get_locations


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


def test_default_shows_clinics_with_and_without_japanese_support() -> None:
    """일본어 상담 여부로 거르지 않고 절약액 순으로 보여 주며, 카드로 구분한다(설계 문서 4.3·4.7, v1.6)."""
    result = cards(call(menu_id="rejuran-2cc", limit=10))
    japanese = cards(call(menu_id="rejuran-2cc", limit=10, japanese_only=True))

    expected = rank_rows(get_catalog(), "rejuran-2cc")[:10]
    assert [c.place_id for c in result] == [r.place.id for r in expected]
    assert {c.japanese_support for c in result} == {True, False}
    assert all(c.japanese_support for c in japanese)


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
    # 사유별 제외 수와 남은 병원 수를 더하면 이 시술을 하는 병원 수와 같다.
    counts = {e["reason"]: e["clinic_count"] for e in message.artifact["exclusions"]}
    # 사유는 처음 걸린 하나만 센다(지역 → 일본어 → 예산). 명동 리쥬란 병원은 모두 일본어가 된다.
    assert set(counts) == {"area", "budget"}
    matched = int(message.content.split(" clinics matched")[0].rsplit("\n", 1)[-1])
    assert sum(counts.values()) + matched == len(
        rank_rows(get_catalog(), "rejuran-2cc")
    )


def test_cards_carry_price_tags() -> None:
    # 밴스의원 홍대점: "체험 이벤트가・마취비 별도 확인 필요"
    message = call(menu_id="rejuran-2cc", area="hongdae", limit=10)
    vands = next(c for c in cards(message) if c.place_id == "vands-hongdae")

    assert vands.price_tags == ["first_visit", "event", "anesthesia"]
    assert "price tags: first_visit, event, anesthesia" in message.content


def test_budget_in_krw_compares_vat_included_price() -> None:
    result = cards(
        call(menu_id="rejuran-2cc", budget={"amount": 100000, "currency": "KRW"})
    )

    assert result
    assert all(c.total_krw <= 100000 for c in result)


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ({"menu_id": "rejuran-2cc", "avoid_needle": True}, "needle"),  # 바늘 시술
        ({"menu_id": "inmode-fx", "max_downtime_days": 2}, "downtime"),  # 회복 최대 5일
    ],
)
def test_treatment_level_exclusion_returns_no_cards(args: dict, reason: str) -> None:
    message = call(**args)

    offered = len(rank_rows(get_catalog(), args["menu_id"]))
    assert message.artifact["cards"] == []
    # 시술 전체가 빠지므로 그 시술을 하는 병원 전부가 같은 사유로 빠진다.
    assert message.artifact["exclusions"] == [
        {"menu_id": args["menu_id"], "reason": reason, "clinic_count": offered}
    ]
    assert "No clinics returned" in message.content


def test_no_needle_treatment_passes_needle_filter() -> None:
    assert cards(call(menu_id="shrink-300", avoid_needle=True, max_downtime_days=0))


def test_distance_sort_with_origin_id() -> None:
    message = call(menu_id="shrink-300", sort="distance", origin_id="area:hongdae")
    result = cards(message)

    hongdae = get_locations().get("area:hongdae")
    expected = rank_rows(
        get_catalog(), "shrink-300", sort="distance", origin=hongdae.point
    )
    assert [c.place_id for c in result] == [r.place.id for r in expected[:3]]
    assert [c.distance_meters for c in result] == [
        r.distance_meters for r in expected[:3]
    ]
    assert "distances from 홍대 / 弘大" in message.content


def test_station_origin_uses_station_coordinates() -> None:
    message = call(menu_id="rejuran-2cc", origin_id="station:홍대입구")

    station = get_locations().get("station:홍대입구")
    expected = rank_rows(get_catalog(), "rejuran-2cc", origin=station.point)[:3]
    assert [c.distance_meters for c in cards(message)] == [
        r.distance_meters for r in expected
    ]


def test_unknown_origin_id_is_an_error_for_the_model() -> None:
    message = call_in_graph(menu_id="shrink-300", origin_id="area:itaewon")

    assert message.status == "error"
    assert "Unknown origin_id" in message.content
    assert "resolve_location" in message.content


def test_distance_sort_without_origin_is_an_error_for_the_model() -> None:
    message = call_in_graph(menu_id="shrink-300", sort="distance")

    assert message.status == "error"
    assert "Call resolve_location" in message.content


def test_rating_sort_follows_web_rating() -> None:
    result = cards(call(menu_id="rejuran-2cc", sort="rating"))

    expected = rank_rows(get_catalog(), "rejuran-2cc", sort="rating")[:3]
    assert [c.place_id for c in result] == [r.place.id for r in expected]


def test_unknown_menu_is_rejected() -> None:
    message = call_in_graph(menu_id="filler")

    assert message.status == "error"
    assert "menu_id" in message.content


@pytest.mark.parametrize(
    ("clinic_name", "place_ids"),
    [
        ("밴스 홍대", ["vands-hongdae"]),  # 여러 단어는 모두 들어간 지점만
        ("バンス 明洞", ["vands-myeongdong"]),
        ("  밴스   홍대  ", ["vands-hongdae"]),  # 공백 정리
    ],
)
def test_clinic_name_finds_branches(clinic_name: str, place_ids: list[str]) -> None:
    assert [
        c.place_id for c in cards(call(menu_id="rejuran-2cc", clinic_name=clinic_name))
    ] == place_ids


def test_clinic_name_brand_returns_branches_in_savings_order() -> None:
    result = cards(call(menu_id="rejuran-2cc", clinic_name="블리비", limit=10))

    expected = rank_rows(get_catalog(), "rejuran-2cc", search_query="블리비")[:10]
    assert [c.place_id for c in result] == [r.place.id for r in expected]
    assert all("블리비" in c.clinic_name_ko for c in result)


def test_clinic_name_is_not_counted_as_exclusion() -> None:
    message = call(menu_id="rejuran-2cc", clinic_name="블리비", japanese_only=True)

    reasons = {e["reason"] for e in message.artifact["exclusions"]}
    # 이름에 맞지 않는 병원은 찾을 대상이 아니므로 제외 수에 넣지 않는다.
    assert reasons <= {"japanese"}


def test_clinic_without_this_treatment_lists_its_treatments() -> None:
    message = call(menu_id="lalapeel", clinic_name="밴스 홍대")

    assert message.artifact["cards"] == []
    assert "have no price for lalapeel" in message.content
    assert "밴스의원 홍대점" in message.content
    assert "리쥬란 2cc" in message.content


def test_unknown_clinic_name_says_so() -> None:
    message = call(menu_id="rejuran-2cc", clinic_name="없는병원")

    assert message.artifact["cards"] == []
    assert "No clinic in the data matches" in message.content


def test_search_target_is_recorded() -> None:
    message = call(menu_id="rejuran-2cc", clinic_name="밴스 홍대", area="hongdae")

    assert message.artifact["target"] == {
        "area": "hongdae",
        "origin_id": None,
        "clinic_tokens": ["밴스", "홍대"],
        "clinic_ids": ["vands-hongdae"],
    }


def test_treatment_exclusion_with_unknown_clinic_has_no_zero_count() -> None:
    message = call(menu_id="rejuran-2cc", avoid_needle=True, clinic_name="없는병원")

    # 빠진 병원이 0곳인 사유는 내보내지 않고, 지점을 못 찾았다고 알린다.
    assert message.artifact["exclusions"] == []
    assert "No clinic in the data matches" in message.content
