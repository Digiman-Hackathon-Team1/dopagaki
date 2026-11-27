"""상담 대화 한 턴의 오케스트레이션(설계 문서 10장).

router는 요청을 받아 `ChatService`를 부르고 응답을 돌려주기만 한다. 데모 녹화 응답, API 키 확인,
하루 한도, 그래프 실행, 실패 처리, 응답 조립은 여기서 한다.

에이전트가 답하지 못하면 오류 대신 시세표 안내를 돌려준다(FR-08). API 키가 없거나 틀림,
LLM 오류·시간 초과, 한 턴 단계 한도, 사용자별·전체 하루 한도가 그 경우다.

같은 대화의 요청은 하나씩 처리하고, 한 대화의 턴 수와 서버 메모리에 남기는 대화를 제한한다(설계 문서 8장
대화 기억 관리). 이 규칙은 `/chat`과 `/chat/stream`이 함께 쓰는 시작 처리(`_begin`)에 있다.
"""

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.utils.json import parse_partial_json
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from api.agent.amounts import drop_unknown_amounts, known_amounts
from api.agent.config import AgentSettings, get_settings
from api.agent.demo import DemoSessions, demo_response, load_demo, recorded_messages
from api.agent.graph import (
    build_graph,
    read_cards,
    read_exclusions,
    read_failure,
    read_reply,
)
from api.agent.schemas import (
    AgentReply,
    ChatRequest,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamDone,
    ChatStreamEvent,
    ChatStreamStart,
    FallbackReason,
    Lang,
)
from api.agent.tools import TOOLS

logger = logging.getLogger(__name__)

# 대화 기억. 저장소 종류는 배포 위치가 정해진 뒤 결정한다(설계 문서 2장). 서버를 재시작하면 사라진다.
checkpointer = InMemorySaver()

DISCLAIMER: dict[Lang, str] = {
    "ja": "AIの回答は正確でない場合があります。決める前にクリニックに確認してください。",
    "ko": "AI 답변은 정확하지 않을 수 있어요. 결정 전에 병원에 확인하세요.",
}

# 시세표 안내(FR-08). 시세표는 web 첫 화면의 시술별 가격 비교표다.
FALLBACK_ANSWER: dict[FallbackReason, dict[Lang, str]] = {
    "error": {
        "ja": (
            "申し訳ありません、いまAI相談がお答えできませんでした。相場表で施術ごとのソウルのクリニック価格と"
            "日本の平均価格との差額を確認できます。少し時間をおいてもう一度お試しください。"
        ),
        "ko": (
            "죄송해요, 지금은 AI 상담이 답하지 못했어요. 시세표에서 시술별 서울 병원 가격과 일본 평균 대비 "
            "절약액을 볼 수 있어요. 잠시 뒤 다시 물어봐 주세요."
        ),
    },
    "daily_limit": {
        "ja": (
            "本日のAI相談の利用上限に達しました。相場表で施術ごとのソウルのクリニック価格と日本の平均価格との"
            "差額を確認できます。AI相談は明日またご利用ください。"
        ),
        "ko": (
            "오늘 AI 상담 이용 한도에 도달했어요. 시세표에서 시술별 서울 병원 가격과 일본 평균 대비 절약액을 "
            "볼 수 있어요. AI 상담은 내일 다시 이용해 주세요."
        ),
    },
    "busy": {
        "ja": (
            "前のメッセージにまだお答えしています。回答が届いてから、もう一度送ってください。"
            "急ぎの場合は相場表で施術ごとの価格を確認できます。"
        ),
        "ko": (
            "앞 메시지에 아직 답하는 중이에요. 답이 온 뒤 다시 보내 주세요. 급하면 시세표에서 시술별 가격을 "
            "볼 수 있어요."
        ),
    },
    "turn_limit": {
        "ja": (
            "この相談は長くなったため、これ以上続けられません。新しい相談を始めてください。"
            "相場表で施術ごとの価格も確認できます。"
        ),
        "ko": (
            "이 대화가 길어져 더 이어 갈 수 없어요. 새 대화를 열어 주세요. 시세표에서 시술별 가격도 볼 수 "
            "있어요."
        ),
    },
}


class DailyUsage:
    """사용자별·전체 하루(UTC) 대화 요청 수(설계 문서 6장 비용).

    서버 메모리에 두므로 재시작하면 0부터 센다. 사용자 ID는 브라우저가 만든 익명 ID라 바꿔 보내면
    사용자별 한도를 피할 수 있다. 비용 상한은 전체 한도가 맡는다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: date | None = None
        self._total = 0
        self._users: dict[UUID, int] = {}

    def take(self, user_id: UUID, total_limit: int, user_limit: int) -> bool:
        """한도 안이면 한 번을 세고 True, 넘었으면 세지 않고 False."""
        today = datetime.now(UTC).date()
        with self._lock:
            if self._day != today:
                self._day, self._total, self._users = today, 0, {}
            used = self._users.get(user_id, 0)
            if self._total >= total_limit or used >= user_limit:
                return False
            self._total += 1
            self._users[user_id] = used + 1
            return True


@dataclass
class _Thread:
    lock: threading.Lock = field(default_factory=threading.Lock)
    # 잠금을 기다리거나 잡고 있는 요청 수. 0일 때만 지울 수 있다.
    users: int = 0


class Conversations:
    """대화별 잠금과 보관(설계 문서 8장 대화 기억 관리).

    같은 대화의 요청은 앞 요청이 끝날 때까지 기다린다. 마지막 요청 뒤 보관 시간이 지난 대화와, 대화 수
    상한을 넘은 가장 오래된 대화는 요청이 끝날 때마다 대화 기억에서 지운다. 처리 중이거나 기다리는 요청이
    있는 대화는 지우지 않는다. 서버 메모리에 두므로 재시작하면 대화 기억과 함께 사라진다.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._guard = threading.Lock()
        self._threads: dict[str, _Thread] = {}
        # 마지막 요청이 끝난 시각. 오래된 대화가 앞에 온다.
        self._seen: OrderedDict[str, float] = OrderedDict()

    def acquire(self, key: str, timeout: float) -> bool:
        """대화의 잠금을 잡으면 True, 시간 안에 못 잡으면 False.

        대화마다 기다리는 요청은 하나까지다. 처리 중인 요청과 기다리는 요청이 이미 있으면 기다리지 않고
        False를 돌려준다. 기다리는 요청은 서버 스레드를 하나씩 잡으므로, 한 대화로 연달아 보내 스레드를
        모두 차지하지 못하게 한다.
        """
        with self._guard:
            thread = self._threads.setdefault(key, _Thread())
            if thread.users >= 2:
                return False
            thread.users += 1
        if thread.lock.acquire(timeout=timeout):
            return True
        with self._guard:
            thread.users -= 1
        return False

    def release(
        self,
        key: str,
        delete: Callable[[str], None] | None,
        ttl: float,
        max_threads: int,
        *,
        stored: bool = True,
    ) -> None:
        """잠금을 풀고, 보관 기한이 지났거나 상한을 넘은 대화를 delete로 지운다.

        stored는 이 대화가 대화 기억에 남아 있는지다. 하루 한도·키 없음처럼 기록 없이 끝난 요청은 보관 목록과
        상한에 넣지 않는다. 그렇지 않으면 새 대화 ID로 거절된 요청만 보내도 다른 대화가 밀려 지워진다.
        삭제는 _guard 안에서 해, 지우는 동안 같은 대화의 새 요청이 잠금을 잡지 못하게 한다.
        """
        with self._guard:
            thread = self._threads[key]
            thread.users -= 1
            thread.lock.release()
            if not stored:
                if thread.users == 0 and key not in self._seen:
                    del self._threads[key]
                return
            now = self._clock()
            self._seen[key] = now
            self._seen.move_to_end(key)
            for old in self._expired(now, ttl, max_threads, keep=key):
                logger.info(
                    "보관 기한이 지났거나 대화 수 상한을 넘은 대화를 지운다: %s", old
                )
                if delete is None:
                    continue
                try:
                    delete(old)
                except Exception:
                    # 다른 대화를 지우다 난 오류로 지금 요청을 실패시키지 않는다.
                    logger.exception("대화 기억에서 대화를 지우지 못했다: %s", old)

    def _expired(
        self, now: float, ttl: float, max_threads: int, keep: str
    ) -> list[str]:
        """지울 대화를 골라 목록에서 뺀다. 오래된 대화부터 보고, 방금 끝난 대화(keep)는 남긴다.

        _guard 안에서 부른다.
        """
        idle = [k for k in self._seen if k != keep and self._threads[k].users == 0]
        over = len(self._seen) - max_threads
        expired = []
        for key in idle:
            if now - self._seen[key] > ttl or over > 0:
                expired.append(key)
                over -= 1
        for key in expired:
            del self._seen[key]
            del self._threads[key]
        return expired


def count_turns(messages: list[BaseMessage]) -> int:
    """대화 기억에 남은 사용자 턴 수."""
    return sum(isinstance(m, HumanMessage) for m in messages)


daily_usage = DailyUsage()
demo_sessions = DemoSessions()
conversations = Conversations()


@lru_cache
def _graph_for(
    model: str,
    effort: str,
    max_tokens: int,
    api_key: str,
    timeout: float,
    max_retries: int,
    max_steps: int,
    max_seconds: float,
) -> CompiledStateGraph:
    chat_model = ChatAnthropic(
        model=model,
        api_key=api_key,
        effort=effort,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
    )
    return build_graph(
        chat_model,
        tools=TOOLS,
        checkpointer=checkpointer,
        max_steps=max_steps,
        max_seconds=max_seconds,
    )


def get_agent(
    settings: Annotated[AgentSettings, Depends(get_settings)],
) -> CompiledStateGraph | None:
    """요청 때 그래프와 LLM 클라이언트를 만든다. API 키가 없으면 None이고 시세표 안내로 답한다."""
    if (
        settings.anthropic_api_key is None
        or not settings.anthropic_api_key.get_secret_value()
    ):
        return None
    return _graph_for(
        settings.agent_model,
        settings.agent_effort,
        settings.agent_max_tokens,
        settings.anthropic_api_key.get_secret_value(),
        settings.agent_timeout_seconds,
        settings.agent_max_retries,
        settings.agent_max_steps,
        settings.agent_turn_timeout_seconds,
    )


def fallback_response(
    thread_id: UUID, lang: Lang, reason: FallbackReason
) -> ChatResponse:
    return ChatResponse(
        thread_id=thread_id,
        fallback=reason,
        condition_summary="",
        answer=FALLBACK_ANSWER[reason][lang],
        cards=[],
        exclusions=[],
        questions=[],
        suggestions=[],
        cautions=[],
        disclaimer=DISCLAIMER[lang],
    )


@dataclass(frozen=True)
class _Turn:
    """한 턴을 시작할 때 정한 값. early가 있으면 그래프를 돌리지 않고 그 응답으로 끝낸다."""

    thread_id: UUID
    key: str
    config: dict
    early: ChatResponse | None
    # 잠금을 잡은 대화 목록. 잡았으면 _end에서 같은 목록에 푼다. 못 잡았으면 None.
    conversations: Conversations | None


class ChatService:
    """대화 한 턴을 처리한다. 그래프와 설정은 요청마다 주입받는다."""

    def __init__(
        self, agent: CompiledStateGraph | None, settings: AgentSettings
    ) -> None:
        self._agent = agent
        self._settings = settings
        self._streams: list[Generator[ChatStreamEvent, None, None]] = []

    def close(self) -> None:
        """요청이 끝날 때(연결이 끊긴 경우 포함) 이 요청이 연 스트림을 닫아 대화 잠금을 푼다.

        FastAPI는 연결이 끊겨도 스트림 generator를 닫지 않아, 닫지 않으면 가비지 컬렉터가 돌 때까지 잠금이
        남는다. generator가 그래프를 실행하는 중이면 그 단계가 끝나 멈출 때까지 기다렸다가 닫는다.
        """
        for gen in self._streams:
            while gen.gi_running:
                time.sleep(0.05)
            gen.close()
        self._streams.clear()

    def chat(self, body: ChatRequest) -> ChatResponse:
        turn = self._begin(*self._open(body), body)
        try:
            if turn.early is not None:
                return turn.early
            try:
                state = self._agent.invoke(self._input(body), turn.config)
            except Exception:
                logger.exception("에이전트 실행 실패")
                return fallback_response(turn.thread_id, body.lang, "error")
            return self._respond(state, turn.thread_id, body.lang)
        finally:
            self._end(turn)

    def stream(self, body: ChatRequest) -> Iterator[ChatStreamEvent]:
        gen = self._stream(body)
        self._streams.append(gen)
        return gen

    def _stream(self, body: ChatRequest) -> Generator[ChatStreamEvent, None, None]:
        """chat과 같은 순서로 처리하되, 답변 글을 만들어지는 대로 보낸다.

        start → delta(0개 이상) → done 순서다. done의 응답은 chat과 같고, 화면은 이어 붙인 답변을
        done의 answer로 바꾼다. 답변은 finalize가 금액을 거르기 전의 글이므로, 문장·줄이 끝날 때마다
        같은 규칙(`drop_unknown_amounts`)으로 걸러 지금까지 보낸 글 뒤에 이어지는 부분만 보낸다.

        start는 대화 잠금을 기다리기 전에 보낸다. 잠금은 done을 보낸 뒤 풀고, 연결이 끊기면 요청이 끝날 때
        `close`가 스트림을 닫아 푼다.
        """
        thread_id, key, config = self._open(body)
        yield ChatStreamStart(type="start", thread_id=thread_id)
        turn = self._begin(thread_id, key, config, body)
        try:
            yield from self._stream_turn(turn, body)
        finally:
            self._end(turn)

    def _stream_turn(self, turn: _Turn, body: ChatRequest) -> Iterator[ChatStreamEvent]:
        if turn.early is not None:
            yield ChatStreamDone(type="done", response=turn.early)
            return
        answers = _AnswerStream(lambda: self._known_amounts(turn.config))
        try:
            for chunk, meta in self._agent.stream(
                self._input(body), turn.config, stream_mode="messages"
            ):
                if meta.get("langgraph_node") != "agent" or not isinstance(
                    chunk, AIMessageChunk
                ):
                    continue
                if text := answers.feed(chunk):
                    yield ChatStreamDelta(type="delta", text=text)
            state = self._agent.get_state(turn.config).values
        except Exception:
            logger.exception("에이전트 실행 실패")
            yield ChatStreamDone(
                type="done",
                response=fallback_response(turn.thread_id, body.lang, "error"),
            )
            return
        yield ChatStreamDone(
            type="done", response=self._respond(state, turn.thread_id, body.lang)
        )

    def _open(self, body: ChatRequest) -> tuple[UUID, str, dict]:
        """대화 ID, 대화 기억 키, 그래프 설정을 정한다."""
        thread_id = body.thread_id or uuid4()
        # 다른 사용자의 대화 ID로 대화를 이어 읽지 못하도록 사용자 ID와 묶어 저장한다.
        key = f"{body.user_id}:{thread_id}"
        config = {
            "configurable": {"thread_id": key},
            # 단계 한도는 그래프가 먼저 멈춘다. 이 값은 그보다 넉넉한 안전장치다(노드 4개 + 단계마다 2개).
            "recursion_limit": self._settings.agent_max_steps * 2 + 10,
        }
        return thread_id, key, config

    def _begin(
        self, thread_id: UUID, key: str, config: dict, body: ChatRequest
    ) -> _Turn:
        """대화 잠금을 잡고, 그래프 없이 끝낼 턴(대기 초과·턴 상한·데모·키 없음·하루 한도)을 가린다.

        턴 상한과 대기 초과는 LLM을 부르지 않으므로 하루 한도에 세지 않는다.
        """
        registry = conversations
        if not registry.acquire(key, self._settings.agent_thread_wait_seconds):
            logger.warning("같은 대화의 앞 요청이 끝나지 않아 기다리지 않고 답한다")
            return _Turn(
                thread_id=thread_id,
                key=key,
                config=config,
                early=fallback_response(thread_id, body.lang, "busy"),
                conversations=None,
            )
        try:
            early = self._early_reply(thread_id, key, config, body)
        except Exception:
            # 잠금을 잡은 뒤라 예외를 그대로 올리면 잠금이 풀리지 않는다. 시세표 안내로 끝내고 _end가 푼다.
            logger.exception("턴 시작 처리 실패")
            early = fallback_response(thread_id, body.lang, "error")
        return _Turn(
            thread_id=thread_id,
            key=key,
            config=config,
            early=early,
            conversations=registry,
        )

    def _early_reply(
        self, thread_id: UUID, key: str, config: dict, body: ChatRequest
    ) -> ChatResponse | None:
        """그래프 없이 끝낼 턴이면 그 응답(턴 상한·데모·키 없음·하루 한도), 아니면 None."""
        if self._agent is not None and (
            count_turns(self._agent.get_state(config).values.get("messages", []))
            >= self._settings.agent_max_turns
        ):
            logger.warning("한 대화의 최대 턴 수를 넘어 새 대화를 안내한다")
            return fallback_response(thread_id, body.lang, "turn_limit")
        if self._settings.agent_demo_cache and (
            demo := self._demo_reply(config, key, body, thread_id)
        ):
            return demo
        if self._agent is None:
            logger.warning("API 키가 없어 시세표 안내로 답한다")
            return fallback_response(thread_id, body.lang, "error")
        if not daily_usage.take(
            body.user_id,
            self._settings.agent_daily_limit,
            self._settings.agent_user_daily_limit,
        ):
            logger.warning("하루 호출 한도를 넘어 시세표 안내로 답한다")
            return fallback_response(thread_id, body.lang, "daily_limit")
        return None

    def _end(self, turn: _Turn) -> None:
        if turn.conversations is None:
            return
        saver = self._agent.checkpointer if self._agent is not None else None
        try:
            stored = saver is not None and saver.get_tuple(turn.config) is not None
        except Exception:
            logger.exception("대화 기억을 읽지 못했다")
            stored = False
        turn.conversations.release(
            turn.key,
            saver.delete_thread if saver else None,
            self._settings.agent_thread_ttl_seconds,
            self._settings.agent_max_threads,
            stored=stored,
        )

    @staticmethod
    def _input(body: ChatRequest) -> dict:
        return {"messages": [HumanMessage(body.message)], "lang": body.lang}

    def _known_amounts(self, config: dict) -> set:
        """지금까지 대화 기억에 남은 도구 결과·사용자 메시지의 금액(finalize와 같은 기준)."""
        return known_amounts(self._agent.get_state(config).values.get("messages", []))

    @staticmethod
    def _respond(state: dict, thread_id: UUID, lang: Lang) -> ChatResponse:
        if failure := read_failure(state):
            logger.warning("에이전트가 답하지 못해 시세표 안내로 답한다: %s", failure)
            return fallback_response(thread_id, lang, "error")
        reply = read_reply(state)
        return ChatResponse(
            thread_id=thread_id,
            fallback=None,
            disclaimer=DISCLAIMER[lang],
            cards=read_cards(state),
            exclusions=read_exclusions(state),
            # card_reasons는 cards에 합쳐졌으므로 응답에 따로 내보내지 않는다.
            # 안전 판정은 건강 정보라 화면에 보내지 않는다(설계 문서 4.8).
            **reply.model_dump(exclude={"card_reasons", "safety_stop"}),
        )

    def _demo_reply(
        self, config: dict, key: str, body: ChatRequest, thread_id: UUID
    ) -> ChatResponse | None:
        """대본과 같은 메시지면 녹화 응답(설계 문서 13장 데모 고정, ADR 0015). 아니면 None.

        API 키가 없거나 Claude에 닿지 않아도 대본 안에서는 답하고, 하루 한도에 세지 않는다. 키가 없으면
        대화 기억을 볼 수 없어, 대본이 처음 보는 대화 ID는 모두 새 대화로 본다. 녹화본이 잘못돼도(파일 형식,
        녹화 뒤 바뀐 응답 형식) 데모 때문에 대화가 실패하지 않도록 로그만 남기고 실제 경로로 넘어간다.
        """
        agent = self._agent
        try:
            is_new = body.thread_id is None or (
                agent is None or not agent.get_state(config).values.get("messages")
            )
            hit = demo_sessions.take(load_demo(), key, is_new, body.message, body.lang)
            if hit is None:
                return None
            script, turn = hit
            response = demo_response(turn, thread_id)
            if agent is not None:
                # 대본에서 벗어난 다음 턴을 실제 Claude가 앞 맥락과 함께 이어 답하게 한다.
                agent.update_state(
                    config,
                    {"messages": recorded_messages(turn), "lang": body.lang},
                    as_node="finalize",
                )
        except Exception:
            logger.exception("데모 녹화 응답을 쓰지 못해 실제 경로로 답한다")
            return None
        logger.info("데모 대본 %s의 녹화 응답으로 답한다", script.id)
        return response


# 문장이나 줄이 끝나는 곳. 금액 검사는 문장·줄 단위라 여기까지만 보낸다.
_SEGMENT_END = re.compile(r"[\n。！？!?]|[.](?=\s)")
# 응답 칸에서 answer 뒤에 오는 칸들. 이 중 하나가 보이면 answer는 다 나온 것이다.
_FIELDS = list(AgentReply.model_fields)
_AFTER_ANSWER = set(_FIELDS[_FIELDS.index("answer") + 1 :])


class _AnswerStream:
    """LLM 출력 조각에서 응답 칸 JSON의 answer만 뽑아, 새로 보낼 글을 돌려준다.

    응답 칸은 구조화 출력(JSON)이라 조각을 모아 부분 JSON으로 읽는다(langchain-core
    `parse_partial_json`). 도구를 부르는 단계의 메시지에는 answer가 없어 아무것도 내지 않는다.
    빈 응답 재요청처럼 같은 턴에 메시지가 새로 시작되면 처음부터 다시 모은다.
    """

    def __init__(self, known: Callable[[], set]) -> None:
        self._known = known
        self._message_id: str | None = None
        self._raw = ""
        self._sent = ""
        self._amounts: set | None = None

    def feed(self, chunk: AIMessageChunk) -> str:
        if chunk.id != self._message_id:
            self._message_id, self._raw = chunk.id, ""
        if chunk.tool_call_chunks or not chunk.text:
            return ""
        self._raw += chunk.text
        parsed = parse_partial_json(self._raw)
        answer = parsed.get("answer") if isinstance(parsed, dict) else None
        if not isinstance(answer, str):
            return ""
        if parsed.keys() & _AFTER_ANSWER:
            # 다음 칸이 시작됐으면 답변이 끝난 것이라 끝까지 보낸다.
            ends = [len(answer)]
        else:
            ends = [m.end() for m in _SEGMENT_END.finditer(answer)]
        if not ends:
            return ""
        if self._amounts is None:
            # 마지막 LLM 단계에서 처음 한 번 읽는다. 그때는 이번 턴 도구 결과가 모두 기록돼 있다.
            self._amounts = self._known()
        screened, _ = drop_unknown_amounts(answer[: ends[-1]], self._amounts)
        if not screened.startswith(self._sent):
            # 앞서 보낸 글과 이어지지 않으면 더 보내지 않는다. done의 answer가 최종이다.
            return ""
        text, self._sent = screened[len(self._sent) :], screened
        return text


def get_chat_service(
    agent: Annotated[CompiledStateGraph | None, Depends(get_agent)],
    settings: Annotated[AgentSettings, Depends(get_settings)],
) -> Iterator[ChatService]:
    """요청마다 서비스를 만들고, 응답이 끝나면(연결이 끊긴 경우 포함) 서비스가 연 스트림을 닫는다.

    router는 `scope="request"`로 받아 응답을 다 보낸 뒤에 닫히게 한다.
    """
    service = ChatService(agent, settings)
    try:
        yield service
    finally:
        service.close()
