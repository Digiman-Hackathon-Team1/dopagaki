"""`POST /api/agent/chat`, `POST /api/agent/chat/stream`, 사용자 기억 보기·지우기, 지난 대화 조회 (설계 문서 10장).

요청을 받아 `ChatService`에 넘기고 응답을 돌려준다. 한 턴의 처리 순서와 실패 시 시세표 안내(FR-08)는
`api.agent.service`에 있다.
"""

from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.sse import EventSourceResponse

from api.agent.schemas import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    ThreadHistory,
    UserProfileResponse,
)
from api.agent.service import (
    ChatService,
    ProfileService,
    ThreadService,
    UnknownThreadError,
    get_chat_service,
    get_profile_service,
    get_thread_service,
)

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


UserIdPath = Annotated[
    UUID, Path(alias="userId", description="브라우저가 만든 익명 사용자 ID")
]


@router.get("/users/{userId}/profile", operation_id="getAgentUserProfile")
def get_agent_user_profile(
    user_id: UserIdPath,
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> UserProfileResponse:
    """저장된 사용자 기억. 저장된 게 없으면 모든 칸이 null이거나 빈 배열이다."""
    return service.get(user_id)


@router.delete(
    "/users/{userId}/profile", operation_id="deleteAgentUserProfile", status_code=204
)
def delete_agent_user_profile(
    user_id: UserIdPath,
    service: Annotated[ProfileService, Depends(get_profile_service)],
) -> None:
    """사용자 기억을 모두 지운다. 저장된 게 없어도 204다."""
    service.delete(user_id)


@router.get(
    "/users/{userId}/threads/{threadId}",
    operation_id="getAgentThread",
    responses={404: {"description": "보관된 대화가 없음"}},
)
def get_agent_thread(
    user_id: UserIdPath,
    thread_id: Annotated[UUID, Path(alias="threadId", description="대화 ID")],
    service: Annotated[ThreadService, Depends(get_thread_service)],
) -> ThreadHistory:
    """지난 대화의 턴. 보관 기한이 지났거나, 서버를 재시작했거나, 다른 사용자의 대화 ID면 404다."""
    try:
        return service.get(user_id, thread_id)
    except UnknownThreadError:
        raise HTTPException(404, "보관된 대화가 없다") from None
