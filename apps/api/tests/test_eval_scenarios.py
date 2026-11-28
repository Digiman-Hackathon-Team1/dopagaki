"""시나리오 평가 스크립트의 반복 집계와 judge 입력 만들기(이슈 #63). 실제 Claude는 부르지 않는다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_judges import Verdict, card_comparison_prompt, card_groups, judge_turn
from eval_scenarios import SCENARIOS, scenario_result, summarize


def _run(passed: bool, judges: list[dict] | None = None) -> dict:
    turn = {"llm_calls": 2, "input_tokens": 10, "output_tokens": 5}
    if judges is not None:
        turn["judges"] = judges
    return {"passed": passed, "turns": [turn]}


def _card(search_id: str, name: str, reason: str | None) -> dict:
    return {
        "kind": "recommendation",
        "searchId": search_id,
        "clinicNameKo": name,
        "totalKrw": 53900,
        "rating": 4.3,
        "reason": reason,
        "sourceUrl": "https://example.com",
    }


def test_scenario_passes_only_when_every_run_passes() -> None:
    scenario = SCENARIOS[0]
    flaky = scenario_result(scenario, [_run(True), _run(False), _run(True)])
    stable = scenario_result(scenario, [_run(True)] * 3)

    assert (flaky["passed"], flaky["passed_runs"]) == (False, 2)
    assert (stable["passed"], stable["passed_runs"]) == (True, 3)

    summary = summarize("claude-haiku-5-5", 3, [flaky, stable])
    assert summary["scenarios_passed"] == 1
    assert (summary["runs_passed"], summary["runs_total"]) == (5, 6)
    assert summary["llm_calls"] == 12


def test_summary_counts_judges_separately_from_code_grading() -> None:
    ok = {"judge": "card_comparison", "passed": True, "explanation": ""}
    bad = {"judge": "card_comparison", "passed": False, "explanation": "공동 1위"}
    result = scenario_result(SCENARIOS[0], [_run(True, [ok]), _run(True, [bad])])

    summary = summarize("claude-haiku-5-5", 2, [result])

    assert summary["scenarios_passed"] == 1
    assert summary["judges"] == {"card_comparison": {"passed": 1, "total": 2}}


def test_cards_are_compared_within_the_same_search() -> None:
    body = {
        "cards": [
            _card("s1", "A의원", "가장 싸요"),
            {"kind": "location"},
            _card("s2", "B의원", None),
            _card("s1", "C의원", None),
        ]
    }

    groups = card_groups(body)

    assert [[c["clinicNameKo"] for c in g] for g in groups] == [
        ["A의원", "C의원"],
        ["B의원"],
    ]
    prompt = card_comparison_prompt(groups[0])
    assert prompt is not None and "C의원" in prompt and "sourceUrl" not in prompt
    # 이유가 하나도 없으면 판정할 문장이 없다
    assert card_comparison_prompt(groups[1]) is None


def test_judge_turn_runs_only_applicable_judges() -> None:
    prompts: list[str] = []

    def fake_judge(prompt: str) -> Verdict:
        prompts.append(prompt)
        return Verdict(passed=False, explanation="틀림")

    body = {
        "answer": "강남 슈링크는 A의원이 가장 싸요.",
        "cards": [_card("s1", "A의원", "가장 싸요")],
    }

    answer = judge_turn(fake_judge, "슈링크 싼 곳", "answer", body)
    ask = judge_turn(
        fake_judge,
        "피부 좋아지고 싶어요",
        "ask",
        {"answer": "몇 가지 여쭤볼게요", "cards": []},
    )

    assert [j["judge"] for j in answer] == ["card_comparison", "answer_first"]
    assert answer[0] == {
        "judge": "card_comparison",
        "passed": False,
        "explanation": "틀림",
    }
    assert ask == []
    assert len(prompts) == 2


def test_rejudge_replaces_saved_judges_using_scenario_turns() -> None:
    from eval_scenarios import rejudge

    s1 = next(s for s in SCENARIOS if s.id == "s1")
    body = {
        "answer": "A의원이 가장 싸요.",
        "cards": [_card("s1", "A의원", "가장 싸요")],
    }
    saved_turns = [
        {"passed": True, "judges": [{"judge": "old"}], "response": body}
        for _ in s1.turns
    ]
    results = [{"id": "s1", "runs": [{"passed": True, "turns": saved_turns}]}]

    rejudge(lambda _: Verdict(passed=True, explanation="ok"), results)

    first, second = results[0]["runs"][0]["turns"]
    # s1 첫 턴은 답해야 하는 턴(shape=answer)이라 답이 먼저 judge도 돈다
    assert [j["judge"] for j in first["judges"]] == ["card_comparison", "answer_first"]
    assert [j["judge"] for j in second["judges"]] == ["card_comparison"]
    assert first["passed"] is True


def test_answer_first_prompt_tells_judge_cards_are_shown() -> None:
    from eval_judges import answer_first_prompt

    body = {
        "answer": "리쥬란 3곳을 찾았어요.",
        "cards": [_card("s1", "A의원", None)] * 3,
    }

    prompt = answer_first_prompt("리쥬란 싼 곳", body)

    assert prompt is not None and "병원 카드 3장" in prompt
    assert answer_first_prompt("리쥬란 싼 곳", {"answer": "", "cards": []}) is None
