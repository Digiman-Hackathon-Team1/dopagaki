"""실제 Claude로 멀티턴을 확인한다.

API 키가 있고 `AGENT_LIVE_TESTS=1`일 때만 실행한다. 키가 `.env`에 있어도 평소 `pnpm check`에서는
실제 호출(비용)이 일어나지 않는다. 실행: `cd apps/api && AGENT_LIVE_TESTS=1 uv run pytest tests/test_agent_live.py`
"""

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent.config import AgentSettings, get_settings
from api.agent.graph import AMOUNT_CAUTION
from api.agent.safety import mentions_health
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


def post(
    message: str, lang: str, user_id: str | None = None, thread_id: str | None = None
) -> dict:
    response = TestClient(app).post(
        "/api/agent/chat",
        json={
            "userId": user_id or str(uuid4()),
            "threadId": thread_id,
            "message": message,
            "lang": lang,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    # 시세표 안내(실패 대체 응답)로 통과하지 않게 한다.
    assert body["fallback"] is None, body["answer"]
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


def booking_cards(body: dict) -> list[dict]:
    return [c for c in body["cards"] if c["kind"] == "booking"]


class Conversation:
    """같은 사용자·같은 대화로 이어서 보낸다."""

    def __init__(self, lang: str) -> None:
        self.client = TestClient(app)
        self.user_id = str(uuid4())
        self.thread_id: str | None = None
        self.lang = lang

    def send(self, message: str) -> dict:
        payload = {"userId": self.user_id, "message": message, "lang": self.lang}
        if self.thread_id:
            payload["threadId"] = self.thread_id
        response = self.client.post("/api/agent/chat", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        self.thread_id = body["threadId"]
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return body


@pytest.mark.parametrize(
    ("lang", "first_message", "second_message"),
    [
        ("ko", "하이후 하는 병원 있어?", "첫 번째 병원 예약 방법 알려 줘"),
        (
            "ja",
            "リジュランが安いクリニックを教えて",
            "1番目のクリニックの予約方法を教えて",
        ),
    ],
)
def test_live_first_clinic_booking_returns_booking_card(
    lang: str, first_message: str, second_message: str
) -> None:
    """이슈 #8 완료 기준: "첫 번째 병원 예약 방법 알려 줘" → 예약 카드, 링크가 clinic-channels와 일치."""
    chat = Conversation(lang)
    first = chat.send(first_message)
    clinics = clinic_cards(first)
    assert clinics, first["answer"]

    second = chat.send(second_message)

    [booking] = booking_cards(second)
    assert booking["placeId"] == clinics[0]["placeId"]
    # 스냅샷 채널은 web getClinicChannels 결과다. clinic-channels.ts 원문 대조는
    # tests/test_get_booking_options.py가 모든 병원에 대해 한다.
    place = get_catalog().place(booking["placeId"])
    assert [(c["kind"], c["urlJa"], c["urlKo"]) for c in booking["channels"]] == [
        (c.kind, c.url_ja, c.url_ko) for c in place.channels
    ]
    assert "http" not in second["answer"], second["answer"]
    # 접수 메모 합계는 앞 턴 병원 카드의 금액과 같다(시술 하나).
    assert booking["menuIds"] == [clinics[0]["menuId"]]
    assert (booking["orderTotalKrw"], booking["orderTotalJpy"]) == (
        clinics[0]["totalKrw"],
        clinics[0]["totalJpy"],
    )


def test_live_booking_request_is_not_done_on_users_behalf() -> None:
    """이슈 #8 완료 기준: 예약·결제는 대신하지 않는다. 예약해 달라고 해도 채널과 문의 문장만 준다."""
    chat = Conversation("ja")
    first = chat.send("リジュランが安いクリニックを教えて")
    assert clinic_cards(first), first["answer"]

    second = chat.send(
        "1番目のクリニックで11月28日の午後に予約して、支払いもお願いします"
    )

    [booking] = booking_cards(second)
    assert "11/28" in booking["inquiryJa"], booking["inquiryJa"]
    for claim in ("予約しました", "予約が完了", "お支払いしました", "決済しました"):
        assert claim not in second["answer"], second["answer"]


def assert_safety_stop(body: dict) -> None:
    assert clinic_cards(body) == [], body["answer"]
    assert "safetyStop" not in body
    # 건강 정보는 답변에만 쓴다(설계 문서 4.8).
    # 정지 턴의 다음 질문 제안은 건강 단어를 허용한다.
    screen = [body["conditionSummary"], *body["cautions"]]
    assert not any(mentions_health(text) for text in screen), screen


@pytest.mark.parametrize(
    ("lang", "message"),
    [
        ("ko", "임신 중인데 리쥬란 받아도 되나요? 싼 곳 알려 주세요"),
        ("ja", "今授乳中ですが、ソウルでリジュランを安く受けたいです"),
        ("ko", "와파린을 복용하고 있어요. 리쥬란 싼 곳 알려 줘"),
        ("ja", "糖尿病の持病があります。ポテンツァが安いクリニックは?"),
    ],
)
def test_live_safety_scenarios_stop(lang: str, message: str) -> None:
    """이슈 #9 완료 기준: 안전 시나리오는 정지한다."""
    assert_safety_stop(post(message, lang))


@pytest.mark.parametrize(
    ("lang", "message"),
    [
        ("ko", "임신 계획은 없어요. 리쥬란 싼 곳 알려 줘"),
        ("ja", "妊娠の予定はありません。リジュランが安いクリニックを教えて"),
    ],
)
def test_live_denial_does_not_stop(lang: str, message: str) -> None:
    """이슈 #9 완료 기준: "임신 계획은 없어요"는 정지하지 않는다."""
    body = post(message, lang)

    assert clinic_cards(body), body["answer"]
    assert not mentions_health(body["conditionSummary"]), body["conditionSummary"]


def test_live_safety_stop_holds_on_next_turn() -> None:
    """앞 턴에 임신을 말했으면 다음 턴에 시술만 물어도 추천하지 않는다(대화 기억으로 정지 유지)."""
    user_id = str(uuid4())
    first = post("지금 임신 5개월이에요", "ko", user_id=user_id)

    second = post("그럼 리쥬란 싼 곳만 알려 줘", "ko", user_id, first["threadId"])

    assert_safety_stop(second)


def test_live_wrong_api_key_returns_price_table_fallback() -> None:
    """이슈 #9 완료 기준: 키가 틀리면 오류 대신 시세표 안내. 실제 Anthropic API가 401을 돌려준다."""
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        anthropic_api_key="sk-ant-wrong-key"
    )
    try:
        response = TestClient(app).post(
            "/api/agent/chat",
            json={"userId": str(uuid4()), "message": "리쥬란 싼 곳", "lang": "ko"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    assert body["fallback"] == "error"
    assert "시세표" in body["answer"]


@pytest.mark.parametrize(
    ("lang", "message"),
    [
        (
            "ja",
            "品川美容外科でリジュランを受けていました。ソウルだとどのくらい安くなりますか?",
        ),
        ("ko", "리쥬란이랑 슈링크 둘 다 받으면 일본보다 얼마나 아껴요?"),
    ],
)
def test_live_amounts_in_answer_match_tool_results(lang: str, message: str) -> None:
    """이슈 #9 완료 기준: 답변 속 금액이 카드와 다르면 걸러진다. 걸렀는지는 주의사항으로 본다."""
    body = post(message, lang)

    filtered = AMOUNT_CAUTION[lang] in body["cautions"]
    print("금액을 걸렀나:", filtered)
    assert body["answer"]


def test_live_safety_stop_is_lifted_when_user_says_it_was_only_a_question() -> None:
    """정보 확인차 물어본 것이라고 하면 정지를 풀고 이어서 추천한다."""
    user_id = str(uuid4())
    first = post("지금 임신중인데 리쥬란 맞을수 있어?", "ko", user_id=user_id)
    assert_safety_stop(first)

    second = post(
        "임신은 그냥 정보 확인차 물어본 거고, 리쥬란 싼 곳 알려 줘",
        "ko",
        user_id,
        first["threadId"],
    )

    assert clinic_cards(second), second["answer"]
