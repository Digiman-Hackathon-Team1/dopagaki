"""프롬프트 캐싱의 표시 위치와 요청 앞부분 안정성(이슈 #59). 가짜 LLM을 쓰므로 API 키가 필요 없다.

캐시는 앞부분이 바이트 단위로 같을 때만 읽힌다. 그래서 호출마다 실제 ChatAnthropic 요청 본문을 만들어
(전송하지 않음) 도구 정의·시스템 프롬프트가 같고, 앞 호출의 메시지가 다음 호출 메시지의 앞부분인지 본다.
"""

from datetime import date, timedelta
from itertools import pairwise
from uuid import uuid4

from agent_fakes import fake_model, reply_message
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.config import AgentSettings
from api.agent.graph import build_graph
from api.agent.schemas import AgentReply
from api.agent.service import get_agent
from api.domain.profile import InMemoryProfileStore, ProfileBudget, ProfilePatch

EPHEMERAL = {"type": "ephemeral"}


@tool
def echo(text: str) -> str:
    """테스트용: 받은 문자열을 그대로 돌려준다."""
    return f"echo:{text}"


def run(graph, message: str, lang: str = "ko") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": lang},
        {"configurable": {"thread_id": "t1"}},
    )


def payload(messages: list[BaseMessage], prompt_cache: bool = True) -> dict:
    """그래프와 같은 방식으로 묶은 실제 ChatAnthropic의 요청 본문(전송하지 않음)."""
    llm = ChatAnthropic(model="claude-haiku-5-5", api_key="dummy", max_tokens=1000)
    cache = {"cache_control": EPHEMERAL} if prompt_cache else {}
    bound = llm.bind_tools([echo], response_format=AgentReply, **cache)
    return llm._get_request_payload(messages, **bound.kwargs)


def block_marks(payload: dict) -> list[dict]:
    """메시지 블록에 붙은 cache_control 목록."""
    return [
        block["cache_control"]
        for message in payload["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
        if "cache_control" in block
    ]


def test_prompt_cache_marks_system_end_and_whole_request() -> None:
    model = fake_model(reply_message("안녕하세요"))
    graph = build_graph(
        model, tools=[echo], checkpointer=InMemorySaver(), prompt_cache=True
    )

    run(graph, "안녕")

    system = model.calls[0][0]
    assert isinstance(system, SystemMessage)
    assert system.content[-1]["cache_control"] == EPHEMERAL
    assert model.call_kwargs[0]["cache_control"] == EPHEMERAL

    body = payload(model.calls[0])
    # 시스템 프롬프트 끝의 표시가 앞에 오는 도구 정의까지 캐시하고, 요청 전체 표시는 API가 마지막 블록에 붙인다.
    assert body["system"][-1]["cache_control"] == EPHEMERAL
    assert body["cache_control"] == EPHEMERAL
    assert [t["name"] for t in body["tools"]] == ["echo"]
    assert block_marks(body) == []


def test_prompt_cache_off_sends_no_marks() -> None:
    model = fake_model(reply_message("안녕하세요"))
    graph = build_graph(model, tools=[echo], checkpointer=InMemorySaver())

    run(graph, "안녕")

    assert model.calls[0][0].content.startswith("You are")
    assert "cache_control" not in model.call_kwargs[0]
    body = payload(model.calls[0], prompt_cache=False)
    assert "cache_control" not in body
    assert all("cache_control" not in block for block in body["system"])


def test_prompt_cache_marks_request_without_tools() -> None:
    model = fake_model(reply_message("안녕하세요"))
    graph = build_graph(model, checkpointer=InMemorySaver(), prompt_cache=True)

    run(graph, "안녕")

    assert model.call_kwargs[0]["cache_control"] == EPHEMERAL


def test_request_prefix_stays_stable_across_tool_loop_and_turns() -> None:
    """도구 호출 반복, 다음 턴, 응답 언어 변경, 건강 키워드 안내가 있어도 앞 호출의 요청이 다음 요청의 앞부분이다."""
    model = fake_model(
        AIMessage(
            content="", tool_calls=[{"name": "echo", "args": {"text": "a"}, "id": "c1"}]
        ),
        reply_message("첫 답"),
        reply_message("둘째 답"),
        AIMessage(
            content="", tool_calls=[{"name": "echo", "args": {"text": "b"}, "id": "c2"}]
        ),
        reply_message("셋째 답"),
    )
    graph = build_graph(
        model, tools=[echo], checkpointer=InMemorySaver(), prompt_cache=True
    )

    run(graph, "리쥬란 싼 곳", lang="ko")
    run(graph, "妊娠中です。大丈夫ですか", lang="ja")
    run(graph, "그럼 다른 시술은?", lang="ko")

    bodies = [payload(messages) for messages in model.calls]
    assert len(bodies) == 5
    for before, after in pairwise(bodies):
        assert after["tools"] == before["tools"]
        assert after["system"] == before["system"]
        assert after["messages"][: len(before["messages"])] == before["messages"]
        assert len(after["messages"]) > len(before["messages"])
    # 대화 기록 블록에는 표시를 직접 붙이지 않는다(표시는 시스템 끝과 요청 전체, 4개 한도 안).
    assert all(block_marks(body) == [] for body in bodies)


def test_profile_note_keeps_prefix_stable_and_same_for_every_user() -> None:
    """사용자 기억 안내와 remember 칸이 있어도 대화 안에서 앞 호출이 다음 호출의 앞부분이고, 도구·시스템·응답 형식은
    사용자마다 같다(ADR 0019). 기억 안내가 바뀐 뒤 다시 붙는 턴도 덧붙이기만 한다."""
    store = InMemoryProfileStore(ttl=timedelta(days=30), max_users=10)
    alice, bob = uuid4(), uuid4()
    store.update(
        alice, ProfilePatch(budget=ProfileBudget(amount=30000, currency="JPY"))
    )
    model = fake_model(
        AIMessage(
            content="", tool_calls=[{"name": "echo", "args": {"text": "a"}, "id": "c1"}]
        ),
        reply_message("첫 답"),
        reply_message("둘째 답", remember={"return_in_days": 2}),
        reply_message("셋째 답"),
        reply_message("다른 사용자 답"),
    )
    graph = build_graph(
        model,
        tools=[echo],
        checkpointer=InMemorySaver(),
        prompt_cache=True,
        profile_store=store,
        today=lambda: date(2026, 11, 28),
    )

    def turn(user, message: str, thread: str) -> None:
        graph.invoke(
            {"messages": [HumanMessage(message)], "lang": "ko"},
            {"configurable": {"thread_id": thread, "user_id": str(user)}},
        )

    turn(alice, "리쥬란 싼 곳", "a1")
    turn(alice, "모레 귀국해요", "a1")
    turn(alice, "그럼 다른 시술은?", "a1")
    turn(bob, "리쥬란 싼 곳", "b1")

    bodies = [payload(messages) for messages in model.calls]
    alice_bodies, bob_body = bodies[:4], bodies[4]
    for before, after in pairwise(alice_bodies):
        assert after["messages"][: len(before["messages"])] == before["messages"]
    # 귀국일이 저장돼 셋째 턴에 기억 안내가 다시 붙었다.
    third_user = alice_bodies[3]["messages"][-1]["content"]
    assert sum("Saved conditions" in b.get("text", "") for b in third_user) == 1
    for body in alice_bodies:
        for key in body.keys() - {"messages"}:
            assert body[key] == bob_body[key], key


def test_settings_switch_prompt_cache() -> None:
    # 개발자 .env의 값에 흔들리지 않게 .env를 읽지 않는다.
    on = AgentSettings(
        _env_file=None, anthropic_api_key="dummy", agent_prompt_cache=True
    )
    off = AgentSettings(
        _env_file=None, anthropic_api_key="dummy", agent_prompt_cache=False
    )

    assert AgentSettings(_env_file=None, anthropic_api_key="dummy").agent_prompt_cache
    assert get_agent(on) is not get_agent(off)
    assert get_agent(on) is get_agent(on)
