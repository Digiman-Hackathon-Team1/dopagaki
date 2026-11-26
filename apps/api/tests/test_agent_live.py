"""실제 Claude로 멀티턴을 확인한다. API 키가 없으면 건너뛴다(CI는 건너뜀)."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent.config import get_settings
from api.main import app

key = get_settings().anthropic_api_key
pytestmark = pytest.mark.skipif(
    key is None or not key.get_secret_value(), reason="ANTHROPIC_API_KEY 없음"
)


def test_live_second_turn_remembers_first_turn() -> None:
    client = TestClient(app)
    user_id = str(uuid4())

    first = client.post(
        "/api/agent/chat",
        json={
            "userId": user_id,
            "message": "예산은 3만 엔이에요. 일정은 2박 3일이에요.",
            "lang": "ko",
        },
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/api/agent/chat",
        json={
            "userId": user_id,
            "threadId": first.json()["threadId"],
            "message": "제가 처음에 말한 예산이 얼마였죠? 숫자만 답해 주세요.",
            "lang": "ko",
        },
    )
    assert second.status_code == 200, second.text
    answer = second.json()["answer"].replace(",", "")
    assert any(s in answer for s in ("3만", "30000")), answer
