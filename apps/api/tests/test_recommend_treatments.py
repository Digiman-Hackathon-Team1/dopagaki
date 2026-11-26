"""recommend_treatments 도구: 고민별 시술표, 회복 기간·바늘 거르기, 지원하지 않는 고민."""

import importlib

import pytest
from langchain_core.messages import ToolMessage

from api.agent.tools import recommend_treatments
from api.domain.catalog import get_catalog

module = importlib.import_module("api.agent.tools.recommend_treatments")
plan_treatments = module.plan_treatments


def call(**args) -> str:
    message: ToolMessage = recommend_treatments.invoke(
        {"type": "tool_call", "name": "recommend_treatments", "id": "c1", "args": args}
    )
    return message.content


def ids(items) -> list[str]:
    return [i.menu.id for i in items]


def test_lift_with_three_days_keeps_shrink_and_excludes_inmode_for_downtime() -> None:
    """설계 문서 11.1: 리프팅, 회복 3일 → 슈링크(인모드는 회복 최대 5일이라 제외)."""
    plan = plan_treatments(["lift"], max_downtime_days=3)

    assert ids(plan.candidates) == ["shrink-300"]
    assert [(e.menu.id, e.reason) for e in plan.excluded] == [("inmode-fx", "downtime")]

    text = call(concerns=["lift"], max_downtime_days=3)
    assert "1. shrink-300" in text
    assert "inmode-fx" in text and "up to 5 days, more than the allowed 3 days" in text


def test_no_conditions_returns_concern_table_order() -> None:
    plan = plan_treatments(["dry"])

    assert ids(plan.candidates) == list(get_catalog().concern("dry").menu_ids)
    assert plan.excluded == []


def test_avoid_needle_excludes_needle_menus_first() -> None:
    """리쥬란은 바늘과 회복 기간(3일) 모두에 걸리지만 search_clinics처럼 바늘로 센다."""
    plan = plan_treatments(["dry"], max_downtime_days=0, avoid_needle=True)

    assert plan.candidates == []
    assert {e.reason for e in plan.excluded} == {"needle"}
    assert "No treatment fits" in call(
        concerns=["dry"], max_downtime_days=0, avoid_needle=True
    )


def test_downtime_rule_matches_search_clinics() -> None:
    """두 도구가 같은 값(downtime_max_days)과 같은 비교(초과면 제외)를 쓴다."""
    for menu in get_catalog().menus:
        allowed = menu.downtime_max_days
        concern = next(c.id for c in get_catalog().concerns if menu.id in c.menu_ids)
        kept = ids(plan_treatments([concern], max_downtime_days=allowed).candidates)
        assert menu.id in kept
        if allowed > 0:
            dropped = ids(
                plan_treatments([concern], max_downtime_days=allowed - 1).candidates
            )
            assert menu.id not in dropped


def test_several_concerns_interleave_most_direct_first() -> None:
    """web candidateMenuIds와 같이 고민마다 가장 직접적인 시술부터 번갈아 넣는다."""
    plan = plan_treatments(["pores", "lift"])

    assert ids(plan.candidates) == [
        "potenza",
        "shrink-300",
        "aquapeel",
        "inmode-fx",
        "juvelook-2cc",
    ]
    shared = plan_treatments(["glow", "dry"])
    rejuran = next(c for c in shared.candidates if c.menu.id == "rejuran-2cc")
    assert rejuran.concerns == ["glow", "dry"]


def test_unsupported_only_lists_supported_concerns_without_candidates() -> None:
    text = call(concerns=["unsupported"])

    assert "not supported" in text
    assert "pores, glow, lift, dry, jaw, skinbase" in text
    assert "Candidates" not in text


def test_unsupported_with_supported_keeps_candidates() -> None:
    text = call(concerns=["lift", "unsupported"])

    assert "not supported" in text
    assert "1. shrink-300" in text


@pytest.mark.parametrize("concerns", [[], ["hair"]])
def test_invalid_concerns_are_rejected(concerns: list[str]) -> None:
    with pytest.raises(ValueError):
        recommend_treatments.invoke({"concerns": concerns})
