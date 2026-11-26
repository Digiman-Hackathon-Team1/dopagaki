"""`POST /api/agent/chat` 정상·오류 응답. 가짜 LLM을 쓰므로 API 키가 필요 없다."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from agent_fakes import FakeChatModel, fake_model, reply_message, tool_call, user_texts
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from api.agent.config import AgentSettings, get_settings
from api.agent.graph import build_graph
from api.agent.router import get_agent
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
from api.main import app

USER_ID = str(uuid4())


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_fake(*answers: str) -> FakeChatModel:
    model = fake_model(*(reply_message(a) for a in answers))
    graph = build_graph(model, checkpointer=InMemorySaver())
    app.dependency_overrides[get_agent] = lambda: graph
    return model


def test_chat_returns_reply_slots_in_camel_case(client: TestClient) -> None:
    use_fake("안녕하세요")

    response = client.post(
        "/api/agent/chat", json={"userId": USER_ID, "message": "안녕", "lang": "ko"}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "threadId",
        "conditionSummary",
        "answer",
        "cards",
        "questions",
        "suggestions",
        "cautions",
        "disclaimer",
    }
    assert body["answer"] == "안녕하세요"
    assert body["disclaimer"].startswith("AI 답변은")


def test_chat_returns_recommendation_cards(client: TestClient) -> None:
    top = rank_rows(get_catalog(), "rejuran-2cc")[0]
    card_id = f"{top.place.id}:rejuran-2cc"
    model = fake_model(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc"}),
        reply_message("가장 싼 곳이에요", {card_id: "가장 싸요"}),
    )
    graph = build_graph(model, tools=TOOLS, checkpointer=InMemorySaver())
    app.dependency_overrides[get_agent] = lambda: graph

    response = client.post(
        "/api/agent/chat",
        json={"userId": USER_ID, "message": "리쥬란 싼 곳", "lang": "ko"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "cardReasons" not in body
    [card] = body["cards"]
    assert card["kind"] == "recommendation"
    assert card["cardId"] == card_id
    assert card["reason"] == "가장 싸요"
    assert card["totalKrw"] == top.total_krw
    assert card["totalJpy"] == top.total_jpy
    assert card["savingsJpy"] == top.savings_jpy
    assert card["distanceMeters"] is None


def test_same_thread_id_continues_conversation(client: TestClient) -> None:
    model = use_fake("첫 답", "둘째 답")

    first = client.post(
        "/api/agent/chat",
        json={"userId": USER_ID, "message": "예산은 3만 엔", "lang": "ja"},
    ).json()
    second = client.post(
        "/api/agent/chat",
        json={
            "userId": USER_ID,
            "threadId": first["threadId"],
            "message": "예산 얼마였지?",
            "lang": "ja",
        },
    ).json()

    assert second["threadId"] == first["threadId"]
    assert user_texts(model.calls[1]) == ["예산은 3만 엔", "예산 얼마였지?"]


def test_other_user_cannot_continue_thread(client: TestClient) -> None:
    model = use_fake("첫 답", "둘째 답")

    first = client.post(
        "/api/agent/chat", json={"userId": USER_ID, "message": "비밀", "lang": "ko"}
    ).json()
    client.post(
        "/api/agent/chat",
        json={
            "userId": str(uuid4()),
            "threadId": first["threadId"],
            "message": "앞에서 뭐라고 했어?",
            "lang": "ko",
        },
    )

    assert user_texts(model.calls[1]) == ["앞에서 뭐라고 했어?"]


def test_without_api_key_returns_503(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        _env_file=None, anthropic_api_key=None
    )

    response = client.post(
        "/api/agent/chat", json={"userId": USER_ID, "message": "안녕", "lang": "ko"}
    )

    assert response.status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"userId": "not-a-uuid", "message": "안녕", "lang": "ko"},
        {"userId": USER_ID, "message": "", "lang": "ko"},
        {"userId": USER_ID, "message": "   ", "lang": "ko"},
        {"userId": USER_ID, "message": "가" * 2001, "lang": "ko"},
        {"userId": USER_ID, "message": "안녕", "lang": "en"},
    ],
)
def test_invalid_request_returns_422(client: TestClient, body: dict) -> None:
    use_fake()

    response = client.post("/api/agent/chat", json=body)

    assert response.status_code == 422
