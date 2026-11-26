"""match_jp_treatment 도구와 search_clinics의 일본 병원 직접 비교(설계 문서 4.3, 4.7, 9장)."""

from langchain_core.messages import ToolMessage

from api.agent.schemas import RecommendationCardData
from api.agent.tools import match_jp_treatment, search_clinics
from api.domain.calc import rank_rows, savings_against
from api.domain.catalog import get_catalog

SHRINK = get_catalog().menu("shrink-300")
SHINAGAWA = next(b for b in SHRINK.jp_benchmarks if b.name_ja == "品川美容外科")


def match(**args) -> ToolMessage:
    return match_jp_treatment.invoke(
        {"type": "tool_call", "name": "match_jp_treatment", "id": "c1", "args": args}
    )


def search(**args) -> ToolMessage:
    return search_clinics.invoke(
        {"type": "tool_call", "name": "search_clinics", "id": "c1", "args": args}
    )


def cards(message: ToolMessage) -> list[RecommendationCardData]:
    return [RecommendationCardData.model_validate(c) for c in message.artifact["cards"]]


def test_found_returns_menu_reason_and_unit_for_card() -> None:
    message = match(query="ハイフ")

    assert "found menu_id=shrink-300" in message.content
    assert "menu_id='shrink-300'" in message.content
    assert "기술 이름(HIFU·하이후)" in message.content
    assert message.artifact["status"] == "found"
    card = message.artifact["match"]
    assert card["query"] == "ハイフ"
    assert card["menu_id"] == "shrink-300"
    assert (card["menu_name_ko"], card["menu_name_ja"]) == (
        SHRINK.name_ko,
        SHRINK.name_ja,
    )
    assert "기기 이름(슈링크)" in card["reason_ko"]
    assert "650" in card["unit_note_ko"]
    assert card["jp_clinic"] is None


def test_found_with_jp_clinic_returns_that_clinic_price() -> None:
    message = match(query="ハイフ", jp_clinic="品川")

    assert "jp_clinic='品川美容外科'" in message.content
    assert "¥27,500" in message.content
    assert message.artifact["match"]["jp_clinic"] == {
        "name_ja": SHINAGAWA.name_ja,
        "name_ko": SHINAGAWA.name_ko,
        "plan_ja": SHINAGAWA.plan_ja,
        "plan_ko": SHINAGAWA.plan_ko,
        "price_jpy": SHINAGAWA.price_jpy,
        "source_url": SHINAGAWA.url,
    }


def test_jp_clinic_without_price_for_menu_says_no_direct_comparison() -> None:
    message = match(query="ハイフ", jp_clinic="TAクリニック")

    assert "no price for" in message.content
    assert "Japan average" in message.content
    assert "사각턱 보톡스" in message.content  # 그 병원 가격이 있는 시술
    assert message.artifact["match"]["jp_clinic"] is None


def test_jp_clinic_matching_two_clinics_asks_which() -> None:
    message = match(query="ララピール", jp_clinic="表参道")

    assert "matches several Japanese clinics" in message.content
    assert "表参道スキンクリニック" in message.content
    assert "表参道美容皮膚科" in message.content
    assert message.artifact["match"]["jp_clinic"] is None


def test_ambiguous_lists_candidates_without_card() -> None:
    message = match(query="ピーリング")

    assert "ambiguous" in message.content
    assert "menu_id=lalapeel" in message.content
    assert "menu_id=aquapeel" in message.content
    assert "Do not call search_clinics" in message.content
    assert message.artifact == {
        "status": "ambiguous",
        "match": None,
        "candidates": ["lalapeel", "aquapeel"],
    }


def test_several_treatments_asks_to_call_once_each() -> None:
    message = match(query="ハイフとポテンツァ")

    assert "several different treatments" in message.content
    assert "once per treatment" in message.content
    assert message.artifact["status"] == "ambiguous"


def test_unknown_says_no_match_without_guessing() -> None:
    message = match(query="ダーマペン")

    assert "unknown" in message.content
    assert "Do not guess" in message.content
    assert "recommend_treatments" in message.content
    assert message.artifact == {"status": "unknown", "match": None, "candidates": []}


def test_search_with_jp_clinic_adds_direct_savings_to_each_card() -> None:
    message = search(menu_id="shrink-300", jp_clinic="品川美容外科")

    expected = rank_rows(get_catalog(), "shrink-300")[:3]
    result = cards(message)
    assert [c.place_id for c in result] == [r.place.id for r in expected]
    for card, row in zip(result, expected, strict=True):
        assert card.jp_clinic is not None
        assert card.jp_clinic.name_ja == "品川美容外科"
        assert card.jp_clinic.price_jpy == 27500
        assert card.jp_clinic.savings_jpy == 27500 - row.total_jpy
        assert card.jp_clinic.savings_pct == round(
            (27500 - row.total_jpy) / 27500 * 100
        )
        # 일본 평균 대비 절약액은 그대로 둔다.
        assert card.savings_jpy == row.savings_jpy
    assert "Direct comparison" in message.content
    assert "vs 品川美容外科" in message.content


def test_direct_savings_can_be_negative() -> None:
    """일본 병원이 더 싸면 절약액은 음수다. 반올림은 web calc.ts(Math.round)와 같다."""
    assert savings_against(6000, 9127) == (-3127, -52)
    assert savings_against(27500, 6128) == (21372, 78)
    assert savings_against(0, 1000) == (-1000, 0)


def test_search_with_unknown_jp_clinic_keeps_average_only() -> None:
    message = search(menu_id="shrink-300", jp_clinic="TAクリニック")

    assert all(c.jp_clinic is None for c in cards(message))
    assert "no direct comparison" in message.content


def test_search_with_ambiguous_jp_clinic_keeps_average_only() -> None:
    message = search(menu_id="lalapeel", jp_clinic="表参道")

    assert all(c.jp_clinic is None for c in cards(message))
    assert "matches several Japanese clinics" in message.content


def test_search_without_jp_clinic_has_null_direct_comparison() -> None:
    assert all(c.jp_clinic is None for c in cards(search(menu_id="shrink-300")))
