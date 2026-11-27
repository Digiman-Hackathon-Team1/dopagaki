"""`POST /api/agent/chat`, `POST /api/agent/chat/stream` (설계 문서 10장).

요청을 받아 `ChatService`에 넘기고 응답을 돌려준다. 한 턴의 처리 순서와 실패 시 시세표 안내(FR-08)는
`api.agent.service`에 있다.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse

from api.agent.schemas import ChatRequest, ChatResponse, ChatStreamEvent
from api.agent.service import ChatService, get_chat_service

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/chat", operation_id="postAgentChat")
def post_agent_chat(
    body: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service, scope="request")],
) -> ChatResponse:
    return service.chat(body)


@router.post(
    "/chat/stream",
    operation_id="streamAgentChat",
    response_class=EventSourceResponse,
)
def stream_agent_chat(
    body: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service, scope="request")],
) -> Iterator[ChatStreamEvent]:
    """`/chat`과 같은 처리를 SSE로 보낸다. start → delta(답변 글, 0개 이상) → done(응답 전체)."""
    yield from service.stream(body)
