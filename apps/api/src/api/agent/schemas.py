"""에이전트 응답 형식.

`AgentReply`는 Claude 구조화 출력(`output_config.format`)에 넘기는 스키마이고,
`ChatRequest`·`ChatResponse`는 `/api/agent/chat`의 API 계약이다.
응답 칸은 설계 문서 4.8을 따른다. 카드 칸은 도구가 생기는 T2부터 추가한다.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

Lang = Literal["ja", "ko"]


class ReplyQuestion(BaseModel):
    """먼저 물어야 할 때의 질문 하나와 선택지."""

    question: str = Field(description="사용자에게 물을 질문 한 문장")
    options: list[str] = Field(
        description="빠른 답변 버튼 문구. '직접 입력'은 화면이 붙이므로 넣지 않는다"
    )
    multi_select: bool = Field(description="여러 개를 고를 수 있으면 true")


class AgentReply(BaseModel):
    """Claude가 한 턴의 마지막에 돌려주는 응답 칸."""

    condition_summary: str = Field(
        description="이해한 조건 한 줄. 파악한 조건이 없으면 빈 문자열"
    )
    answer: str = Field(description="답변 본문. 표와 목록을 쓸 수 있다")
    questions: list[ReplyQuestion] = Field(
        description="답하기 전에 먼저 물어야 할 질문 묶음(2~3개). 없으면 빈 배열"
    )
    suggestions: list[str] = Field(
        description="사용자가 이어서 보낼 만한 다음 질문 3개"
    )
    cautions: list[str] = Field(
        description="모르는 조건, 약한 근거 같은 주의사항. 없으면 빈 배열"
    )


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)


class ChatRequest(CamelModel):
    user_id: UUID = Field(description="브라우저가 만든 익명 사용자 ID")
    thread_id: UUID | None = Field(
        default=None, description="대화 ID. 없으면 새 대화를 시작한다"
    )
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ] = Field(description="이번 턴의 사용자 메시지. 앞뒤 공백을 지운 뒤 1~2000자")
    lang: Lang = Field(description="응답 언어")


class ResponseModel(CamelModel):
    # AgentReply에 필드가 늘었는데 응답 모델에 없으면 조용히 버리지 않고 오류를 낸다.
    model_config = ConfigDict(extra="forbid")


class Question(ResponseModel):
    question: str
    options: list[str]
    multi_select: bool


class ChatResponse(ResponseModel):
    thread_id: UUID
    condition_summary: str
    answer: str
    questions: list[Question]
    suggestions: list[str]
    cautions: list[str]
    disclaimer: str
