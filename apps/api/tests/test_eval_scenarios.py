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


def _graded(turn, answer: str, cards: list[dict]) -> dict:
    from agent_fakes import reply_message
    from eval_scenarios import grade_turn
    from langchain_core.messages import HumanMessage

    body = {
        "fallback": None,
        "cards": cards,
        "questions": [],
        "conditionSummary": "조건",
    }
    messages = [HumanMessage("질문"), reply_message(answer)]
    return grade_turn(turn, body, messages)


def test_location_card_checks_the_searched_location() -> None:
    from eval_scenarios import Turn

    turn = Turn("강남역 근처 리쥬란", location_card="station:강남")

    hit = _graded(turn, "답", [{"kind": "location", "locationId": "station:강남"}])
    miss = _graded(turn, "답", [{"kind": "location", "locationId": "area:gangnam"}])
    none = _graded(turn, "답", [])

    assert hit["passed"]
    assert not miss["passed"] and "area:gangnam" in miss["failures"][0]
    assert not none["passed"]


def test_answer_turn_fails_when_claude_asked_even_if_app_removed_questions() -> None:
    # 앱은 병원 카드가 나간 턴의 질문 묶음을 지운다(#80). 채점은 Claude 원래 응답을 본다.
    import json

    from agent_fakes import reply_message
    from eval_scenarios import Turn, grade_turn
    from langchain_core.messages import AIMessage, HumanMessage

    raw = json.loads(reply_message("답").content)
    raw["questions"] = [
        {"question": "귀국일은?", "options": ["내일"], "multi_select": False}
    ]
    body = {
        "fallback": None,
        "cards": [{"kind": "recommendation"}],
        "questions": [],
        "conditionSummary": "조건",
    }
    messages = [
        HumanMessage("질문"),
        AIMessage(content=json.dumps(raw, ensure_ascii=False)),
    ]

    graded = grade_turn(Turn("리쥬란 싼 곳", shape="answer"), body, messages)

    assert graded["failures"] == ["답과 함께 질문 묶음 1개를 냄"]


def test_mentions_any_needs_one_of_the_words_in_answer() -> None:
    from eval_scenarios import EXCLUDED_INMODE, Turn

    turn = Turn("頬のたるみ", mentions_any=EXCLUDED_INMODE)

    assert _graded(turn, "インモードは回復5日なので外しました", [])["passed"]
    missed = _graded(turn, "シュリンクがおすすめです", [])
    assert missed["failures"] == ["답변에 インモード 언급 없음"]


def test_roundtrip_scenarios_are_not_in_the_default_eval() -> None:
    from eval_scenarios import ROUNDTRIP

    ids = {s.id for s in SCENARIOS}
    assert set(ROUNDTRIP) <= ids


TABLE_ANSWER = """리쥬란은 회복이 조금 더 길어요.

| | 리쥬란 | 쥬베룩 |
|---|---|---|
| 회복 기간 | 최대 3일 | 최대 2일 |
| 바늘 | 사용 | 사용 |

자세한 건 병원 상담에서 확인하세요."""


def test_table_lines_returns_only_the_markdown_table() -> None:
    from eval_scenarios import table_lines

    lines = table_lines(TABLE_ANSWER)

    assert lines[0] == "| | 리쥬란 | 쥬베룩 |"
    assert lines[-1] == "| 바늘 | 사용 | 사용 |"
    assert len(lines) == 4
    assert table_lines("리쥬란 | 쥬베룩 둘 다 바늘을 써요") == []


def test_has_table_and_table_excludes_check_only_table_rows() -> None:
    from eval_scenarios import NOT_IN_DATA, Turn

    turn = Turn("리쥬란 쥬베룩 차이", has_table=True, table_excludes=NOT_IN_DATA)

    # 표 밖에서 통증을 병원에 물어보라고 하는 문장은 괜찮다.
    assert _graded(turn, TABLE_ANSWER + "\n통증은 병원에서 설명해 줘요.", [])["passed"]
    assert _graded(turn, "리쥬란은 회복 최대 3일이에요.", [])["failures"] == [
        "답변에 표 없음"
    ]
    pain = TABLE_ANSWER.replace("| 바늘 | 사용 | 사용 |", "| 통증 | 중간 | 약함 |")
    assert _graded(turn, pain, [])["failures"] == ["표에 데이터에 없는 행: 통증"]


def test_mentions_none_fails_on_jargon() -> None:
    from eval_scenarios import JARGON, Turn

    turn = Turn("리쥬란", mentions_none=JARGON)

    assert _graded(turn, "회복 기간은 최대 3일이에요.", [])["passed"]
    assert _graded(turn, "다운타임은 최대 3일이에요.", [])["failures"] == [
        "응답에 쓰면 안 되는 말: 다운타임"
    ]


def test_searched_menus_needs_every_treatment() -> None:
    from agent_fakes import reply_message
    from eval_scenarios import Turn, grade_turn
    from langchain_core.messages import AIMessage, HumanMessage

    turn = Turn("차이", searched_menus=("rejuran-2cc", "juvelook-2cc"))
    body = {"fallback": None, "cards": [], "questions": [], "conditionSummary": "조건"}
    call = AIMessage(
        content="",
        tool_calls=[
            {"name": "search_clinics", "args": {"menu_id": "rejuran-2cc"}, "id": "t1"}
        ],
    )
    messages = [HumanMessage("질문"), call, reply_message("답")]

    graded = grade_turn(turn, body, messages)

    assert graded["failures"] == ["search_clinics로 juvelook-2cc를 찾지 않음"]


def test_table_lines_accepts_short_delimiters() -> None:
    from eval_scenarios import table_lines

    assert len(table_lines("| A | B |\n|:-:|-|\n| 1 | 2 |")) == 3


def test_mentions_none_checks_cautions_too() -> None:
    from agent_fakes import reply_message
    from eval_scenarios import JARGON, Turn, grade_turn
    from langchain_core.messages import HumanMessage

    reply = reply_message(
        "회복 기간은 최대 3일이에요.", cautions=["다운타임이 최대 3일이에요."]
    )
    body = {"fallback": None, "cards": [], "questions": [], "conditionSummary": "조건"}

    graded = grade_turn(
        Turn("리쥬란", mentions_none=JARGON), body, [HumanMessage("질문"), reply]
    )

    assert graded["failures"] == ["응답에 쓰면 안 되는 말: 다운타임"]


def test_reason_checks_look_at_the_cards_reason() -> None:
    from eval_scenarios import NEW_CLINIC_REASON, Turn

    turn = Turn(
        "리쥬란", reason_checks={"vands-balsan": (("개원",), NEW_CLINIC_REASON[1])}
    )
    shown = [{"kind": "recommendation", "placeId": "vands-balsan"}]

    def graded(reason: str | None, cards: list[dict]) -> dict:
        from agent_fakes import reply_message
        from eval_scenarios import grade_turn
        from langchain_core.messages import HumanMessage

        reasons = {"vands-balsan:rejuran-2cc": reason} if reason else None
        body = {
            "fallback": None,
            "cards": cards,
            "questions": [],
            "conditionSummary": "조건",
        }
        return grade_turn(
            turn, body, [HumanMessage("질문"), reply_message("답", reasons)]
        )

    assert graded("11월 25일에 새로 개원해 평점 집계 중이에요.", shown)["passed"]
    assert graded("평점 3.7로 낮은 편이에요.", shown)["failures"] == [
        "vands-balsan 추천 이유에 개원 언급 없음",
        "vands-balsan 추천 이유·답변에 쓰면 안 되는 말: 3.7",
    ]
    assert graded(None, shown)["failures"] == ["vands-balsan 카드 추천 이유 없음"]
    # 그 병원 카드가 나오지 않았으면 검사하지 않는다. 예약 카드는 추천 이유가 없어 보지 않는다.
    assert graded(None, [])["passed"]
    assert graded(None, [{"kind": "booking", "placeId": "vands-balsan"}])["passed"]
    # 신규 개원 검사는 낮은 평점이라는 설명만 막는다. 개원을 말하지 않아도 된다.
    only_forbid = Turn("리쥬란", reason_checks={"vands-balsan": NEW_CLINIC_REASON})
    body = {
        "fallback": None,
        "cards": shown,
        "questions": [],
        "conditionSummary": "조건",
    }
    from agent_fakes import reply_message
    from eval_scenarios import grade_turn
    from langchain_core.messages import HumanMessage

    message = reply_message("답", {"vands-balsan:rejuran-2cc": "2번째로 싸요."})
    assert grade_turn(only_forbid, body, [HumanMessage("질문"), message])["passed"]
    # 답변 본문에서 낮은 평점이라고 해도 실패다.
    message = reply_message(
        "발산점은 평점은 낮지만 싸요.", {"vands-balsan:rejuran-2cc": "싸요."}
    )
    graded_answer = grade_turn(only_forbid, body, [HumanMessage("질문"), message])
    assert graded_answer["failures"] == [
        "vands-balsan 추천 이유·답변에 쓰면 안 되는 말: 평점은 낮"
    ]
