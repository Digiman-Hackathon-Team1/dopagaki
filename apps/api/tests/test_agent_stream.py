"""`POST /api/agent/chat/stream` SSE 응답. 가짜 LLM을 쓰므로 API 키가 필요 없다."""

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from agent_fakes import FailingChatModel, fake_model, reply_message, tool_call
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from api.agent import service as agent_service
from api.agent.config import AgentSettings, get_settings
from api.agent.graph import build_graph
from api.agent.service import FALLBACK_ANSWER, DailyUsage, get_agent
from api.agent.tools import TOOLS
from api.main import app

USER_ID = str(uuid4())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_graph(graph) -> None:
    app.dependency_overrides[get_agent] = lambda: graph


def stream(client: TestClient, message: str = "안녕", **body) -> list[dict]:
    """SSE 이벤트의 data를 순서대로 돌려준다."""
    with client.stream(
        "POST",
        "/api/agent/chat/stream",
        json={"userId": USER_ID, "message": message, "lang": "ko", **body},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in response.iter_lines() if line.startswith("data:")]
    return [json.loads(line.removeprefix("data:")) for line in lines]


def deltas(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "delta")


def test_stream_sends_start_deltas_then_done(client: TestClient) -> None:
    answer = "안녕하세요. 어떤 시술이 궁금하세요?\n편하게 물어보세요"
    use_graph(
        build_graph(fake_model(reply_message(answer)), checkpointer=InMemorySaver())
    )

    events = stream(client)

    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done"
    assert [e["type"] for e in events[1:-1]] == ["delta"] * (len(events) - 2)
    done = events[-1]["response"]
    assert done["threadId"] == events[0]["threadId"]
    assert done["answer"] == answer
    assert done["fallback"] is None
    # 문장·줄 단위로 나눠 보내고, 다음 칸이 시작되면 남은 글을 보낸다.
    sent = [e["text"] for e in events if e["type"] == "delta"]
    assert sent[0] == "안녕하세요."
    assert deltas(events) == answer


def test_stream_done_matches_chat_response(client: TestClient) -> None:
    use_graph(
        build_graph(
            fake_model(reply_message("A."), reply_message("A.")),
            checkpointer=InMemorySaver(),
        )
    )
    thread_id = str(uuid4())

    streamed = stream(client, threadId=thread_id)[-1]["response"]
    chat = client.post(
        "/api/agent/chat",
        json={
            "userId": str(uuid4()),
            "message": "안녕",
            "lang": "ko",
            "threadId": thread_id,
        },
    ).json()

    assert set(streamed) == set(chat)
    assert streamed["answer"] == chat["answer"]


def test_stream_skips_tool_call_steps(client: TestClient) -> None:
    model = fake_model(
        tool_call("resolve_location", {"query": "강남역"}),
        reply_message("강남역 근처로 찾았어요. 원하는 시술을 알려 주세요."),
    )
    use_graph(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()))

    events = stream(client, "강남역 근처")

    assert deltas(events) == "강남역 근처로 찾았어요. 원하는 시술을 알려 주세요."
    assert events[-1]["response"]["answer"] == deltas(events)


def test_stream_holds_back_sentences_with_unknown_amounts(client: TestClient) -> None:
    # 도구 결과에 없는 금액이 든 문장은 스트림에도 나오지 않는다(finalize와 같은 규칙).
    answer = "슈링크는 리프팅 시술이에요. 보통 99,000원 정도예요. 병원을 찾아볼까요?"
    use_graph(
        build_graph(fake_model(reply_message(answer)), checkpointer=InMemorySaver())
    )

    events = stream(client)

    assert "99,000" not in deltas(events)
    assert "99,000" not in events[-1]["response"]["answer"]
    assert events[-1]["response"]["answer"].startswith(deltas(events))


def test_stream_without_api_key_sends_fallback(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: AgentSettings(_env_file=None)

    events = stream(client)

    assert [e["type"] for e in events] == ["start", "done"]
    assert events[-1]["response"]["fallback"] == "error"
    assert events[-1]["response"]["answer"] == FALLBACK_ANSWER["error"]["ko"]


def test_stream_llm_error_sends_fallback(client: TestClient) -> None:
    model = FailingChatModel(messages=iter([reply_message("안녕")]), failures=5)
    use_graph(build_graph(model, checkpointer=InMemorySaver()))

    events = stream(client)

    assert events[-1]["type"] == "done"
    assert events[-1]["response"]["fallback"] == "error"


def test_stream_daily_limit_sends_fallback(client: TestClient) -> None:
    use_graph(
        build_graph(fake_model(reply_message("A.")), checkpointer=InMemorySaver())
    )
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        _env_file=None, anthropic_api_key="test-key", agent_user_daily_limit=0
    )

    events = stream(client)

    assert [e["type"] for e in events] == ["start", "done"]
    assert events[-1]["response"]["fallback"] == "daily_limit"


def test_stream_rejects_invalid_request(client: TestClient) -> None:
    response = client.post(
        "/api/agent/chat/stream", json={"userId": "x", "message": "", "lang": "en"}
    )

    assert response.status_code == 422


def test_stream_is_documented_as_event_stream() -> None:
    operation = app.openapi()["paths"]["/api/agent/chat/stream"]["post"]

    assert operation["operationId"] == "streamAgentChat"
    assert "text/event-stream" in operation["responses"]["200"]["content"]
