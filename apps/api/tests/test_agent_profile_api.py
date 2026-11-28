"""사용자 기억 보기·지우기 API(설계 문서 10장, ADR 0019)와, /chat을 거쳐 저장·불러오기·지우기가 이어지는지."""

from collections.abc import Iterator
from datetime import timedelta
from uuid import uuid4

import pytest
from agent_fakes import fake_model, reply_message
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent import service as agent_service
from api.agent.config import AgentSettings, get_settings
from api.agent.graph import build_graph
from api.agent.service import (
    Conversations,
    DailyUsage,
    ProfileService,
    get_agent,
    get_profile_service,
)
from api.domain.profile import InMemoryProfileStore, ProfileBudget, ProfilePatch
from api.main import app

USER = uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryProfileStore]:
    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())
    monkeypatch.setattr(agent_service, "conversations", Conversations())
    profiles = InMemoryProfileStore(ttl=timedelta(days=30), max_users=100)
    app.dependency_overrides[get_profile_service] = lambda: ProfileService(profiles)
    yield profiles
    app.dependency_overrides.clear()


@pytest.fixture
def client(store: InMemoryProfileStore) -> TestClient:
    return TestClient(app)


def path(user=USER) -> str:
    return f"/api/agent/users/{user}/profile"


def test_get_empty_profile_returns_nulls(client: TestClient) -> None:
    response = client.get(path())

    assert response.status_code == 200
    assert response.json() == {
        "visitor": None,
        "budget": None,
        "arrivalDate": None,
        "returnDate": None,
        "location": None,
        "concerns": [],
        "avoidNeedle": None,
        "jpTreatments": [],
        "updatedAt": None,
    }


def test_get_profile_camel_case(
    client: TestClient, store: InMemoryProfileStore
) -> None:
    store.update(
        USER,
        ProfilePatch(
            budget=ProfileBudget(amount=30000, currency="JPY", scope="per_treatment"),
            location_id="area:hongdae",
            jp_treatments=("ハイフ",),
            avoid_needle=True,
        ),
    )

    body = client.get(path()).json()

    assert body["budget"] == {
        "amount": 30000,
        "currency": "JPY",
        "scope": "per_treatment",
    }
    assert body["location"] == {
        "locationId": "area:hongdae",
        "nameJa": "弘大",
        "nameKo": "홍대",
    }
    assert body["jpTreatments"] == ["ハイフ"]
    assert body["avoidNeedle"] is True
    assert body["updatedAt"] is not None


def test_delete_returns_204_and_clears(
    client: TestClient, store: InMemoryProfileStore
) -> None:
    store.update(USER, ProfilePatch(visitor="traveler"))

    response = client.delete(path())

    assert response.status_code == 204
    assert response.content == b""
    assert store.get(USER) is None
    assert client.get(path()).json()["visitor"] is None


def test_delete_missing_returns_204(client: TestClient) -> None:
    assert client.delete(path(uuid4())).status_code == 204


@pytest.mark.parametrize("method", ["get", "delete"])
def test_invalid_user_id_returns_422(client: TestClient, method: str) -> None:
    response = getattr(client, method)("/api/agent/users/not-a-uuid/profile")

    assert response.status_code == 422


def chat(client: TestClient, message: str, thread_id=None) -> dict:
    body = {"userId": str(USER), "message": message, "lang": "ko"}
    if thread_id:
        body["threadId"] = thread_id
    response = client.post("/api/agent/chat", json=body)
    assert response.status_code == 200
    return response.json()


def saved_note_in(call) -> bool:
    human = [m for m in call if isinstance(m, HumanMessage)][-1]
    return any(
        b["text"].startswith("[Note from Dopa app] Saved conditions")
        for b in human.content
    )


def use_graph(model, store, **settings) -> None:
    graph = build_graph(model, checkpointer=InMemorySaver(), profile_store=store)
    app.dependency_overrides[get_agent] = lambda: graph
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        _env_file=None, anthropic_api_key="test-key", **settings
    )


def test_saved_in_one_thread_loaded_in_new_thread_and_gone_after_delete(
    client: TestClient, store: InMemoryProfileStore
) -> None:
    model = fake_model(
        reply_message(
            "알겠어요",
            remember={"budget": {"amount": 30000, "currency": "JPY", "scope": None}},
        ),
        reply_message("3만 엔 기준으로 찾았어요"),
        reply_message("조건을 알려 주세요"),
    )
    use_graph(model, store)

    first = chat(client, "예산은 3만 엔이에요")
    assert "remember" not in first
    second = chat(client, "리쥬란 싼 곳")  # 새 대화
    assert client.delete(path()).status_code == 204
    chat(client, "리쥬란 싼 곳")  # 지운 뒤 새 대화

    assert [saved_note_in(c) for c in model.calls] == [False, True, False]
    assert second["answer"] == "3만 엔 기준으로 찾았어요"


def test_turn_limit_new_thread_keeps_profile(
    client: TestClient, store: InMemoryProfileStore
) -> None:
    model = fake_model(
        reply_message("알겠어요", remember={"visitor": "resident"}),
        reply_message("새 대화 답"),
    )
    use_graph(model, store, agent_max_turns=1)

    first = chat(client, "서울에 살아요")
    over = chat(client, "또 질문", thread_id=first["threadId"])
    chat(client, "리쥬란 싼 곳")

    assert over["fallback"] == "turn_limit"
    assert len(model.calls) == 2
    assert saved_note_in(model.calls[1])
    assert store.get(USER).visitor == "resident"
