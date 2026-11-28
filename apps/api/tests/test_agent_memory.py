"""사용자 기억 저장·불러오기(설계 문서 8장 사용자 기억 관리, 4.2, ADR 0019). 가짜 LLM을 쓴다.

- 저장: 응답 칸 remember에서 사용자가 직접 말한 값만 저장한다(예산·일본 시술·위치 대조, 건강 정보 거르기).
- 불러오기: 대화 첫 턴과 저장 내용이 바뀐 뒤 첫 턴에 [본문, 기억 안내, 오늘 날짜, 응답 언어] 순서로 붙인다.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from agent_fakes import EMPTY_REMEMBER, fake_model, reply_message, tool_call
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import AMOUNT_CAUTION, LANG_NOTE, build_graph, read_reply
from api.agent.memory import profile_note, to_patch
from api.agent.schemas import Remember
from api.agent.tools import TOOLS
from api.domain.profile import (
    InMemoryProfileStore,
    ProfileBudget,
    ProfilePatch,
    UserProfile,
)

TODAY = date(2026, 11, 28)
# 어제(서울) 저장한 것으로 둔다.
SAVED_AT = datetime(2026, 11, 27, 3, 0, tzinfo=UTC)
USER = uuid4()


def new_store(now: datetime = SAVED_AT) -> InMemoryProfileStore:
    return InMemoryProfileStore(ttl=timedelta(days=30), max_users=100, now=lambda: now)


def graph_with(model, store, tools=()):
    return build_graph(
        model,
        tools=list(tools),
        checkpointer=InMemorySaver(),
        profile_store=store,
        today=lambda: TODAY,
    )


def run(graph, message: str, thread: str = "t1", user: UUID = USER, lang: str = "ko"):
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": lang},
        {"configurable": {"thread_id": thread, "user_id": str(user)}},
    )


def last_user_blocks(call) -> list[str]:
    human = [m for m in call if isinstance(m, HumanMessage)][-1]
    return [b["text"] for b in human.content]


def remember(**values) -> Remember:
    return Remember.model_validate({**EMPTY_REMEMBER, **values})


def said(*texts: str) -> list:
    return [HumanMessage([{"type": "text", "text": t}]) for t in texts]


# --- to_patch: 사용자가 직접 말한 값만 남긴다 ---


def test_budget_the_user_said_is_kept() -> None:
    patch, dropped = to_patch(
        remember(budget={"amount": 30000, "currency": "JPY", "scope": "per_treatment"}),
        said("1회 3만 엔 정도요"),
        TODAY,
    )
    assert patch.budget == ProfileBudget(
        amount=30000, currency="JPY", scope="per_treatment"
    )
    assert dropped == []


def test_bare_man_budget_counts_as_yen_or_won() -> None:
    patch, _ = to_patch(
        remember(budget={"amount": 30000, "currency": "JPY", "scope": None}),
        said("예산은 3만 정도"),
        TODAY,
    )
    assert patch.budget.amount == 30000


def test_budget_not_said_by_user_is_dropped() -> None:
    patch, dropped = to_patch(
        remember(budget={"amount": 50000, "currency": "JPY", "scope": None}),
        said("리쥬란 싼 곳 알려 줘"),
        TODAY,
    )
    assert patch is None
    assert dropped == ["budget"]


def test_relative_return_day_uses_seoul_date() -> None:
    patch, _ = to_patch(remember(return_in_days=2), said("모레 귀국해요"), TODAY)
    assert patch.return_date == date(2026, 11, 30)


def test_calendar_date_and_out_of_range_days() -> None:
    patch, dropped = to_patch(
        remember(arrival_date="2026-12-03", return_date="2025-12-05"),
        said("12월 3일에 와요"),
        TODAY,
    )
    assert patch.arrival_date == date(2026, 12, 3)
    assert patch.return_date is None
    assert dropped == ["return_date"]

    _, dropped = to_patch(remember(return_in_days=400), said("내년"), TODAY)
    assert dropped == ["return_date"]


def test_arrival_after_return_drops_arrival() -> None:
    patch, dropped = to_patch(
        remember(arrival_in_days=5, return_in_days=2), said("..."), TODAY
    )
    assert patch.arrival_date is None
    assert patch.return_date == date(2026, 11, 30)
    assert dropped == ["arrival_date"]


def test_health_text_in_jp_treatments_is_not_saved() -> None:
    patch, dropped = to_patch(
        remember(jp_treatments=["ハイフ", "ステロイド注射"]),
        said("日本でハイフとステロイド注射を受けました"),
        TODAY,
    )
    assert patch.jp_treatments == ("ハイフ",)
    assert dropped == ["jp_treatments"]


def test_jp_treatment_must_be_in_user_text_ignoring_width() -> None:
    patch, dropped = to_patch(
        remember(jp_treatments=["HIFU", "ボトックス"]),
        said("日本でＨＩＦＵを受けました"),
        TODAY,
    )
    assert patch.jp_treatments == ("HIFU",)
    assert dropped == ["jp_treatments"]


def test_location_must_be_found_in_this_conversation() -> None:
    found = ToolMessage(
        "found",
        tool_call_id="c1",
        name="resolve_location",
        artifact={
            "status": "found",
            "location": {
                "location_id": "area:hongdae",
                "location_type": "area",
                "query": "홍대",
                "name_ja": "弘大",
                "name_ko": "홍대",
                "lat": 37.5563,
                "lng": 126.9228,
            },
        },
    )
    patch, _ = to_patch(
        remember(location_id="area:hongdae"), [*said("홍대 숙소"), found], TODAY
    )
    assert patch.location_id == "area:hongdae"

    patch, dropped = to_patch(remember(location_id="area:gangnam"), said("강남"), TODAY)
    assert patch is None
    assert dropped == ["location_id"]


def test_concerns_capped_at_three() -> None:
    patch, _ = to_patch(
        remember(concerns=["lift", "pores", "lift", "dry", "jaw"]), said("..."), TODAY
    )
    assert patch.concerns == ("lift", "pores", "dry")


# --- profile_note ---


def test_note_lists_saved_conditions_with_days_left() -> None:
    profile = UserProfile(
        visitor="traveler",
        budget=ProfileBudget(amount=30000, currency="JPY", scope="per_treatment"),
        return_date=date(2026, 11, 30),
        location_id="area:hongdae",
        avoid_needle=True,
        updated_at=SAVED_AT,
    )
    note = profile_note(profile, TODAY)

    assert note.startswith("[Note from Dopa app] Saved conditions")
    assert "last saved 2026-11-27, yesterday" in note
    assert "- budget: 30,000 JPY per treatment" in note
    assert (
        "- return date: 2026-11-30 (in 2 days; from today to the return day is 3 days)"
        in note
    )
    assert "area:hongdae" in note
    assert "- avoids needles" in note


def test_note_leaves_out_a_finished_stay() -> None:
    profile = UserProfile(
        arrival_date=date(2026, 11, 20),
        return_date=date(2026, 11, 25),
        updated_at=SAVED_AT,
    )
    assert profile_note(profile, TODAY) is None

    profile = profile.model_copy(update={"avoid_needle": False})
    note = profile_note(profile, TODAY)
    assert "return date" not in note
    assert "arrival" not in note


# --- 그래프 흐름 ---


def test_new_thread_gets_profile_note_before_lang_note() -> None:
    store = new_store()
    store.update(USER, ProfilePatch(budget=ProfileBudget(amount=30000, currency="JPY")))
    model = fake_model(reply_message("답"))
    run(graph_with(model, store), "리쥬란 싼 곳")

    blocks = last_user_blocks(model.calls[0])
    assert blocks[0] == "리쥬란 싼 곳"
    assert blocks[1].startswith("[Note from Dopa app] Saved conditions")
    assert blocks[2] == "[Note from Dopa app] Today in Seoul: 2026-11-28 (Sat)."
    assert blocks[3] == LANG_NOTE.format(language="Korean")


def test_no_note_when_profile_empty() -> None:
    model = fake_model(reply_message("답"))
    run(graph_with(model, new_store()), "안녕")

    assert len(last_user_blocks(model.calls[0])) == 3


def test_note_reattached_only_when_changed() -> None:
    store = new_store()
    store.update(USER, ProfilePatch(visitor="traveler"))
    model = fake_model(
        reply_message("첫 답"),
        reply_message(
            "둘째 답",
            remember={"budget": {"amount": 50000, "currency": "KRW", "scope": None}},
        ),
        reply_message("셋째 답"),
    )
    graph = graph_with(model, store)

    run(graph, "안녕")
    run(graph, "예산은 5만 원이에요")
    run(graph, "고마워요")

    def has_note(call) -> bool:
        return any(
            b.startswith("[Note from Dopa app] Saved") for b in last_user_blocks(call)
        )

    assert [has_note(c) for c in model.calls] == [True, False, True]
    assert "50,000 KRW" in last_user_blocks(model.calls[2])[1]


def test_remember_is_saved_for_the_user_in_config_only() -> None:
    store = new_store()
    model = fake_model(
        reply_message(
            "알겠어요",
            remember={
                "budget": {
                    "amount": 30000,
                    "currency": "JPY",
                    "scope": "per_treatment",
                },
                "return_in_days": 2,
            },
        )
    )
    state = run(graph_with(model, store), "1회 3만 엔이고 모레 귀국해요")

    saved = store.get(USER)
    assert saved.budget.amount == 30000
    assert saved.return_date == date(2026, 11, 30)
    assert store.get(uuid4()) is None
    # 화면으로 가는 응답 칸은 그대로다.
    assert read_reply(state).answer == "알겠어요"


def test_health_not_saved_end_to_end() -> None:
    store = new_store()
    model = fake_model(
        reply_message(
            "지금은 권하지 않아요.",
            safety_stop=True,
            remember={"jp_treatments": ["ステロイド注射"]},
        )
    )
    run(graph_with(model, store), "日本でステロイド注射を受けています", lang="ja")

    assert store.get(USER) is None


def test_current_message_overrides_saved_budget() -> None:
    store = new_store()
    store.update(USER, ProfilePatch(budget=ProfileBudget(amount=30000, currency="JPY")))
    model = fake_model(
        reply_message(
            "5만 엔 기준으로 찾았어요",
            remember={"budget": {"amount": 50000, "currency": "JPY", "scope": None}},
        )
    )
    run(graph_with(model, store), "이번엔 예산 5만 엔으로 볼래요")

    note = last_user_blocks(model.calls[0])[1]
    assert "what the user says now wins" in note
    assert store.get(USER).budget.amount == 50000


def test_saved_budget_amount_survives_amount_filter() -> None:
    store = new_store()
    store.update(USER, ProfilePatch(budget=ProfileBudget(amount=30000, currency="JPY")))
    model = fake_model(reply_message("어제 말한 예산 3万円 기준으로 찾았어요."))
    state = run(graph_with(model, store), "リジュラン安いところ", lang="ja")

    reply = read_reply(state)
    assert reply.answer == "어제 말한 예산 3万円 기준으로 찾았어요."
    assert AMOUNT_CAUTION["ja"] not in reply.cautions


def test_after_delete_new_thread_has_no_note() -> None:
    store = new_store()
    store.update(USER, ProfilePatch(visitor="traveler"))
    store.delete(USER)
    model = fake_model(reply_message("답"))
    run(graph_with(model, store), "안녕", thread="t2")

    assert not any(
        b.startswith("[Note from Dopa app] Saved")
        for b in last_user_blocks(model.calls[0])
    )


def test_location_saved_after_resolve_location_in_same_turn() -> None:
    store = new_store()
    model = fake_model(
        tool_call("resolve_location", {"query": "홍대"}),
        reply_message("홍대 기준으로 볼게요", remember={"location_id": "area:hongdae"}),
    )
    run(graph_with(model, store, tools=TOOLS), "홍대 숙소예요")

    assert store.get(USER).location_id == "area:hongdae"


def test_store_failure_does_not_break_the_reply() -> None:
    class Broken(InMemoryProfileStore):
        def get(self, user_id):
            raise RuntimeError("down")

        def update(self, user_id, patch, **kwargs):
            raise RuntimeError("down")

    model = fake_model(reply_message("답", remember={"visitor": "traveler"}))
    state = run(
        graph_with(model, Broken(ttl=timedelta(days=1), max_users=1)), "여행 왔어요"
    )

    assert read_reply(state).answer == "답"


def test_graph_without_store_keeps_old_blocks() -> None:
    model = fake_model(reply_message("답"))
    graph = build_graph(model, checkpointer=InMemorySaver())
    graph.invoke(
        {"messages": [HumanMessage("안녕")], "lang": "ko"},
        {"configurable": {"thread_id": "t1"}},
    )
    assert last_user_blocks(model.calls[0]) == [
        "안녕",
        LANG_NOTE.format(language="Korean"),
    ]
    assert isinstance(model.calls[0][-1], HumanMessage)
    assert not isinstance(model.calls[0][-1], AIMessage)


# --- push 전 리뷰에서 나온 경우 ---


def test_visitor_needs_travel_or_living_words() -> None:
    patch, dropped = to_patch(remember(visitor="traveler"), said("리쥬란 싼 곳"), TODAY)
    assert patch is None
    assert dropped == ["visitor"]

    patch, _ = to_patch(remember(visitor="traveler"), said("모레 귀국해요"), TODAY)
    assert patch.visitor == "traveler"
    patch, _ = to_patch(
        remember(visitor="resident"), said("ソウルに住んでいます"), TODAY
    )
    assert patch.visitor == "resident"


def test_saved_resident_not_overwritten_by_default_traveler() -> None:
    store = new_store()
    store.update(USER, ProfilePatch(visitor="resident"))
    model = fake_model(reply_message("답", remember={"visitor": "traveler"}))
    run(graph_with(model, store), "리쥬란 싼 곳")

    assert store.get(USER).visitor == "resident"


def test_side_effect_text_in_jp_treatments_is_not_saved() -> None:
    patch, dropped = to_patch(
        remember(jp_treatments=["ボトックスでアレルギー"]),
        said("日本でボトックスでアレルギーが出ました"),
        TODAY,
    )
    assert patch is None
    assert dropped == ["jp_treatments"]


def test_delete_during_turn_does_not_bring_memory_back() -> None:
    from langchain_core.tools import tool

    store = new_store()
    store.update(USER, ProfilePatch(visitor="traveler"))

    @tool
    def delete_now() -> str:
        """테스트용: 답을 만드는 도중에 사용자가 기억을 지운 것처럼 지운다."""
        store.delete(USER)
        return "deleted"

    model = fake_model(
        tool_call("delete_now", {}),
        reply_message(
            "답",
            remember={"budget": {"amount": 30000, "currency": "JPY", "scope": None}},
        ),
    )
    run(graph_with(model, store, tools=[delete_now]), "예산 3만 엔이에요")

    assert store.get(USER) is None


def test_first_save_without_earlier_memory_still_works() -> None:
    store = new_store()
    model = fake_model(reply_message("답", remember={"avoid_needle": True}))
    run(graph_with(model, store), "바늘은 무서워요")

    assert store.get(USER).avoid_needle is True


def test_one_seoul_date_per_turn() -> None:
    # 턴이 서울 자정을 넘겨도 안내한 날짜로 "모레"를 계산한다.
    days = iter([date(2026, 11, 28), date(2026, 11, 29), date(2026, 11, 29)])
    store = new_store()
    model = fake_model(reply_message("답", remember={"return_in_days": 2}))
    graph = build_graph(
        model,
        checkpointer=InMemorySaver(),
        profile_store=store,
        today=lambda: next(days),
    )
    run(graph, "모레 귀국해요")

    assert "2026-11-28" in last_user_blocks(model.calls[0])[1]
    assert store.get(USER).return_date == date(2026, 11, 30)


def test_failed_read_keeps_note_state() -> None:
    class Flaky(InMemoryProfileStore):
        fail = False

        def get(self, user_id):
            if self.fail:
                raise RuntimeError("down")
            return super().get(user_id)

    store = Flaky(ttl=timedelta(days=30), max_users=10, now=lambda: SAVED_AT)
    store.update(USER, ProfilePatch(visitor="traveler"))
    model = fake_model(reply_message("1"), reply_message("2"), reply_message("3"))
    graph = graph_with(model, store)

    run(graph, "안녕")
    store.fail = True
    run(graph, "질문")
    store.fail = False
    run(graph, "또 질문")

    def has_note(call) -> bool:
        return any(
            b.startswith("[Note from Dopa app] Saved") for b in last_user_blocks(call)
        )

    assert [has_note(c) for c in model.calls] == [True, False, False]
