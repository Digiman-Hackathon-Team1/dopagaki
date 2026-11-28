"""지난 대화 조회 API(설계 문서 10장 대화 기록 불러오기)."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent.schemas import ThreadTurn
from api.agent.service import ThreadService, fallback_response, get_thread_service
from api.agent.threads import InMemoryThreadStore, thread_key
from api.main import app

USER = uuid4()
THREAD = uuid4()


@pytest.fixture
def store() -> Iterator[InMemoryThreadStore]:
    threads = InMemoryThreadStore(max_turns=50, max_threads=100)
    app.dependency_overrides[get_thread_service] = lambda: ThreadService(threads)
    yield threads
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: InMemoryThreadStore) -> TestClient:
    return TestClient(app)


def path(user=USER, thread=THREAD) -> str:
    return f"/api/agent/users/{user}/threads/{thread}"


def test_returns_stored_turns_oldest_first(
    client: TestClient, store: InMemoryThreadStore
) -> None:
    first = fallback_response(THREAD, "ko", "error")
    second = fallback_response(THREAD, "ja", "busy")
    store.append(thread_key(USER, THREAD), ThreadTurn(message="안녕", response=first))
    store.append(
        thread_key(USER, THREAD), ThreadTurn(message="こんにちは", response=second)
    )

    response = client.get(path())

    assert response.status_code == 200
    body = response.json()
    assert body["threadId"] == str(THREAD)
    assert [t["message"] for t in body["turns"]] == ["안녕", "こんにちは"]
    assert body["turns"][0]["response"] == first.model_dump(mode="json", by_alias=True)
    assert body["turns"][1]["response"]["fallback"] == "busy"


def test_unknown_thread_is_404(client: TestClient) -> None:
    assert client.get(path()).status_code == 404


def test_other_user_cannot_read_thread(
    client: TestClient, store: InMemoryThreadStore
) -> None:
    store.append(
        thread_key(USER, THREAD),
        ThreadTurn(message="안녕", response=fallback_response(THREAD, "ko", "error")),
    )

    assert client.get(path(user=uuid4())).status_code == 404
    assert client.get(path()).status_code == 200


def test_deleted_thread_is_404(client: TestClient, store: InMemoryThreadStore) -> None:
    key = thread_key(USER, THREAD)
    store.append(
        key,
        ThreadTurn(message="안녕", response=fallback_response(THREAD, "ko", "error")),
    )
    store.delete(key)

    assert client.get(path()).status_code == 404


def test_invalid_ids_are_422(client: TestClient) -> None:
    assert client.get("/api/agent/users/not-a-uuid/threads/x").status_code == 422
