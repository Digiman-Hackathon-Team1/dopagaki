"""실제 Claude로 멀티턴을 확인한다.

API 키가 있고 `AGENT_LIVE_TESTS=1`일 때만 실행한다. 키가 `.env`에 있어도 평소 `pnpm check`에서는
실제 호출(비용)이 일어나지 않는다. 실행: `cd apps/api && AGENT_LIVE_TESTS=1 uv run pytest tests/test_agent_live.py`
"""

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent.config import get_settings
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
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


@pytest.mark.parametrize(
    ("lang", "message"),
    [
        ("ko", "리쥬란 받고 싶은데 서울에서 일본보다 싼 곳 알려 줘"),
        ("ja", "リジュランをソウルで日本より安く受けられるクリニックを教えて"),
    ],
)
def test_live_price_question_returns_cards_with_web_prices(
    lang: str, message: str
) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={"userId": str(uuid4()), "message": message, "lang": lang},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    assert body["cards"], body["answer"]
    # 카드 금액은 web 순위표(calc.ts와 같은 calc.py)의 그 병원 행과 같아야 한다.
    rows = {r.place.id: r for r in rank_rows(get_catalog(), "rejuran-2cc")}
    for card in body["cards"]:
        assert card["menuId"] == "rejuran-2cc"
        row = rows[card["placeId"]]
        assert card["totalKrw"] == row.total_krw
        assert card["totalJpy"] == row.total_jpy
        assert card["savingsJpy"] == row.savings_jpy
        assert card["jpAverageJpy"] == row.jp_benchmark_jpy
        assert card["reason"]


def test_live_branch_comparison_shows_both_branches() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={
            "userId": str(uuid4()),
            "message": "밴스의원 홍대점이랑 블리비의원 명동점 리쥬란 가격 비교해 줘",
            "lang": "ko",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    place_ids = {card["placeId"] for card in body["cards"]}
    assert {"vands-hongdae", "velyb-myeongdong"} <= place_ids, body["answer"]
    rows = {r.place.id: r for r in rank_rows(get_catalog(), "rejuran-2cc")}
    for card in body["cards"]:
        row = rows[card["placeId"]]
        assert (card["totalKrw"], card["totalJpy"], card["savingsJpy"]) == (
            row.total_krw,
            row.total_jpy,
            row.savings_jpy,
        )
