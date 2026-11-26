"""`POST /api/agent/chat` (설계 문서 10장)."""

from functools import lru_cache
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from api.agent.config import AgentSettings, get_settings
from api.agent.graph import build_graph, read_cards, read_exclusions, read_reply
from api.agent.schemas import ChatRequest, ChatResponse, Lang
from api.agent.tools import TOOLS

router = APIRouter(prefix="/api/agent", tags=["agent"])

# 대화 기억. 저장소 종류는 배포 위치가 정해진 뒤 결정한다(설계 문서 2장). 서버를 재시작하면 사라진다.
checkpointer = InMemorySaver()

DISCLAIMER: dict[Lang, str] = {
    "ja": "AIの回答は正確でない場合があります。決める前にクリニックに確認してください。",
    "ko": "AI 답변은 정확하지 않을 수 있어요. 결정 전에 병원에 확인하세요.",
}


@lru_cache
def _graph_for(
    model: str, effort: str, max_tokens: int, api_key: str
) -> CompiledStateGraph:
    chat_model = ChatAnthropic(
        model=model,
        api_key=api_key,
        effort=effort,
        max_tokens=max_tokens,
    )
    return build_graph(chat_model, tools=TOOLS, checkpointer=checkpointer)


def get_agent(
    settings: Annotated[AgentSettings, Depends(get_settings)],
) -> CompiledStateGraph:
    """요청 때 그래프와 LLM 클라이언트를 만든다. API 키가 없어도 앱 import는 된다."""
    if (
        settings.anthropic_api_key is None
        or not settings.anthropic_api_key.get_secret_value()
    ):
        raise HTTPException(status_code=503, detail="에이전트를 사용할 수 없습니다")
    return _graph_for(
        settings.agent_model,
        settings.agent_effort,
        settings.agent_max_tokens,
        settings.anthropic_api_key.get_secret_value(),
    )


@router.post(
    "/chat",
    operation_id="postAgentChat",
    responses={503: {"description": "API 키가 없어 에이전트를 사용할 수 없음"}},
)
def post_agent_chat(
    body: ChatRequest,
    agent: Annotated[CompiledStateGraph, Depends(get_agent)],
) -> ChatResponse:
    thread_id = body.thread_id or uuid4()
    # 다른 사용자의 대화 ID로 대화를 이어 읽지 못하도록 사용자 ID와 묶어 저장한다.
    config = {"configurable": {"thread_id": f"{body.user_id}:{thread_id}"}}
    state = agent.invoke(
        {"messages": [HumanMessage(body.message)], "lang": body.lang},
        config,
    )
    reply = read_reply(state)
    return ChatResponse(
        thread_id=thread_id,
        disclaimer=DISCLAIMER[body.lang],
        cards=read_cards(state),
        exclusions=read_exclusions(state),
        # card_reasons는 cards에 합쳐졌으므로 응답에 따로 내보내지 않는다.
        **reply.model_dump(exclude={"card_reasons"}),
    )
