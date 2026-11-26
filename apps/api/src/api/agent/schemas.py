"""에이전트 응답 형식.

`AgentReply`는 Claude 구조화 출력(`output_config.format`)에 넘기는 스키마이고,
`ChatRequest`·`ChatResponse`는 `/api/agent/chat`의 API 계약이다.
응답 칸은 설계 문서 4.8을 따른다.

카드 금액은 LLM이 쓰지 않는다. 도구가 카드 데이터(`RecommendationCardData`)를 만들고, LLM은
`card_reasons`로 카드마다 추천 이유만 쓴다. finalize가 둘을 합쳐 `RecommendationCard`를
만든다(ADR 0009).
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

from api.domain.catalog import AreaId
from api.domain.price_tags import PriceTag

Lang = Literal["ja", "ko"]


class ReplyQuestion(BaseModel):
    """먼저 물어야 할 때의 질문 하나와 선택지."""

    question: str = Field(description="사용자에게 물을 질문 한 문장")
    options: list[str] = Field(
        description="빠른 답변 버튼 문구. '직접 입력'은 화면이 붙이므로 넣지 않는다"
    )
    multi_select: bool = Field(description="여러 개를 고를 수 있으면 true")


class CardReason(BaseModel):
    """카드 하나의 추천 이유."""

    card_id: str = Field(description="도구 결과에 있는 card_id를 그대로 쓴다")
    reason: str = Field(
        description="이 병원을 권하는 이유 한 줄. 예: 가장 싸고 일본어 상담이 가능해요"
    )


class AgentReply(BaseModel):
    """Claude가 한 턴의 마지막에 돌려주는 응답 칸."""

    condition_summary: str = Field(
        description="이해한 조건 한 줄. 파악한 조건이 없으면 빈 문자열"
    )
    answer: str = Field(description="답변 본문. 표와 목록을 쓸 수 있다")
    card_reasons: list[CardReason] = Field(
        description=(
            "이번 턴 도구 결과의 카드마다 추천 이유 한 줄. card_id는 도구 결과에 있는 것만 쓴다. "
            "카드 금액은 앱이 도구 결과로 채우므로 여기에 쓰지 않는다. 카드가 없으면 빈 배열"
        )
    )
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


class RecommendationCardData(ResponseModel):
    """search_clinics가 만드는 병원 추천 카드의 데이터. 금액은 모두 calc.py 결과다."""

    card_id: str = Field(description="병원 ID와 시술 ID를 묶은 카드 ID")
    place_id: str
    menu_id: str
    clinic_name_ja: str
    clinic_name_ko: str
    menu_name_ja: str
    menu_name_ko: str
    area: AreaId
    station_ja: str
    station_ko: str
    japanese_support: bool
    base_krw: int = Field(description="병원 표시 가격(원)")
    vat_krw: int = Field(
        description="더한 부가세(원). 표시 가격에 이미 포함돼 있으면 0"
    )
    total_krw: int = Field(description="부가세 포함 가격(원)")
    total_jpy: int = Field(description="부가세 포함 가격의 엔화 환산")
    krw_per_100_jpy: float = Field(description="환산에 쓴 환율(100엔당 원)")
    fx_date_label: str = Field(description="환율 기준일. 예: 2026.11.25")
    jp_average_jpy: int = Field(description="일본 주요 병원 세금 포함 공식가 평균")
    jp_min_jpy: int
    jp_max_jpy: int
    jp_clinic_count: int = Field(description="일본 평균에 쓴 병원 수")
    savings_jpy: int = Field(description="일본 평균 대비 절약액(엔). 음수면 더 비쌈")
    savings_pct: int
    distance_meters: int | None = Field(
        description="기준 위치에서의 직선거리. 기준 위치가 없으면 null"
    )
    walk_minutes: int | None
    price_tags: list[PriceTag] = Field(
        description=(
            "가격 조건 태그(설계 4.7). first_visit 첫 방문·체험가, event 이벤트가·특가, "
            "day_limited 요일 한정, app_benefit 앱·카카오 혜택가, anesthesia 마취비 별도·확인, "
            "extra_fee 시술 팁 별도. 절약액 계산에는 반영하지 않는다"
        )
    )
    price_condition_ja: str = Field(description="가격 조건(첫 방문 한정, 이벤트가 등)")
    price_condition_ko: str
    price_note_ja: str = Field(description="가격 출처 메모")
    price_note_ko: str
    source_url: str = Field(description="병원 공식 가격 출처")


ExcludeReason = Literal["needle", "downtime", "area", "japanese", "budget"]


class ExclusionData(ResponseModel):
    """search_clinics 거르기로 빠진 병원 수(설계 문서 9장, FR-03)."""

    menu_id: str
    reason: ExcludeReason = Field(
        description=(
            "needle 바늘 시술 기피, downtime 회복 기간 초과(이 둘은 시술 전체가 빠짐), "
            "area 다른 지역, japanese 일본어 미지원, budget 예산 초과"
        )
    )
    clinic_count: int = Field(description="이 사유로 빠진 병원 수")


class Exclusion(ExclusionData):
    search_id: str = Field(
        description="어느 검색의 제외 사유인지. 같은 턴의 카드 searchId와 같다"
    )


class RecommendationCard(RecommendationCardData):
    kind: Literal["recommendation"]
    search_id: str = Field(
        description=(
            "어느 검색의 카드인지(이번 턴 검색 순서, 예: s1). 지점·지역을 비교하면 검색마다 값이 "
            "다르므로 화면이 묶어 보여 줄 수 있다. 같은 병원이 두 검색에 모두 나오면 카드도 두 장이다"
        )
    )
    reason: str | None = Field(
        description="LLM이 쓴 추천 이유 한 줄. LLM이 빠뜨리면 null"
    )


class ChatResponse(ResponseModel):
    thread_id: UUID
    condition_summary: str
    answer: str
    cards: list[RecommendationCard]
    exclusions: list[Exclusion] = Field(
        description="이번 턴 검색에서 거르기로 빠진 병원 수. 없으면 빈 배열"
    )
    questions: list[Question]
    suggestions: list[str]
    cautions: list[str]
    disclaimer: str
