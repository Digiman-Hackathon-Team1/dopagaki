"""턴이 끝날 때 대화 턴 보관소에 넣고, 대화 기억과 같이 지우기(#94, 설계 문서 10장).

가짜 LLM을 쓰므로 API 키가 필요 없다.
"""

import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from agent_fakes import fake_model, reply_message
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from api.agent import service as agent_service
from api.agent.config import AgentSettings, get_settings
from api.agent.graph import build_graph
from api.agent.service import (
    Conversations,
    DailyUsage,
    ThreadService,
    get_agent,
    get_thread_service,
)
from api.agent.threads import InMemoryThreadStore
from api.main import app

USER_ID = str(uuid4())


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryThreadStore]:
    # 대화 기억·하루 한도·보관소는 서버 메모리에 쌓이므로 테스트마다 새로 둔다.
    threads = InMemoryThreadStore(max_turns=50, max_threads=1000)
    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())
    monkeypatch.setattr(agent_service, "conversations", Conversations())
    monkeypatch.setattr(agent_service, "get_thread_store", lambda: threads)
    app.dependency_overrides[get_thread_service] = lambda: ThreadService(threads)
    use_settings()
    yield threads
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: InMemoryThreadStore) -> TestClient:
    return TestClient(app)


def use_settings(**values) -> None:
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        _env_file=None, anthropic_api_key="test-key", **values
    )


def use_agent(*answers: str) -> InMemorySaver:
    saver = InMemorySaver()
    model = fake_model(*(reply_message(a) for a in answers))
    app.dependency_overrides[get_agent] = lambda: build_graph(model, checkpointer=saver)
    return saver


def body(message: str, thread_id: str) -> dict:
    return {"userId": USER_ID, "threadId": thread_id, "message": message, "lang": "ko"}


def post_chat(client: TestClient, message: str, thread_id: str) -> dict:
    response = client.post("/api/agent/chat", json=body(message, thread_id))
    assert response.status_code == 200
    return response.json()


def post_stream(client: TestClient, message: str, thread_id: str) -> dict:
    with client.stream(
        "POST", "/api/agent/chat/stream", json=body(message, thread_id)
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line.removeprefix("data:"))
            for line in response.iter_lines()
            if line.startswith("data:")
        ]
    return next(e["response"] for e in events if e["type"] == "done")


def get_thread(client: TestClient, thread_id: str, user_id: str = USER_ID):
    return client.get(f"/api/agent/users/{user_id}/threads/{thread_id}")


def test_chat_and_stream_turns_are_restored_in_order(client: TestClient) -> None:
    use_agent("첫 답", "둘째 답")
    thread_id = str(uuid4())

    first = post_chat(client, "첫 질문", thread_id)
    second = post_stream(client, "둘째 질문", thread_id)

    response = get_thread(client, thread_id)
    assert response.status_code == 200
    turns = response.json()["turns"]
    assert [t["message"] for t in turns] == ["첫 질문", "둘째 질문"]
    # 화면이 그때 받은 응답 그대로다.
    assert [t["response"] for t in turns] == [first, second]
    assert second["answer"].startswith("둘째 답")


def test_request_not_kept_in_memory_is_not_stored(client: TestClient) -> None:
    # 첫 턴부터 하루 한도에 걸리면 대화 기억에 남지 않으므로 보관소에도 넣지 않는다.
    use_settings(agent_user_daily_limit=0)
    use_agent("쓰이지 않는 답")
    thread_id = str(uuid4())

    assert post_chat(client, "첫 질문", thread_id)["fallback"] == "daily_limit"

    assert get_thread(client, thread_id).status_code == 404


def test_other_user_cannot_read_the_thread(client: TestClient) -> None:
    use_agent("답")
    thread_id = str(uuid4())
    post_chat(client, "질문", thread_id)

    assert get_thread(client, thread_id, user_id=str(uuid4())).status_code == 404


def test_thread_expired_from_memory_is_removed_from_store(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(agent_service, "conversations", Conversations(clock=clock))
    use_settings(agent_thread_ttl_seconds=100)
    saver = use_agent("1", "2")
    old, new = str(uuid4()), str(uuid4())
    post_chat(client, "오래된 대화", old)

    clock.now = 101
    post_chat(client, "새 대화", new)

    # 대화 기억이 지운 대화는 보관소에도 없어, 화면이 다시 그리는 대화와 Claude가 기억하는 대화가 같다.
    assert saver.get_tuple({"configurable": {"thread_id": f"{USER_ID}:{old}"}}) is None
    assert get_thread(client, old).status_code == 404
    assert get_thread(client, new).status_code == 200
