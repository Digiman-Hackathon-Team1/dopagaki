"""가짜 LLM으로 그래프 흐름을 검사한다. API 키 없이 동작한다."""

import pytest
from agent_fakes import fake_model, reply_message
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.graph import AgentOutputError, build_graph, read_reply


@tool
def echo(text: str) -> str:
    """테스트용: 받은 문자열을 그대로 돌려준다."""
    return f"echo:{text}"


def run(graph, message: str, thread_id: str = "t1") -> dict:
    return graph.invoke(
        {"messages": [HumanMessage(message)], "lang": "ko"},
        {"configurable": {"thread_id": thread_id}},
    )


def test_answer_without_tools() -> None:
    model = fake_model(reply_message("안녕하세요"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    result = run(graph, "안녕")

    assert read_reply(result).answer == "안녕하세요"
    assert read_reply(result).suggestions == ["a", "b", "c"]
    # 고정 시스템 프롬프트가 맨 앞, 응답 언어 지시는 사용자 메시지 뒤에 붙는다.
    first_call = model.calls[0]
    assert isinstance(first_call[0], SystemMessage)
    assert isinstance(first_call[1], HumanMessage)
    assert isinstance(first_call[2], SystemMessage)
    assert "Korean" in first_call[2].content


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
    # 두 번째 LLM 호출에는 도구 결과가 들어 있다.
    assert isinstance(model.calls[1][-1], ToolMessage)


def test_unknown_tool_returns_error_to_model() -> None:
    tool_call = AIMessage(
        content="", tool_calls=[{"name": "nope", "args": {}, "id": "c1"}]
    )
    model = fake_model(tool_call, reply_message())
    graph = build_graph(model, tools=[echo], checkpointer=InMemorySaver())

    result = run(graph, "x")

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert read_reply(result) is not None


def test_same_thread_keeps_previous_turns() -> None:
    model = fake_model(reply_message("첫 답"), reply_message("둘째 답"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    run(graph, "예산은 3만 엔이야", thread_id="t1")
    run(graph, "내 예산 얼마라고 했지?", thread_id="t1")

    second_call = model.calls[1]
    contents = [m.content for m in second_call if isinstance(m, HumanMessage)]
    assert contents == ["예산은 3만 엔이야", "내 예산 얼마라고 했지?"]


def test_history_is_append_only_even_when_lang_changes() -> None:
    """앞 턴의 요청 앞부분이 바뀌면 Claude가 이전 thinking 블록을 거절하므로 덧붙이기만 한다."""
    model = fake_model(reply_message("첫 답"), reply_message("둘째 답"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    graph.invoke(
        {"messages": [HumanMessage("안녕")], "lang": "ko"},
        {"configurable": {"thread_id": "t1"}},
    )
    graph.invoke(
        {"messages": [HumanMessage("こんにちは")], "lang": "ja"},
        {"configurable": {"thread_id": "t1"}},
    )

    first, second = model.calls
    prefix = [(type(m), m.content) for m in first]
    assert [(type(m), m.content) for m in second[: len(first)]] == prefix
    assert "Japanese" in second[-1].content


def test_other_thread_starts_fresh() -> None:
    model = fake_model(reply_message(), reply_message())
    graph = build_graph(model, checkpointer=InMemorySaver())

    run(graph, "첫 대화", thread_id="t1")
    run(graph, "다른 대화", thread_id="t2")

    contents = [m.content for m in model.calls[1] if isinstance(m, HumanMessage)]
    assert contents == ["다른 대화"]


def test_invalid_reply_raises() -> None:
    model = fake_model(AIMessage(content="형식 없는 답"))
    graph = build_graph(model, checkpointer=InMemorySaver())

    with pytest.raises(AgentOutputError):
        run(graph, "x")
