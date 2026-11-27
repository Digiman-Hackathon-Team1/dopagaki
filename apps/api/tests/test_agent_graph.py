"""가짜 LLM으로 그래프 흐름을 검사한다. API 키 없이 동작한다."""

import pytest
from agent_fakes import FailingChatModel, fake_model, reply_message, user_texts
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import build_graph, read_failure, read_reply


@tool
def echo(text: str) -> str:
    """테스트용: 받은 문자열을 그대로 돌려준다."""
    return f"echo:{text}"


@tool
def broken(text: str) -> str:
    """테스트용: 항상 실패하는 도구."""
    raise RuntimeError("data lookup failed")


def run(graph, message: str, thread_id: str = "t1", lang: str = "ko") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": lang},
        {"configurable": {"thread_id": thread_id}},
    )


def anthropic_payload(messages: list[BaseMessage]) -> dict:
    """가짜 LLM이 받은 메시지 목록을 실제 ChatAnthropic 요청 본문으로 바꿔 본다(전송하지 않음)."""
    llm = ChatAnthropic(model="claude-sonnet-5-5", api_key="dummy", max_tokens=1000)
    return llm._get_request_payload(messages)


def test_answer_without_tools() -> None:
    model = fake_model(reply_message("안녕하세요"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    result = run(graph, "안녕")

    assert read_reply(result).answer == "안녕하세요"
    assert read_reply(result).suggestions == ["a", "b", "c"]
    # 고정 시스템 프롬프트가 맨 앞, 응답 언어 지시는 사용자 메시지의 두 번째 블록이다.
    system, human = model.calls[0]
    assert isinstance(system, SystemMessage)
    assert human.content[0]["text"] == "안녕"
    assert "Korean" in human.content[1]["text"]


def test_tool_loop_runs_tool_then_answers() -> None:
    tool_call = AIMessage(
        content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]
    )
    model = fake_model(tool_call, reply_message("끝"))
    graph = build_graph(model, tools=[echo], checkpointer=InMemorySaver())

    result = run(graph, "도구 써 줘")

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.content for m in tool_messages] == ["echo:hi"]
    assert read_reply(result).answer == "끝"
    # 두 번째 LLM 호출에는 도구 결과가 들어 있고, 실제 요청 본문으로도 만들어진다.
    assert isinstance(model.calls[1][-1], ToolMessage)
    payload = anthropic_payload(model.calls[1])
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "user"]


@pytest.mark.parametrize(
    "call",
    [
        {"name": "nope", "args": {}, "id": "c1"},  # 없는 도구
        {"name": "broken", "args": {"text": "x"}, "id": "c1"},  # 도구 안에서 예외
    ],
)
def test_tool_errors_are_returned_to_model(call: dict) -> None:
    model = fake_model(AIMessage(content="", tool_calls=[call]), reply_message())
    graph = build_graph(model, tools=[echo, broken], checkpointer=InMemorySaver())

    result = run(graph, "x")

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert read_reply(result) is not None


def test_same_thread_keeps_previous_turns() -> None:
    model = fake_model(reply_message("첫 답"), reply_message("둘째 답"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    run(graph, "예산은 3만 엔이야", thread_id="t1")
    run(graph, "내 예산 얼마라고 했지?", thread_id="t1")

    assert user_texts(model.calls[1]) == ["예산은 3만 엔이야", "내 예산 얼마라고 했지?"]


def test_history_is_append_only_even_when_lang_changes() -> None:
    """앞 턴의 요청 앞부분이 바뀌면 Claude가 이전 thinking 블록을 거절하므로 덧붙이기만 한다."""
    model = fake_model(reply_message("첫 답"), reply_message("둘째 답"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    run(graph, "안녕", lang="ko")
    run(graph, "こんにちは", lang="ja")

    first, second = model.calls
    prefix = [(type(m), m.content) for m in first]
    assert [(type(m), m.content) for m in second[: len(first)]] == prefix
    assert "Japanese" in second[-1].content[1]["text"]


def test_failed_turn_does_not_break_next_turn() -> None:
    """LLM 호출이 실패해 답 없는 사용자 메시지가 남아도, 다음 턴 요청을 만들 수 있다."""
    model = FailingChatModel(messages=iter([reply_message("복구")]))
    graph = build_graph(model, checkpointer=InMemorySaver())

    failed = run(graph, "첫 시도")
    result = run(graph, "다시 시도")

    assert read_failure(failed) == "llm_error"
    assert read_failure(result) is None

    assert read_reply(result).answer == "복구"
    payload = anthropic_payload(model.calls[-1])
    # 시스템 메시지는 맨 앞 하나뿐이고, 대화 중간에는 user/assistant만 있다.
    assert all(m["role"] in ("user", "assistant") for m in payload["messages"])


def test_other_thread_starts_fresh() -> None:
    model = fake_model(reply_message(), reply_message())
    graph = build_graph(model, checkpointer=InMemorySaver())

    run(graph, "첫 대화", thread_id="t1")
    run(graph, "다른 대화", thread_id="t2")

    assert user_texts(model.calls[1]) == ["다른 대화"]


def test_invalid_reply_is_a_failure() -> None:
    model = fake_model(AIMessage(content="형식 없는 답"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    result = run(graph, "x")

    assert read_failure(result) == "invalid_reply"
    assert result["reply"] is None
