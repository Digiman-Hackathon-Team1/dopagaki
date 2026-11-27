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
from api.domain.locations import get_locations
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


def clinic_cards(body: dict) -> list[dict]:
    return [c for c in body["cards"] if c["kind"] == "recommendation"]


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
    clinics = clinic_cards(body)
    assert clinics, body["answer"]
    # 카드 금액은 web 순위표(calc.ts와 같은 calc.py)의 그 병원 행과 같아야 한다.
    rows = {r.place.id: r for r in rank_rows(get_catalog(), "rejuran-2cc")}
    for card in clinics:
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
    place_ids = {card["placeId"] for card in clinic_cards(body)}
    assert {"vands-hongdae", "velyb-myeongdong"} <= place_ids, body["answer"]
    rows = {r.place.id: r for r in rank_rows(get_catalog(), "rejuran-2cc")}
    for card in clinic_cards(body):
        row = rows[card["placeId"]]
        assert (card["totalKrw"], card["totalJpy"], card["savingsJpy"]) == (
            row.total_krw,
            row.total_jpy,
            row.savings_jpy,
        )


def test_live_sagging_two_nights_hongdae_recommends_shrink() -> None:
    """이슈 #6 완료 기준: 슈링크 추천, 인모드는 회복 기간으로 제외 이유 표시, 홍대 기준 거리."""
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={
            "userId": str(uuid4()),
            "message": "얼굴 처짐이 신경 쓰여요. 2박 3일 여행이고 숙소는 홍대예요.",
            "lang": "ko",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    locations = [c for c in body["cards"] if c["kind"] == "location"]
    assert [c["locationId"] for c in locations] == ["area:hongdae"]
    clinics = clinic_cards(body)
    assert clinics, body["answer"]
    assert {c["menuId"] for c in clinics} == {"shrink-300"}
    origin = get_locations().get("area:hongdae").point
    rows = {
        r.place.id: r for r in rank_rows(get_catalog(), "shrink-300", origin=origin)
    }
    for card in clinics:
        row = rows[card["placeId"]]
        assert (card["totalKrw"], card["savingsJpy"], card["distanceMeters"]) == (
            row.total_krw,
            row.savings_jpy,
            row.distance_meters,
        )
    assert "인모드" in body["answer"], body["answer"]


def test_live_unsupported_concern_is_not_recommended() -> None:
    """이슈 #6 완료 기준: 분류에 없는 고민은 "지원하지 않음"으로 안내."""
    client = TestClient(app)

    response = client.post(
        "/api/agent/chat",
        json={
            "userId": str(uuid4()),
            "message": "탈모가 고민인데 서울에서 뭘 받으면 좋을까요?",
            "lang": "ko",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    assert clinic_cards(body) == [], body["answer"]


def post(message: str, lang: str) -> dict:
    response = TestClient(app).post(
        "/api/agent/chat",
        json={"userId": str(uuid4()), "message": message, "lang": lang},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return body


def jp_match_cards(body: dict) -> list[dict]:
    return [c for c in body["cards"] if c["kind"] == "jp_match"]


def test_live_hifu_question_maps_to_shrink_with_reason_and_clinics() -> None:
    """이슈 #7 완료 기준: "하이후 하는 병원 있어?" → 슈링크 대응과 이유, 병원 카드."""
    body = post("하이후 하는 병원 있어?", "ko")

    [match] = jp_match_cards(body)
    assert match["menuId"] == "shrink-300"
    assert "슈링크" in body["answer"], body["answer"]
    clinics = clinic_cards(body)
    assert clinics, body["answer"]
    assert {c["menuId"] for c in clinics} == {"shrink-300"}
    rows = {r.place.id: r for r in rank_rows(get_catalog(), "shrink-300")}
    for card in clinics:
        row = rows[card["placeId"]]
        assert (card["totalKrw"], card["savingsJpy"]) == (
            row.total_krw,
            row.savings_jpy,
        )


def test_live_jp_clinic_price_is_compared_directly() -> None:
    """설계 4.7: 다니던 일본 병원 가격이 데이터에 있으면 그 가격과 직접 비교한다(절약액은 코드가 계산)."""
    body = post(
        "品川美容外科でハイフを受けていました。ソウルだとどのくらい安くなりますか?",
        "ja",
    )

    [match] = jp_match_cards(body)
    assert match["jpClinic"]["nameJa"] == "品川美容外科"
    clinics = clinic_cards(body)
    assert clinics, body["answer"]
    for card in clinics:
        assert card["jpClinic"]["savingsJpy"] == 27500 - card["totalJpy"]


def test_live_ambiguous_jp_name_asks_before_searching() -> None:
    """이슈 #7 완료 기준: 애매하면 확인 질문."""
    body = post("ピーリングを受けたいです", "ja")

    assert jp_match_cards(body) == []
    assert clinic_cards(body) == [], body["answer"]
    asked = body["answer"] + json.dumps(body["questions"], ensure_ascii=False)
    assert "ララピール" in asked and "アクアピール" in asked, asked


def test_live_unknown_jp_name_says_no_match() -> None:
    """이슈 #7 완료 기준: 대응이 없으면 틀린 대응 대신 "대응 없음"."""
    body = post("ダーマペンを受けられるクリニックはありますか?", "ja")

    assert jp_match_cards(body) == []
    assert clinic_cards(body) == [], body["answer"]
