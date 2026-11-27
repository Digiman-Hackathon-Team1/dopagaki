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

from api.domain.catalog import AreaId, ChannelKind
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
    safety_stop: bool = Field(
        description=(
            "사용자가 지금 임신·수유 중이거나, 질환이 있거나, 약을 먹고 있어 병원 추천을 멈추면 true. "
            '"임신 계획은 없어요"처럼 부정하거나 과거·가정의 이야기면 false. 화면에는 보내지 않는다'
        )
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


class JpClinicPrice(ResponseModel):
    """사용자가 다니던 일본 병원의 가격. 데이터의 일본 벤치마크 병원 한 곳이다(설계 문서 4.7)."""

    name_ja: str
    name_ko: str
    plan_ja: str = Field(
        description=(
            "일본 병원의 플랜명. 단위(샷 수, cc, 단위 수)가 들어 있고 한국 메뉴와 단위가 다를 수 있다. "
            "예: ウルトラスキンハイフ 全顔 (650ショット)"
        )
    )
    plan_ko: str
    price_jpy: int = Field(description="세금 포함 공식가(엔)")
    source_url: str = Field(description="일본 병원 공식 가격 출처")
    product_note_ja: str | None = Field(
        description=(
            "이 일본 병원 가격이 한국 시술과 다른 제품일 때의 안내. 예: SBC 리쥬란은 힐러가 아니라 "
            "같은 성분 농도의 눈가용 리쥬란 i 가격이다. 같은 제품이면 null"
        )
    )
    product_note_ko: str | None


class JpClinicSavings(JpClinicPrice):
    savings_jpy: int = Field(
        description="이 일본 병원 가격 대비 절약액(엔). 음수면 한국이 더 비쌈"
    )
    savings_pct: int


class BookingChannel(ResponseModel):
    """병원 공식 예약·상담 채널 하나. web clinic-channels.ts의 링크 그대로다(FR-05)."""

    kind: ChannelKind = Field(
        description=(
            "kakao 카카오톡, line LINE, instagram 인스타그램, naver_booking 네이버 예약, "
            "direct_reserve 공식 웹 예약"
        )
    )
    label_ja: str = Field(description="버튼 문구. 예: LINE公式相談・予約")
    label_ko: str
    url_ja: str = Field(
        description="일본어 화면에서 쓸 링크(일본어 계정·페이지가 있으면 그쪽)"
    )
    url_ko: str


class PriceProof(ResponseModel):
    """가격표 증빙. web 병원 화면의 공식 가격 링크와 같다(설계 문서 4.7)."""

    url_ja: str = Field(description="공식 가격 페이지. 일본어 페이지가 있으면 그쪽")
    url_ko: str
    domain_ja: str = Field(
        description="링크에 표시할 도메인. 예: hongdaejp.vandsclinics.com"
    )
    domain_ko: str
    captured_date: str = Field(description="가격을 확인한 날짜. 예: 2026-11-23")
    verified: bool = Field(
        description="공식 페이지 스크린샷에서 이 가격을 확인했으면 true. false면 링크만 있음"
    )
    matched_text: str | None = Field(
        description="스크린샷에서 찾은 가격 문구(한국어). verified가 false면 null"
    )


RatingScope = Literal["branch", "brand"]
RatingTier = Literal["jp_kr_verified", "kr_verified", "limited"]


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
    jp_clinic: JpClinicSavings | None = Field(
        description=(
            "사용자가 다니던 일본 병원과 직접 비교한 가격과 절약액. 그 병원의 이 시술 가격이 데이터에 "
            "있을 때만 채우고, 없으면 null(일본 평균 대비만 표시)"
        )
    )
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
    rating: float = Field(
        description="후기 평점(5점 만점, web AI 평점과 같음). 순서에는 영향이 없다(sort=rating 제외)"
    )
    rating_scope: RatingScope = Field(
        description="평점 근거. branch 이 지점 후기, brand 같은 브랜드 다른 지점 후기를 참고"
    )
    rating_tier: RatingTier = Field(
        description=(
            "후기 확인 수준. jp_kr_verified 일본·한국 후기 확인, kr_verified 한국 후기만 확인, "
            "limited 확인된 후기가 적음. 후기 수는 데이터에 없다"
        )
    )
    booking_channels: list[BookingChannel] = Field(
        description="공식 예약·상담 채널. 데이터에 없으면 빈 배열"
    )
    central_channel: bool = Field(
        description="브랜드 공통 예약 창구라 예약할 때 지점을 직접 골라야 하면 true"
    )
    proof: PriceProof = Field(description="이 시술 가격의 가격표 증빙 링크")


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


class LocationCardData(ResponseModel):
    """resolve_location이 찾은 기준 위치(설계 문서 4.3, 9장, FR-16). 거리는 이 좌표에서 잰 직선거리다."""

    location_id: str = Field(
        description="위치 ID. 예: area:hongdae(지역), station:홍대입구(역)"
    )
    location_type: Literal["area", "station"] = Field(
        description="area 지역 중심 좌표, station 역 좌표"
    )
    query: str | None = Field(
        description=(
            "위치를 찾은 사용자 표현. 예: 홍대 숙소. resolve_location을 거치지 않은 위치 ID로 "
            "검색했으면 null"
        )
    )
    name_ja: str
    name_ko: str
    lat: float
    lng: float


class LocationCard(LocationCardData):
    kind: Literal["location"]


class JpMatchCardData(ResponseModel):
    """match_jp_treatment가 찾은 일본 시술명 대응(설계 문서 4.3, 9장, FR-09). 찾은 경우에만 카드가 된다."""

    query: str = Field(description="사용자가 말한 일본 시술명. 예: ハイフ")
    menu_id: str = Field(description="대응하는 한국 시술 ID")
    menu_name_ja: str
    menu_name_ko: str
    reason_ja: str = Field(description="한국과 일본에서 이름이 다른 이유 한 줄")
    reason_ko: str
    unit_note_ja: str = Field(description="한국·일본의 단위 차이. 예: 300샷과 650샷")
    unit_note_ko: str
    jp_clinic: JpClinicPrice | None = Field(
        description="사용자가 말한 일본 병원의 이 시술 가격. 말하지 않았거나 데이터에 없으면 null"
    )


class JpMatchCard(JpMatchCardData):
    kind: Literal["jp_match"]


class BookingCardData(ResponseModel):
    """get_booking_options가 만드는 예약 안내(설계 문서 9장, 11.2, FR-05).

    예약과 결제는 앱이 대신하지 않는다. 사용자가 채널을 눌러 문의 문장을 보내고 직접 예약한다.
    """

    place_id: str
    clinic_name_ja: str
    clinic_name_ko: str
    menu_ids: list[str] = Field(
        description="문의 문장에 넣은 시술 ID. 이 병원에 가격이 없는 시술은 빠진다"
    )
    japanese_support: bool
    channels: list[BookingChannel] = Field(
        description="공식 예약·상담 채널. web 예약 시트와 같은 순서다. 없으면 빈 배열(공식 사이트로 안내)"
    )
    central_channel: bool = Field(
        description="브랜드 공통 예약 창구라 예약할 때 지점을 직접 골라야 하면 true"
    )
    official_site_url: str = Field(description="병원 공식 사이트")
    inquiry_ko: str = Field(
        description=(
            "방문 전에 채널로 보낼 한국어 예약 문의(web buildInquiryMessages와 같은 틀). 희망 날짜·시간대·"
            "인원이 들어가고 가격은 없다. 이름 칸은 비워 둔다"
        )
    )
    inquiry_ja: str = Field(description="같은 문의 문장의 일본어판")
    order_sheet_ko: str = Field(
        description=(
            "방문한 날 접수대에서 보여 줄 한국어 메모(web 예약 시트의 접수 메모와 같은 틀). 시술별 공식가, "
            "부가세 포함 예상 합계, 부탁(추가 시술 권유 사양 등)이 들어간다"
        )
    )
    order_sheet_ja: str = Field(description="같은 접수 메모의 일본어판")
    order_total_krw: int = Field(
        description="접수 메모의 부가세 포함 예상 합계(원). 시술이 없으면 0"
    )
    order_total_jpy: int = Field(
        description="같은 합계의 엔화 환산. 시술 하나면 병원 추천 카드의 totalJpy와 같다"
    )
    krw_per_100_jpy: float = Field(description="접수 메모 환산에 쓴 환율(100엔당 원)")
    fx_date_label: str = Field(description="환율 기준일")


class BookingCard(BookingCardData):
    kind: Literal["booking"]


Card = Annotated[
    RecommendationCard | LocationCard | JpMatchCard | BookingCard,
    Field(discriminator="kind"),
]


FallbackReason = Literal["error", "daily_limit"]


class ChatResponse(ResponseModel):
    thread_id: UUID
    fallback: FallbackReason | None = Field(
        description=(
            "에이전트가 답하지 못해 시세표 안내로 대신했으면 그 이유(FR-08). error는 API 키 없음·LLM 오류·"
            "시간 초과(호출별·한 턴 전체)·빈 응답 두 번·응답 형식 오류·단계 한도, daily_limit은 사용자별·전체 하루 호출 한도. 화면은 시세표로 가는 길을 "
            "보여 준다. 정상 답변이면 null"
        )
    )
    condition_summary: str
    answer: str
    cards: list[Card] = Field(
        description=(
            "kind로 종류를 구분한다. jp_match(일본 시술명 대응), location(이번 턴에 찾은 기준 위치), "
            "recommendation(병원 추천), booking(예약 채널과 문의 문장) 순서로 온다"
        )
    )
    exclusions: list[Exclusion] = Field(
        description="이번 턴 검색에서 거르기로 빠진 병원 수. 없으면 빈 배열"
    )
    questions: list[Question]
    suggestions: list[str]
    cautions: list[str]
    disclaimer: str
