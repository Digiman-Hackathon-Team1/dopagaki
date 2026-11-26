"""실제 Claude로 멀티턴을 확인한다.

API 키가 있고 `AGENT_LIVE_TESTS=1`일 때만 실행한다. 키가 `.env`에 있어도 평소 `pnpm check`에서는
실제 호출(비용)이 일어나지 않는다. 실행: `cd apps/api && AGENT_LIVE_TESTS=1 uv run pytest tests/test_agent_live.py`
"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent.config import get_settings
from api.main import app

key = get_settings().anthropic_api_key
pytestmark = [
    pytest.mark.skipif(
        key is None or not key.get_secret_value(), reason="ANTHROPIC_API_KEY 없음"
    ),
    pytest.mark.skipif(
        os.environ.get("AGENT_LIVE_TESTS") != "1", reason="AGENT_LIVE_TESTS=1이 아님"
    ),
]


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
