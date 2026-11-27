"""대화 기억 관리(설계 문서 8장): 같은 대화 요청 줄 세우기, 턴 상한, 보관 기한·대화 수 상한.

가짜 LLM을 쓰므로 API 키가 필요 없다.
"""

import json
import threading
from collections.abc import Iterator
from uuid import uuid4

import pytest
from agent_fakes import FakeChatModel, fake_model, reply_message, user_texts
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ConfigDict

from api.agent import service as agent_service
from api.agent.config import AgentSettings, get_settings
from api.agent.graph import build_graph
from api.agent.service import (
    FALLBACK_ANSWER,
    Conversations,
    DailyUsage,
    count_turns,
    get_agent,
)
from api.main import app

USER_ID = str(uuid4())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # 하루 한도와 대화 잠금은 서버 메모리에 쌓이므로 테스트마다 새로 둔다.
    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())
    monkeypatch.setattr(agent_service, "conversations", Conversations())
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_settings(**values) -> None:
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        _env_file=None, anthropic_api_key="test-key", **values
    )


class GatedChatModel(FakeChatModel):
    """첫 LLM 호출에서 gate가 열릴 때까지 멈추는 가짜 LLM. 앞 요청이 처리 중인 상황을 만든다."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    entered: threading.Event
    gate: threading.Event

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if not self.entered.is_set():
            self.entered.set()
            assert self.gate.wait(5), "테스트가 gate를 열지 않았다"
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def gated(*answers: str) -> tuple[GatedChatModel, InMemorySaver]:
    model = GatedChatModel(
        messages=iter(reply_message(a) for a in answers),
        entered=threading.Event(),
        gate=threading.Event(),
    )
    saver = InMemorySaver()
    app.dependency_overrides[get_agent] = lambda: build_graph(model, checkpointer=saver)
    return model, saver


def body(message: str, thread_id: str) -> dict:
    return {"userId": USER_ID, "threadId": thread_id, "message": message, "lang": "ko"}


def post_chat(client: TestClient, message: str, thread_id: str) -> dict:
    response = client.post("/api/agent/chat", json=body(message, thread_id))
    assert response.status_code == 200
    return response.json()


def post_stream(client: TestClient, message: str, thread_id: str) -> list[dict]:
    with client.stream(
        "POST", "/api/agent/chat/stream", json=body(message, thread_id)
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line.startswith("data:")]
    return [json.loads(line.removeprefix("data:")) for line in lines]


def run_in_thread(fn, *args) -> tuple[threading.Thread, list]:
    result: list = []
    thread = threading.Thread(target=lambda: result.append(fn(*args)))
    thread.start()
    return thread, result


def history(saver: InMemorySaver, thread_id: str) -> list[str]:
    config = {"configurable": {"thread_id": f"{USER_ID}:{thread_id}"}}
    return user_texts(saver.get_tuple(config).checkpoint["channel_values"]["messages"])


@pytest.mark.parametrize("first", [post_chat, post_stream])
def test_same_thread_requests_run_in_order(client: TestClient, first) -> None:
    model, saver = gated("첫 답", "둘째 답")
    thread_id = str(uuid4())

    one, _ = run_in_thread(first, client, "첫 질문", thread_id)
    assert model.entered.wait(5)
    two, second = run_in_thread(post_chat, client, "둘째 질문", thread_id)
    # 앞 요청이 LLM에서 멈춘 동안 둘째 요청은 LLM을 부르지 않고 기다린다.
    two.join(0.3)
    assert two.is_alive()
    # 첫 호출은 gate에서 멈춰 아직 기록 전이다. 둘째 요청이 LLM을 불렀다면 기록이 하나 생긴다.
    assert model.calls == []

    model.gate.set()
    one.join(5)
    two.join(5)

    assert second[0]["fallback"] is None
    assert second[0]["answer"] == "둘째 답"
    # 둘째 턴의 LLM 호출은 첫 턴을 본 뒤에 일어났고, 두 턴이 모두 대화 기록에 남는다.
    assert user_texts(model.calls[1]) == ["첫 질문", "둘째 질문"]
    assert history(saver, thread_id) == ["첫 질문", "둘째 질문"]


def test_stream_waits_for_previous_request(client: TestClient) -> None:
    model, saver = gated("첫 답", "둘째 답")
    thread_id = str(uuid4())

    one, _ = run_in_thread(post_chat, client, "첫 질문", thread_id)
    assert model.entered.wait(5)
    two, events = run_in_thread(post_stream, client, "둘째 질문", thread_id)
    two.join(0.3)
    assert two.is_alive()

    model.gate.set()
    one.join(5)
    two.join(5)

    assert [e["type"] for e in (events[0][0], events[0][-1])] == ["start", "done"]
    assert events[0][-1]["response"]["answer"] == "둘째 답"
    assert history(saver, thread_id) == ["첫 질문", "둘째 질문"]


@pytest.mark.parametrize("second", ["chat", "stream"])
def test_wait_timeout_returns_busy_fallback(client: TestClient, second: str) -> None:
    use_settings(agent_thread_wait_seconds=0.2)
    model, saver = gated("첫 답")
    thread_id = str(uuid4())

    one, _ = run_in_thread(post_chat, client, "첫 질문", thread_id)
    assert model.entered.wait(5)
    if second == "chat":
        response = post_chat(client, "둘째 질문", thread_id)
    else:
        events = post_stream(client, "둘째 질문", thread_id)
        assert events[0] == {"type": "start", "threadId": thread_id}
        response = events[-1]["response"]
    model.gate.set()
    one.join(5)

    assert response["fallback"] == "busy"
    assert response["answer"] == FALLBACK_ANSWER["busy"]["ko"]
    assert len(model.calls) == 1
    # 기다리다 돌아간 요청은 대화 기록에 남지 않는다.
    assert history(saver, thread_id) == ["첫 질문"]


def test_threads_of_different_conversations_do_not_wait(client: TestClient) -> None:
    use_settings(agent_thread_wait_seconds=0.2)
    model, _ = gated("첫 답", "다른 대화 답")

    one, _ = run_in_thread(post_chat, client, "첫 질문", str(uuid4()))
    assert model.entered.wait(5)
    other = post_chat(client, "다른 대화 질문", str(uuid4()))
    model.gate.set()
    one.join(5)

    assert other["fallback"] is None


@pytest.mark.parametrize("path", ["chat", "stream"])
def test_turn_limit_asks_for_new_conversation(client: TestClient, path: str) -> None:
    use_settings(agent_max_turns=2, agent_user_daily_limit=3)
    model = fake_model(reply_message("1"), reply_message("2"), reply_message("3"))
    saver = InMemorySaver()
    app.dependency_overrides[get_agent] = lambda: build_graph(model, checkpointer=saver)
    thread_id = str(uuid4())
    post_chat(client, "첫 질문", thread_id)
    post_chat(client, "둘째 질문", thread_id)

    if path == "chat":
        over = post_chat(client, "셋째 질문", thread_id)
    else:
        over = post_stream(client, "셋째 질문", thread_id)[-1]["response"]

    assert over["fallback"] == "turn_limit"
    assert over["answer"] == FALLBACK_ANSWER["turn_limit"]["ko"]
    # LLM을 부르지 않고, 대화 기록과 하루 한도에도 남기지 않는다.
    assert len(model.calls) == 2
    assert history(saver, thread_id) == ["첫 질문", "둘째 질문"]
    assert post_chat(client, "다른 대화", str(uuid4()))["fallback"] is None


def test_count_turns_counts_user_messages_only() -> None:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages = [
        HumanMessage("a"),
        AIMessage(""),
        ToolMessage("r", tool_call_id="c"),
        AIMessage("b"),
        HumanMessage("c"),
    ]

    assert count_turns(messages) == 2


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def finish(conversations: Conversations, key: str, deleted: list, **limits) -> None:
    assert conversations.acquire(key, timeout=1)
    conversations.release(
        key,
        deleted.append,
        limits.get("ttl", 100),
        limits.get("max_threads", 10),
    )


def test_conversations_delete_threads_idle_past_ttl() -> None:
    clock = Clock()
    conversations = Conversations(clock=clock)
    deleted: list[str] = []
    finish(conversations, "a", deleted)
    clock.now = 50
    finish(conversations, "b", deleted)

    clock.now = 120
    finish(conversations, "c", deleted)
    # a는 마지막 요청 뒤 120초, b는 70초가 지났다. 보관 시간 100초를 넘은 a만 지운다.
    assert deleted == ["a"]

    clock.now = 151
    finish(conversations, "b", deleted)
    assert deleted == ["a"]

    clock.now = 252
    finish(conversations, "c", deleted)
    assert deleted == ["a", "b"]


def test_conversations_delete_oldest_over_limit() -> None:
    clock = Clock()
    conversations = Conversations(clock=clock)
    deleted: list[str] = []
    for i, key in enumerate(["a", "b", "c"]):
        clock.now = i
        finish(conversations, key, deleted, max_threads=3)
    # 오래된 대화에 다시 요청이 오면 가장 최근 대화가 된다.
    clock.now = 3
    finish(conversations, "a", deleted, max_threads=3)

    clock.now = 4
    finish(conversations, "d", deleted, max_threads=3)

    assert deleted == ["b"]


def test_conversations_keep_threads_in_use() -> None:
    clock = Clock()
    conversations = Conversations(clock=clock)
    deleted: list[str] = []
    finish(conversations, "a", deleted, max_threads=1)
    # a를 처리하는 중에 b가 끝나 상한(1개)을 넘어도, 처리 중인 a는 지우지 않는다.
    assert conversations.acquire("a", timeout=1)
    clock.now = 500
    finish(conversations, "b", deleted, max_threads=1, ttl=100)
    assert deleted == []

    conversations.release("a", deleted.append, 100, 1)
    assert deleted == ["b"]


def test_expired_thread_is_removed_from_memory(client: TestClient, monkeypatch) -> None:
    clock = Clock()
    monkeypatch.setattr(agent_service, "conversations", Conversations(clock=clock))
    use_settings(agent_thread_ttl_seconds=100)
    model = fake_model(reply_message("1"), reply_message("2"))
    saver = InMemorySaver()
    app.dependency_overrides[get_agent] = lambda: build_graph(model, checkpointer=saver)
    old, new = str(uuid4()), str(uuid4())
    post_chat(client, "오래된 대화", old)

    clock.now = 101
    post_chat(client, "새 대화", new)

    assert saver.get_tuple({"configurable": {"thread_id": f"{USER_ID}:{old}"}}) is None
    assert history(saver, new) == ["새 대화"]


def test_error_after_lock_releases_it(client: TestClient, monkeypatch) -> None:
    model = fake_model(reply_message("다음 답"))
    saver = InMemorySaver()
    app.dependency_overrides[get_agent] = lambda: build_graph(model, checkpointer=saver)
    thread_id = str(uuid4())

    def broken(*args) -> bool:
        raise RuntimeError("하루 한도 저장소 오류")

    monkeypatch.setattr(agent_service.daily_usage, "take", broken)
    failed = post_chat(client, "첫 질문", thread_id)
    monkeypatch.undo()
    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())

    assert failed["fallback"] == "error"
    # 잠금이 풀려 같은 대화의 다음 요청이 기다리지 않고 처리된다.
    use_settings(agent_thread_wait_seconds=0.2)
    assert post_chat(client, "다시", thread_id)["fallback"] is None


def test_busy_reply_is_not_counted_in_daily_limit(client: TestClient) -> None:
    use_settings(agent_thread_wait_seconds=0.2, agent_user_daily_limit=2)
    model, _ = gated("첫 답", "다음 답")
    thread_id = str(uuid4())

    one, _ = run_in_thread(post_chat, client, "첫 질문", thread_id)
    assert model.entered.wait(5)
    assert post_chat(client, "둘째 질문", thread_id)["fallback"] == "busy"
    model.gate.set()
    one.join(5)

    # 하루 한도 2회 중 첫 질문만 셌으므로 한 번 더 답한다.
    assert post_chat(client, "셋째 질문", thread_id)["fallback"] is None


def test_rejected_requests_do_not_push_out_stored_threads(
    client: TestClient,
) -> None:
    use_settings(agent_max_threads=1, agent_user_daily_limit=1)
    model = fake_model(reply_message("답"))
    saver = InMemorySaver()
    app.dependency_overrides[get_agent] = lambda: build_graph(model, checkpointer=saver)
    kept = str(uuid4())
    post_chat(client, "저장된 대화", kept)

    # 하루 한도를 넘긴 요청은 대화 기억에 남지 않으므로 상한(1개)을 차지하지 않는다.
    for _ in range(3):
        assert post_chat(client, "새 대화", str(uuid4()))["fallback"] == "daily_limit"

    assert history(saver, kept) == ["저장된 대화"]


def test_waiting_request_keeps_thread() -> None:
    clock = Clock()
    conversations = Conversations(clock=clock)
    deleted: list[str] = []
    finish(conversations, "a", deleted)
    assert conversations.acquire("a", timeout=1)
    waiter, got = run_in_thread(conversations.acquire, "a", 5)
    # 잠금을 잡은 요청이 끝나도 기다리는 요청이 있으면 a를 지우지 않는다.
    clock.now = 500
    conversations.release("a", deleted.append, 100, 10)
    waiter.join(5)
    assert got == [True]
    # a는 보관 시간(100초)을 넘겼지만, 잠금을 이어받은 요청이 처리 중이라 지우지 않는다.
    clock.now = 700
    finish(conversations, "b", deleted, ttl=100)
    assert deleted == []


def test_third_request_does_not_wait(client: TestClient) -> None:
    model, _ = gated("첫 답", "둘째 답")
    thread_id = str(uuid4())

    one, _ = run_in_thread(post_chat, client, "첫 질문", thread_id)
    assert model.entered.wait(5)
    two, second = run_in_thread(post_chat, client, "둘째 질문", thread_id)
    two.join(0.3)
    # 처리 중인 요청과 기다리는 요청이 이미 있으면, 기본 대기(60초)를 쓰지 않고 바로 busy로 답한다.
    third = post_chat(client, "셋째 질문", thread_id)
    model.gate.set()
    one.join(5)
    two.join(5)

    assert third["fallback"] == "busy"
    assert second[0]["answer"] == "둘째 답"


def test_disconnected_stream_releases_lock(monkeypatch) -> None:
    import asyncio
    import gc

    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())
    monkeypatch.setattr(agent_service, "conversations", Conversations())
    # 문장이 끝나 delta가 나가는 답. 끊긴 뒤 스트림이 delta를 낸 자리에서 멈춘다.
    model, _ = gated("첫 답. 이어지는 글")
    thread_id = str(uuid4())
    key = f"{USER_ID}:{thread_id}"
    payload = json.dumps(body("첫 질문", thread_id)).encode()

    async def run() -> None:
        disconnected = asyncio.Event()
        started = asyncio.Event()
        received = False

        async def receive() -> dict:
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if b'"start"' in message.get("body", b""):
                started.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/agent/chat/stream",
            "raw_path": b"/api/agent/chat/stream",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("test", 1),
            "server": ("test", 80),
        }
        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.to_thread(model.entered.wait, 5)
        # LLM이 답을 만드는 도중에 연결이 끊기고, LLM은 그 뒤에 끝난다.
        disconnected.set()
        asyncio.get_running_loop().call_later(0.3, model.gate.set)
        await asyncio.wait_for(task, 5)
        # 끊긴 뒤에도 그래프 실행은 스레드에서 끝까지 간다. 그 단계가 끝날 시간을 준다.
        await asyncio.sleep(0.5)
        # 이벤트 루프가 끝나면 남은 객체가 정리되며 풀릴 수 있어, 루프 안에서 확인한다.
        released.append(agent_service.conversations.acquire(key, timeout=0))

    released: list[bool] = []
    gc.disable()
    try:
        asyncio.run(run())
        # 가비지 컬렉터를 기다리지 않고, 요청이 끝날 때 잠금이 풀린다.
        assert released == [True]
    finally:
        gc.enable()
        app.dependency_overrides.clear()
