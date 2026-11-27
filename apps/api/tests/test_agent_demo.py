"""데모 응답 캐싱(설계 문서 13장 데모 고정). 가짜 LLM으로 녹화하고, 녹화본으로 답하는지 본다."""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from agent_fakes import fake_model, reply_message, tool_call, user_texts
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, messages_to_dict
from langgraph.checkpoint.memory import InMemorySaver

from api.agent import router as agent_router
from api.agent.config import AgentSettings, get_settings
from api.agent.demo import (
    DemoFile,
    DemoScript,
    DemoSessions,
    DemoTurn,
    dump_demo,
    load_demo,
    normalize,
)
from api.agent.graph import build_graph
from api.agent.router import DailyUsage, get_agent
from api.agent.tools import TOOLS
from api.main import app

SCRIPT = ("ハイフを受けたいです", "1番目のクリニックの予約方法を教えて")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(agent_router, "daily_usage", DailyUsage())
    monkeypatch.setattr(agent_router, "demo_sessions", DemoSessions())
    yield TestClient(app)
    app.dependency_overrides.clear()


def use(graph, *, demo: bool, key: str | None = "test-key") -> None:
    settings = AgentSettings(
        _env_file=None, anthropic_api_key=key, agent_demo_cache=demo
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_agent] = lambda: graph


def post(client: TestClient, message: str, user_id: str, thread_id=None) -> dict:
    response = client.post(
        "/api/agent/chat",
        json={
            "userId": user_id,
            "threadId": thread_id,
            "message": message,
            "lang": "ja",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def with_thinking(message: AIMessage) -> AIMessage:
    """실제 Claude 응답처럼 thinking 블록을 앞에 붙인다. 도구 호출은 [thinking, tool_use]로 온다."""
    thinking = {"type": "thinking", "thinking": "…", "signature": "sig"}
    if message.tool_calls:
        blocks = [
            {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
            for c in message.tool_calls
        ]
    else:
        blocks = [{"type": "text", "text": message.text}]
    return message.model_copy(update={"content": [thinking, *blocks]})


def record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> DemoFile:
    """scripts/record_demo.py와 같은 방식으로 가짜 LLM 응답을 녹화한다."""
    graph = build_graph(
        fake_model(
            with_thinking(tool_call("search_clinics", {"menu_id": "shrink-300"}, "c1")),
            with_thinking(reply_message("シュリンクの上位3院です")),
            with_thinking(reply_message("LINEでお問い合わせください")),
        ),
        tools=TOOLS,
        checkpointer=InMemorySaver(),
    )
    use(graph, demo=False)
    user_id = str(uuid4())
    thread_id = None
    seen = 0
    turns = []
    for message in SCRIPT:
        body = post(client, message, user_id, thread_id)
        thread_id = body["threadId"]
        state = graph.get_state(
            {"configurable": {"thread_id": f"{user_id}:{thread_id}"}}
        )
        messages = state.values["messages"]
        turns.append(
            DemoTurn(
                message=message,
                response={k: v for k, v in body.items() if k != "threadId"},
                messages=messages_to_dict(messages[seen:]),
            )
        )
        seen = len(messages)
    demo = DemoFile(
        scripts=[
            DemoScript(
                id="hifu-ja",
                lang="ja",
                recorded_at="2026-11-27T00:00:00+00:00",
                model="fake",
                turns=turns,
            )
        ]
    )
    # 녹화 스크립트처럼 파일로 저장했다가 다시 읽어 JSON 왕복(도구 결과 포함)도 거친다.
    path = tmp_path / "demo-replies.json"
    dump_demo(demo, path)
    loaded = load_demo(path)
    monkeypatch.setattr(agent_router, "load_demo", lambda: loaded)
    return loaded


def test_script_turns_are_answered_from_recording_without_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = record(client, monkeypatch, tmp_path)
    recorded_usage = agent_router.daily_usage._total
    model = fake_model()  # 부르면 응답이 없어 실패한다
    use(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()), demo=True)
    user_id = str(uuid4())

    first = post(client, SCRIPT[0], user_id)
    second = post(client, SCRIPT[1], user_id, first["threadId"])

    assert model.calls == []
    assert first["answer"] == "シュリンクの上位3院です"
    assert [c["kind"] for c in first["cards"]] == ["recommendation"] * 3
    assert second["answer"] == "LINEでお問い合わせください"
    assert second["threadId"] == first["threadId"]
    recorded = demo.scripts[0].turns[0].response
    assert {k: v for k, v in first.items() if k != "threadId"} == recorded
    # 녹화 응답은 하루 한도에 세지 않는다.
    assert agent_router.daily_usage._total == recorded_usage


def test_leaving_the_script_continues_with_claude_and_recorded_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record(client, monkeypatch, tmp_path)
    model = fake_model(reply_message("江南の病院も探せます"))
    use(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()), demo=True)
    user_id = str(uuid4())

    first = post(client, SCRIPT[0], user_id)
    live = post(client, "江南の病院はありますか", user_id, first["threadId"])
    # 한 번 벗어난 대화는 대본 메시지가 와도 실제 Claude가 답한다.
    model.messages = iter([reply_message("予約はLINEです")])
    again = post(client, SCRIPT[1], user_id, first["threadId"])

    assert live["answer"] == "江南の病院も探せます"
    assert again["answer"] == "予約はLINEです"
    [request, _] = model.calls
    # 녹화한 앞 턴(사용자 메시지, 도구 호출과 결과)을 이어받고, thinking 블록은 빠진다.
    assert user_texts(request) == [SCRIPT[0], "江南の病院はありますか"]
    assert any(m.type == "tool" and m.name == "search_clinics" for m in request)
    for m in request:
        if isinstance(m, AIMessage) and isinstance(m.content, list):
            assert all(b.get("type") != "thinking" for b in m.content)


def test_demo_answers_without_api_key_and_falls_back_off_script(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record(client, monkeypatch, tmp_path)
    use(None, demo=True, key=None)
    user_id = str(uuid4())

    first = post(client, SCRIPT[0], user_id)
    off = post(client, "江南の病院はありますか", user_id, first["threadId"])

    assert first["fallback"] is None
    assert first["answer"] == "シュリンクの上位3院です"
    assert off["fallback"] == "error"


def test_demo_is_off_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record(client, monkeypatch, tmp_path)
    model = fake_model(reply_message("実際の答え"))
    use(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()), demo=False)

    body = post(client, SCRIPT[0], str(uuid4()))

    assert body["answer"] == "実際の答え"


def test_existing_conversation_is_not_taken_over_by_the_script(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record(client, monkeypatch, tmp_path)
    model = fake_model(reply_message("こんにちは"), reply_message("実際の答え"))
    use(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()), demo=True)
    user_id = str(uuid4())

    first = post(client, "こんにちは", user_id)
    body = post(client, SCRIPT[0], user_id, first["threadId"])

    assert body["answer"] == "実際の答え"


def test_new_thread_id_from_client_still_starts_the_script(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record(client, monkeypatch, tmp_path)
    model = fake_model()
    use(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()), demo=True)

    body = post(client, SCRIPT[0], str(uuid4()), str(uuid4()))

    assert body["answer"] == "シュリンクの上位3院です"
    assert model.calls == []


@pytest.mark.parametrize(
    "typed",
    ["ハイフを受けたいです。", " ハイフを受けたいです？ ", "ﾊｲﾌを受けたいです！"],
)
def test_script_match_ignores_spacing_width_and_trailing_marks(typed: str) -> None:
    assert normalize(typed) == normalize(SCRIPT[0])


def test_broken_demo_file_does_not_break_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "demo-replies.json"
    path.write_text('{"scripts": [{"id": "x"}]}')
    monkeypatch.setattr(agent_router, "load_demo", lambda: load_demo(path))
    model = fake_model(reply_message("実際の答え"))
    use(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()), demo=True)

    body = post(client, SCRIPT[0], str(uuid4()))

    assert body["fallback"] is None
    assert body["answer"] == "実際の答え"


def test_demo_sessions_forget_oldest_threads_over_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DemoSessions, "MAX_THREADS", 2)
    sessions = DemoSessions()
    demo = DemoFile()

    for key in ("a", "b", "c"):
        sessions.take(demo, key, True, "hi", "ja")

    assert list(sessions._threads) == ["b", "c"]
