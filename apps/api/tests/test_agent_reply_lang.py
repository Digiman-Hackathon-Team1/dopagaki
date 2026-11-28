"""사용자가 쓴 언어로 답하기(설계 문서 2장 응답 언어, 이슈 #76, ADR 0020). 가짜 LLM을 쓰므로 API 키가 필요 없다.

언어는 두 단계로 정한다. 서비스가 메시지 글자로 1차 판별을 하고(못 정하면 직전 턴 언어, 첫 턴이면 화면 언어),
Claude가 뜻을 보고 최종 언어를 고른다. 면책·금액 주의 문구와 응답의 lang은 Claude가 실제로 쓴 답의 언어를 따른다.
Claude를 부르지 않는 안내(처리 중·턴 상한·하루 한도·오류)는 1차 판별 언어를 쓴다.
"""

import json
import threading
from collections.abc import Iterator
from uuid import uuid4

import pytest
from agent_fakes import FailingChatModel, FakeChatModel, fake_model, reply_message
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ConfigDict

from api.agent import service as agent_service
from api.agent.config import AgentSettings, get_settings
from api.agent.graph import AMOUNT_CAUTION, LANG_NAME, LANG_NOTE, build_graph
from api.agent.service import (
    DISCLAIMER,
    FALLBACK_ANSWER,
    Conversations,
    DailyUsage,
    get_agent,
)
from api.main import app

USER_ID = str(uuid4())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # 하루 한도와 대화 잠금은 서버 메모리에 쌓이므로 테스트마다 새로 둔다.
    monkeypatch.setattr(agent_service, "daily_usage", DailyUsage())
    monkeypatch.setattr(agent_service, "conversations", Conversations())
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_settings(**values) -> None:
    values.setdefault("anthropic_api_key", "test-key")
    app.dependency_overrides[get_settings] = lambda: AgentSettings(
        _env_file=None, **values
    )


def use_model(model: FakeChatModel) -> FakeChatModel:
    saver = InMemorySaver()
    app.dependency_overrides[get_agent] = lambda: build_graph(model, checkpointer=saver)
    return model


def post(
    client: TestClient,
    message: str,
    screen: str,
    thread_id: str | None = None,
    path: str = "chat",
) -> dict:
    body = {
        "userId": USER_ID,
        "threadId": thread_id,
        "message": message,
        "lang": screen,
    }
    if path == "chat":
        response = client.post("/api/agent/chat", json=body)
        assert response.status_code == 200, response.text
        return response.json()
    with client.stream("POST", "/api/agent/chat/stream", json=body) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line.startswith("data:")]
    return json.loads(lines[-1].removeprefix("data:"))["response"]


def lang_note(model: FakeChatModel, call: int = 0) -> str:
    """그 호출의 마지막 사용자 메시지 끝에 붙은 언어 안내."""
    last = [m for m in model.calls[call] if m.type == "human"][-1]
    return last.content[-1]["text"]


def note(lang: str) -> str:
    return LANG_NOTE.format(language=LANG_NAME[lang])


@pytest.mark.parametrize("path", ["chat", "stream"])
@pytest.mark.parametrize(
    ("screen", "message", "answer", "expected"),
    [
        ("ja", "리쥬란 얼마예요?", "리쥬란은 이 병원이 가장 싸요.", "ko"),
        ("ko", "リジュランはいくらですか", "リジュランはこちらが最安です。", "ja"),
    ],
)
def test_message_language_beats_screen_language(
    client: TestClient, path: str, screen: str, message: str, answer: str, expected: str
) -> None:
    model = use_model(fake_model(reply_message(answer)))

    body = post(client, message, screen, path=path)

    assert lang_note(model) == note(expected)
    assert body["lang"] == expected
    assert body["disclaimer"] == DISCLAIMER[expected]


def test_reply_language_follows_what_claude_wrote(client: TestClient) -> None:
    """글자 수로는 일본어지만 한국어로 답해 달라는 메시지. Claude가 한국어로 답하면 응답과 다음 턴도 한국어다."""
    model = use_model(
        fake_model(
            reply_message("리쥬란은 이 병원이 가장 싸요.", suggestions=["가까운 곳"]),
            reply_message("2cc 기준이에요.", suggestions=["예약 방법"]),
        )
    )

    first = post(client, "한국어로 말해줘. リジュランの価格", "ja")
    second = post(client, "Rejuran 2cc?", "ja", first["threadId"])

    # 1차 판별은 일본어라 모호할 때 쓸 언어로 일본어를 건넨다. 최종 언어는 Claude가 쓴 답을 따른다.
    assert lang_note(model, 0) == note("ja")
    assert (first["lang"], first["disclaimer"]) == ("ko", DISCLAIMER["ko"])
    # 판별하지 못한 메시지는 직전 턴에 실제로 답한 언어를 쓴다.
    assert lang_note(model, 1) == note("ko")
    assert (second["lang"], second["disclaimer"]) == ("ko", DISCLAIMER["ko"])


def test_reply_without_letters_keeps_first_guess(client: TestClient) -> None:
    use_model(fake_model(reply_message("OK", suggestions=["1", "2", "3"])))

    body = post(client, "리쥬란 얼마예요?", "ja")

    assert body["lang"] == "ko"


@pytest.mark.parametrize(
    ("screen", "message", "answer", "expected"),
    [
        ("ja", "리쥬란 얼마예요?", "리쥬란은 ¥1,234예요. 다른 곳도 볼까요?", "ko"),
        # 1차 판별(한국어)과 다르게 Claude가 일본어로 답하면 주의 문구도 일본어다.
        ("ko", "밴스의원 홍대점 どう?", "¥1,234です。ほかも見ますか。", "ja"),
    ],
)
def test_amount_caution_follows_reply_language(
    client: TestClient, screen: str, message: str, answer: str, expected: str
) -> None:
    use_model(fake_model(reply_message(answer)))

    body = post(client, message, screen)

    assert body["lang"] == expected
    assert AMOUNT_CAUTION[expected] in body["cautions"]
    other = "ja" if expected == "ko" else "ko"
    assert AMOUNT_CAUTION[other] not in body["cautions"]


def test_undetected_first_message_uses_screen_language(client: TestClient) -> None:
    model = use_model(fake_model(reply_message("OK")))

    body = post(client, "Rejuran?", "ja")

    assert lang_note(model) == note("ja")
    assert body["lang"] == "ja"


@pytest.mark.parametrize("path", ["chat", "stream"])
def test_llm_error_fallback_uses_message_language(
    client: TestClient, path: str
) -> None:
    use_model(FailingChatModel(messages=iter([reply_message("x")]), failures=5))

    body = post(client, "리쥬란 얼마예요?", "ja", path=path)

    assert body["fallback"] == "error"
    assert body["answer"] == FALLBACK_ANSWER["error"]["ko"]
    assert (body["lang"], body["disclaimer"]) == ("ko", DISCLAIMER["ko"])


def test_missing_key_fallback_uses_message_language(client: TestClient) -> None:
    use_settings(anthropic_api_key=None)
    app.dependency_overrides[get_agent] = lambda: None

    body = post(client, "리쥬란 얼마예요?", "ja")

    assert body["fallback"] == "error"
    assert body["answer"] == FALLBACK_ANSWER["error"]["ko"]
    assert body["lang"] == "ko"


def test_daily_limit_fallback_uses_message_language(client: TestClient) -> None:
    use_settings(agent_user_daily_limit=0)
    use_model(fake_model())

    body = post(client, "リジュランはいくらですか", "ko")

    assert body["fallback"] == "daily_limit"
    assert body["answer"] == FALLBACK_ANSWER["daily_limit"]["ja"]
    assert (body["lang"], body["disclaimer"]) == ("ja", DISCLAIMER["ja"])


@pytest.mark.parametrize("path", ["chat", "stream"])
def test_turn_limit_fallback_uses_previous_turn_language(
    client: TestClient, path: str
) -> None:
    use_settings(agent_max_turns=1)
    use_model(fake_model(reply_message("리쥬란은 이 병원이 가장 싸요.")))
    first = post(client, "리쥬란 얼마예요?", "ja")

    # 판별하지 못한 메시지라 직전 턴 언어(한국어)로 안내한다.
    over = post(client, "Rejuran?", "ja", first["threadId"], path=path)

    assert over["fallback"] == "turn_limit"
    assert over["answer"] == FALLBACK_ANSWER["turn_limit"]["ko"]
    assert over["lang"] == "ko"


class GatedChatModel(FakeChatModel):
    """첫 LLM 호출에서 gate가 열릴 때까지 멈추는 가짜 LLM. 앞 요청이 처리 중인 상황을 만든다."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    entered: threading.Event
    gate: threading.Event

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if not self.entered.is_set():
            self.entered.set()
            assert self.gate.wait(5), "테스트가 gate를 열지 않았다"
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        ("리쥬란은요?", "ko"),
        # 대화 잠금 전이라 대화 기록(직전 턴 한국어)을 읽지 않고 화면 언어를 쓴다.
        ("Rejuran?", "ja"),
    ],
)
def test_busy_fallback_uses_this_message_only(
    client: TestClient, second: str, expected: str
) -> None:
    use_settings(agent_thread_wait_seconds=0.2)
    model = GatedChatModel(
        messages=iter([reply_message("리쥬란은 이 병원이 가장 싸요.")]),
        entered=threading.Event(),
        gate=threading.Event(),
    )
    use_model(model)
    thread_id = str(uuid4())
    result: list[dict] = []
    one = threading.Thread(
        target=lambda: result.append(post(client, "리쥬란 얼마예요?", "ja", thread_id))
    )
    one.start()
    assert model.entered.wait(5)

    busy = post(client, second, "ja", thread_id)
    model.gate.set()
    one.join(5)

    assert busy["fallback"] == "busy"
    assert busy["answer"] == FALLBACK_ANSWER["busy"][expected]
    assert busy["lang"] == expected
    assert result[0]["lang"] == "ko"


class FailOnceModel(FakeChatModel):
    """fail_at번째 호출(0부터)에서만 API 장애를 흉내 낸다."""

    fail_at: int = 1

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if len(self.calls) == self.fail_at:
            self.calls.append(list(messages))
            raise RuntimeError("simulated API failure")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_failed_turn_does_not_change_previous_turn_language(
    client: TestClient,
) -> None:
    """실패한 턴의 안내 언어가 아니라, Claude가 마지막으로 실제 답한 언어가 다음 턴에 이어진다."""
    use_settings(agent_max_retries=0)
    model = use_model(
        FailOnceModel(
            messages=iter(
                [
                    reply_message("리쥬란은 이 병원이 가장 싸요."),
                    reply_message("2cc 기준이에요."),
                ]
            )
        )
    )
    first = post(client, "리쥬란 얼마예요?", "ja")
    failed = post(client, "リジュランアイは?", "ja", first["threadId"])
    third = post(client, "2cc?", "ja", first["threadId"])

    assert failed["fallback"] == "error"
    assert failed["lang"] == "ja"
    # 판별하지 못한 메시지는 실패 턴(일본어 안내)이 아니라 앞서 한국어로 답한 턴을 따른다.
    assert lang_note(model, 2) == note("ko")
    assert third["lang"] == "ko"
