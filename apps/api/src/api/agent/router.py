"""`POST /api/agent/chat` (설계 문서 10장).

요청을 받아 `ChatService`에 넘기고 응답을 돌려준다. 한 턴의 처리 순서와 실패 시 시세표 안내(FR-08)는
`api.agent.service`에 있다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from api.agent.schemas import ChatRequest, ChatResponse
from api.agent.service import ChatService, get_chat_service

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/chat", operation_id="postAgentChat")
def post_agent_chat(
    body: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    return service.chat(body)
