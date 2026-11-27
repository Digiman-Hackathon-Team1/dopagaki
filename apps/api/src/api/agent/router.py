"""`POST /api/agent/chat` (설계 문서 10장).

에이전트가 답하지 못하면 오류 대신 시세표 안내를 200으로 돌려준다(FR-08). API 키가 없거나 틀림,
LLM 오류·시간 초과, 한 턴 단계 한도, 사용자별·전체 하루 한도가 그 경우다.
"""

import logging
import threading
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from api.agent.config import AgentSettings, get_settings
from api.agent.demo import DemoSessions, demo_response, load_demo, recorded_messages
from api.agent.graph import (
    build_graph,
    read_cards,
    read_exclusions,
    read_failure,
    read_reply,
)
from api.agent.schemas import ChatRequest, ChatResponse, FallbackReason, Lang
from api.agent.tools import TOOLS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

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


daily_usage = DailyUsage()
demo_sessions = DemoSessions()


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


def _demo_reply(
    agent: CompiledStateGraph | None,
    config: dict,
    key: str,
    body: ChatRequest,
    thread_id: UUID,
) -> ChatResponse | None:
    """대본과 같은 메시지면 녹화 응답(설계 문서 13장 데모 고정, ADR 0015). 아니면 None.

    API 키가 없거나 Claude에 닿지 않아도 대본 안에서는 답하고, 하루 한도에 세지 않는다. 키가 없으면
    대화 기억을 볼 수 없어, 대본이 처음 보는 대화 ID는 모두 새 대화로 본다. 녹화본이 잘못돼도(파일 형식,
    녹화 뒤 바뀐 응답 형식) 데모 때문에 대화가 실패하지 않도록 로그만 남기고 실제 경로로 넘어간다.
    """
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


@router.post("/chat", operation_id="postAgentChat")
def post_agent_chat(
    body: ChatRequest,
    agent: Annotated[CompiledStateGraph | None, Depends(get_agent)],
    settings: Annotated[AgentSettings, Depends(get_settings)],
) -> ChatResponse:
    thread_id = body.thread_id or uuid4()
    # 다른 사용자의 대화 ID로 대화를 이어 읽지 못하도록 사용자 ID와 묶어 저장한다.
    key = f"{body.user_id}:{thread_id}"
    config = {
        "configurable": {"thread_id": key},
        # 단계 한도는 그래프가 먼저 멈춘다. 이 값은 그보다 넉넉한 안전장치다(노드 4개 + 단계마다 2개).
        "recursion_limit": settings.agent_max_steps * 2 + 10,
    }
    if settings.agent_demo_cache and (
        demo := _demo_reply(agent, config, key, body, thread_id)
    ):
        return demo
    if agent is None:
        logger.warning("API 키가 없어 시세표 안내로 답한다")
        return fallback_response(thread_id, body.lang, "error")
    if not daily_usage.take(
        body.user_id, settings.agent_daily_limit, settings.agent_user_daily_limit
    ):
        logger.warning("하루 호출 한도를 넘어 시세표 안내로 답한다")
        return fallback_response(thread_id, body.lang, "daily_limit")
    try:
        state = agent.invoke(
            {"messages": [HumanMessage(body.message)], "lang": body.lang},
            config,
        )
    except Exception:
        logger.exception("에이전트 실행 실패")
        return fallback_response(thread_id, body.lang, "error")
    if failure := read_failure(state):
        logger.warning("에이전트가 답하지 못해 시세표 안내로 답한다: %s", failure)
        return fallback_response(thread_id, body.lang, "error")
    reply = read_reply(state)
    return ChatResponse(
        thread_id=thread_id,
        fallback=None,
        disclaimer=DISCLAIMER[body.lang],
        cards=read_cards(state),
        exclusions=read_exclusions(state),
        # card_reasons는 cards에 합쳐졌으므로 응답에 따로 내보내지 않는다.
        # 안전 판정은 건강 정보라 화면에 보내지 않는다(설계 문서 4.8).
        **reply.model_dump(exclude={"card_reasons", "safety_stop"}),
    )
