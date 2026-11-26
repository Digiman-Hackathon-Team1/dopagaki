"""환율 조회(ADR 0010): web 파일 → 마지막으로 읽은 값 → 스냅샷 값."""

import importlib

import httpx
import pytest
from langchain_core.messages import ToolMessage

from api.agent.schemas import RecommendationCardData
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
from api.domain.fx import FxProvider

# api.agent.tools 패키지는 같은 이름의 도구 객체를 내보내므로 모듈은 이름으로 가져온다.
tool_module = importlib.import_module("api.agent.tools.search_clinics")
SNAPSHOT = get_catalog().fx
URL = "https://web.example/naver-fx.json"


def web_fx(rate: float, date: str = "2026.11.26") -> dict:
    """web의 naver-fx.json과 같은 모양."""
    return {
        "krwPer100Jpy": rate,
        "localTradedAt": "2026-11-26T03:07:55+09:00",
        "dateLabel": date,
        "degreeCount": 960,
        "fluctuations": 0.42,
        "fluctuationsRatio": 0.05,
        "direction": "RISING",
        "bankKo": "하나은행",
        "bankJa": "ハナ銀行",
        "sourceUrl": "https://stock.naver.com/marketindex/exchange/FX_JPYKRW",
        "fetchedAt": "2026-11-25T18:09:38.502Z",
    }


class FakeWeb:
    """차례로 응답하는 가짜 web. 예외를 넣으면 그 예외를 낸다."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, url: str, timeout: float) -> object:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def provider(web: FakeWeb, clock: Clock | None = None, url: str | None = URL):
    return FxProvider(url, SNAPSHOT, fetch=web, clock=clock or Clock())


def test_without_url_uses_snapshot_and_never_fetches() -> None:
    web = FakeWeb()

    assert provider(web, url=None).current() == SNAPSHOT
    assert web.calls == 0


def test_reads_web_file() -> None:
    fx = provider(FakeWeb(web_fx(844.1))).current()

    assert fx.krw_per_100_jpy == 844.1
    assert fx.date_label == "2026.11.26"


def test_reads_again_only_after_ttl() -> None:
    web = FakeWeb(web_fx(844.1), web_fx(850.0, "2026.11.27"))
    clock = Clock()
    fx = provider(web, clock)

    assert fx.current().krw_per_100_jpy == 844.1
    clock.now = 599
    assert fx.current().krw_per_100_jpy == 844.1
    assert web.calls == 1
    clock.now = 600
    assert fx.current().krw_per_100_jpy == 850.0
    assert web.calls == 2


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("timeout"),
        httpx.HTTPStatusError("502", request=None, response=None),  # type: ignore[arg-type]
        ValueError("not json"),
        {"krwPer100Jpy": "abc"},  # 형식 오류
        ["not", "an", "object"],
        web_fx(5000),  # 범위 밖
    ],
)
def test_failure_keeps_last_value(failure: object) -> None:
    web = FakeWeb(web_fx(844.1), failure)
    clock = Clock()
    fx = provider(web, clock)

    assert fx.current().krw_per_100_jpy == 844.1
    clock.now = 600
    assert fx.current().krw_per_100_jpy == 844.1
    assert web.calls == 2


def test_failure_before_any_read_uses_snapshot() -> None:
    fx = provider(FakeWeb(httpx.ConnectTimeout("timeout"))).current()

    assert fx == SNAPSHOT


def test_failure_is_not_retried_until_ttl() -> None:
    # web이 내려가 있을 때 요청마다 시간 제한(2초)만큼 기다리지 않는다.
    web = FakeWeb(httpx.ConnectTimeout("timeout"), web_fx(844.1))
    clock = Clock()
    fx = provider(web, clock)

    assert fx.current() == SNAPSHOT
    assert fx.current() == SNAPSHOT
    assert web.calls == 1
    clock.now = 600
    assert fx.current().krw_per_100_jpy == 844.1


def test_search_clinics_uses_current_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    fx = provider(FakeWeb(web_fx(844.1)))
    monkeypatch.setattr(tool_module, "get_fx_provider", lambda: fx)

    message: ToolMessage = tool_module.search_clinics.invoke(
        {
            "type": "tool_call",
            "name": "search_clinics",
            "id": "c1",
            "args": {"menu_id": "rejuran-2cc"},
        }
    )
    cards = [
        RecommendationCardData.model_validate(c) for c in message.artifact["cards"]
    ]

    expected = rank_rows(get_catalog(), "rejuran-2cc", krw_per_100_jpy=844.1)[:3]
    assert [c.total_jpy for c in cards] == [r.total_jpy for r in expected]
    assert all(c.krw_per_100_jpy == 844.1 for c in cards)
    assert all(c.fx_date_label == "2026.11.26" for c in cards)
    assert "₩844.1 per ¥100" in message.content
    # 스냅샷 환율(843.68)과 엔화 금액이 다르다.
    assert cards[0].total_jpy != rank_rows(get_catalog(), "rejuran-2cc")[0].total_jpy
