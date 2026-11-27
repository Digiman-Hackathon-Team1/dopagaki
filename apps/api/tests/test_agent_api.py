"""`POST /api/agent/chat` 정상·오류 응답. 가짜 LLM을 쓰므로 API 키가 필요 없다."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from agent_fakes import (
    FailingChatModel,
    FakeChatModel,
    fake_model,
    reply_message,
    tool_call,
    user_texts,
)
from fastapi.testclient import TestClient
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.agent import router as agent_router
from api.agent.config import AgentSettings, get_settings
from api.agent.graph import build_graph
from api.agent.router import FALLBACK_ANSWER, DailyUsage, get_agent
from api.agent.tools import TOOLS
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
from api.main import app

USER_ID = str(uuid4())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # 하루 한도는 서버 메모리에 쌓이므로 테스트마다 새로 센다.
    monkeypatch.setattr(agent_router, "daily_usage", DailyUsage())
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_graph(graph) -> None:
    app.dependency_overrides[get_agent] = lambda: graph


def settings(**values) -> AgentSettings:
    return AgentSettings(_env_file=None, anthropic_api_key="test-key", **values)


def chat(client: TestClient, message: str = "안녕", **body) -> dict:
    response = client.post(
        "/api/agent/chat",
        json={"userId": USER_ID, "message": message, "lang": "ko", **body},
    )
    assert response.status_code == 200
    return response.json()


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
        "fallback",
        "conditionSummary",
        "answer",
        "cards",
        "exclusions",
        "questions",
        "suggestions",
        "cautions",
        "disclaimer",
    }
    assert body["answer"] == "안녕하세요"
    assert body["fallback"] is None
    assert body["disclaimer"].startswith("AI 답변은")


def test_chat_returns_recommendation_cards(client: TestClient) -> None:
    top = rank_rows(get_catalog(), "rejuran-2cc")[0]
    card_id = f"{top.place.id}:rejuran-2cc"
    model = fake_model(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc", "limit": 1}),
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


def test_chat_returns_location_card_before_clinic_cards(client: TestClient) -> None:
    model = fake_model(
        tool_call("resolve_location", {"query": "홍대 숙소"}, "c1"),
        tool_call(
            "search_clinics",
            {"menu_id": "shrink-300", "origin_id": "area:hongdae", "limit": 1},
            "c2",
        ),
        reply_message("홍대 기준이에요"),
    )
    graph = build_graph(model, tools=TOOLS, checkpointer=InMemorySaver())
    app.dependency_overrides[get_agent] = lambda: graph

    response = client.post(
        "/api/agent/chat",
        json={"userId": USER_ID, "message": "홍대 숙소, 슈링크", "lang": "ko"},
    )

    assert response.status_code == 200
    location, clinic = response.json()["cards"]
    assert location == {
        "kind": "location",
        "locationId": "area:hongdae",
        "locationType": "area",
        "query": "홍대 숙소",
        "nameJa": "弘大",
        "nameKo": "홍대",
        "lat": 37.5563,
        "lng": 126.9228,
    }
    assert clinic["kind"] == "recommendation"
    assert clinic["distanceMeters"] is not None


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


def assert_price_table_fallback(body: dict, reason: str) -> None:
    assert body["fallback"] == reason
    assert body["answer"] == FALLBACK_ANSWER[reason]["ko"]
    assert "시세표" in body["answer"]
    assert body["cards"] == [] and body["suggestions"] == []
    assert body["disclaimer"].startswith("AI 답변은")


def test_without_api_key_returns_price_table_fallback(client: TestClient) -> None:
    """FR-08: API 키가 없어도 오류 대신 시세표 안내."""
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        _env_file=None, anthropic_api_key=None
    )

    body = chat(client)

    assert_price_table_fallback(body, "error")


def test_unreachable_llm_returns_price_table_fallback(client: TestClient) -> None:
    """실제 ChatAnthropic이 연결에 실패해도(틀린 키·장애와 같은 경로) 시세표 안내."""
    llm = ChatAnthropic(
        model="claude-sonnet-5-5",
        api_key="wrong-key",
        base_url="http://127.0.0.1:9",
        max_tokens=100,
        max_retries=0,
        timeout=2,
    )
    use_graph(build_graph(llm, checkpointer=InMemorySaver()))

    assert_price_table_fallback(chat(client), "error")


def test_llm_error_then_next_turn_recovers(client: TestClient) -> None:
    model = FailingChatModel(messages=iter([reply_message("복구")]))
    use_graph(build_graph(model, checkpointer=InMemorySaver()))

    failed = chat(client, "첫 시도")
    recovered = chat(client, "다시", threadId=failed["threadId"])

    assert_price_table_fallback(failed, "error")
    assert recovered["answer"] == "복구"
    assert user_texts(model.calls[-1]) == ["첫 시도", "다시"]


def test_step_limit_returns_fallback_and_keeps_history_valid(
    client: TestClient,
) -> None:
    """도구를 끝없이 부르면 단계 한도에서 멈춘다. 모든 tool_use에 결과가 있어 다음 턴이 된다."""
    calls = [
        tool_call("search_clinics", {"menu_id": "rejuran-2cc", "limit": 1}, f"c{i}")
        for i in range(3)
    ]
    model = fake_model(*calls, reply_message("다음 턴"))
    use_graph(
        build_graph(model, tools=TOOLS, checkpointer=InMemorySaver(), max_steps=3)
    )

    stopped = chat(client, "리쥬란")
    next_turn = chat(client, "다시", threadId=stopped["threadId"])

    assert_price_table_fallback(stopped, "error")
    assert len(model.calls) == 4  # 한도 3번 + 다음 턴 1번
    assert next_turn["answer"] == "다음 턴"


def test_empty_reply_is_retried_once(client: TestClient) -> None:
    model = fake_model(AIMessage(content=""), reply_message("두 번째"))
    use_graph(build_graph(model, checkpointer=InMemorySaver()))

    assert chat(client)["answer"] == "두 번째"
    assert len(model.calls) == 2


def test_reply_with_empty_slots_is_retried_once(client: TestClient) -> None:
    """응답 칸 형식이지만 답변·질문이 모두 빈 응답도 빈 응답으로 보고 다시 요청한다."""
    model = fake_model(reply_message(""), reply_message("두 번째"))
    use_graph(build_graph(model, checkpointer=InMemorySaver()))

    assert chat(client)["answer"] == "두 번째"
    assert len(model.calls) == 2


def test_empty_reply_twice_returns_fallback_without_saving_it(
    client: TestClient,
) -> None:
    model = fake_model(AIMessage(content=""), AIMessage(content=""), reply_message())
    use_graph(build_graph(model, checkpointer=InMemorySaver()))

    failed = chat(client)
    chat(client, "다시", threadId=failed["threadId"])

    assert_price_table_fallback(failed, "error")
    # 빈 assistant 메시지는 API가 거절하므로 기록에 넣지 않는다.
    assert [m.type for m in model.calls[-1][1:]] == ["human", "human"]


def test_user_daily_limit_returns_fallback(client: TestClient) -> None:
    use_fake("1", "2", "3")
    app.dependency_overrides[get_settings] = lambda: settings(agent_user_daily_limit=2)

    answers = [chat(client)["answer"] for _ in range(2)]
    over = chat(client)
    other_user = client.post(
        "/api/agent/chat",
        json={"userId": str(uuid4()), "message": "안녕", "lang": "ko"},
    ).json()

    assert answers == ["1", "2"]
    assert_price_table_fallback(over, "daily_limit")
    assert other_user["answer"] == "3"


def test_total_daily_limit_returns_fallback(client: TestClient) -> None:
    model = use_fake("1")
    app.dependency_overrides[get_settings] = lambda: settings(agent_daily_limit=1)

    chat(client)
    over = client.post(
        "/api/agent/chat",
        json={"userId": str(uuid4()), "message": "안녕", "lang": "ko"},
    ).json()

    assert_price_table_fallback(over, "daily_limit")
    assert len(model.calls) == 1


def test_safety_stop_is_not_sent_to_screen(client: TestClient) -> None:
    model = fake_model(
        tool_call("search_clinics", {"menu_id": "rejuran-2cc", "limit": 1}),
        reply_message("임신 중에는 권하지 않아요.", safety_stop=True),
    )
    use_graph(build_graph(model, tools=TOOLS, checkpointer=InMemorySaver()))

    body = chat(client, "임신 중인데 리쥬란 싼 곳")

    assert "safetyStop" not in body
    assert body["cards"] == [] and body["exclusions"] == []
    assert body["fallback"] is None


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
