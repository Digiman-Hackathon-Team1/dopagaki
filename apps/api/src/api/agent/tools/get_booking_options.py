"""get_booking_options: 병원의 공식 예약 채널과 병원에 보낼 한·일 문장(설계 문서 9장, 11.2, FR-05).

- 채널 링크: web `clinic-channels.ts` 데이터 그대로다.
- 문의 문장: 방문 전에 채널로 보내는 예약 문의(web `buildInquiryMessages`와 같은 틀).
- 접수 메모: 방문한 날 접수대에서 보여 주는 메모(web 예약 시트의 `buildOrderSheetMessages`와 같은 틀).
  금액은 calc.py로 계산해 병원 추천 카드와 같다.

LLM에는 채널 이름과 합계만 알리고 링크와 문장은 예약 카드로만 내보낸다. LLM이 링크를 옮겨 적다 틀리는
일을 막기 위해서다. 예약과 결제는 대신하지 않는다.
"""

from calendar import monthrange

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from api.agent.schemas import BookingCardData
from api.agent.tools.search_clinics import (
    MenuId,
    booking_channels,
    channel_names,
    get_fx_provider,
)
from api.domain.catalog import get_catalog
from api.domain.inquiry import (
    DEFAULT_ORDER_REQUESTS,
    OrderRequestId,
    TimeSlot,
    inquiry_messages,
    order_sheet_messages,
)

TOOL_NAME = "get_booking_options"


class GetBookingOptionsInput(BaseModel):
    place_id: str = Field(
        min_length=1,
        max_length=80,
        description=(
            "병원 ID. search_clinics 결과 card_id(<place_id>:<menu_id>)의 ':' 앞부분을 그대로 쓴다. "
            "예: card_id=vands-hongdae:shrink-300 이면 vands-hongdae. card_id를 그대로 넣어도 된다. "
            "search_bundle 묶음 card_id와 plan_trip 방문 card_id(<place_id>:<menu_id>+<menu_id>)는 그대로 "
            "넣으면 시술이 모두 들어간다. "
            "ID를 지어내지 않는다"
        ),
    )
    menu_ids: list[MenuId] | None = Field(
        default=None,
        max_length=3,
        description=(
            "문의 문장과 접수 메모에 넣을 시술 ID. 보통 그 카드의 menu_id 하나. null이면 place_id에 넣은 "
            "card_id의 시술을 쓴다. 시술을 정하지 않았으면 빈 배열(문장에 '상담 후 결정'으로 들어감)"
        ),
    )
    preferred_date: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "사용자가 말한 희망 날짜를 월/일로. 예: 11/28. 사용자가 월과 일을 말했을 때만 넣는다. "
            "'내일', '다음 주 금요일'처럼 날짜를 계산해야 하면 오늘 날짜를 모르므로 null로 두고 날짜를 묻는다"
        ),
    )
    time_slot: TimeSlot = Field(
        default="any",
        description=(
            "희망 시간대. any 무관(기본), morning 오전, afternoon 오후, evening 저녁. "
            "사용자가 말했을 때만 바꾼다"
        ),
    )
    people: int = Field(
        default=1,
        ge=1,
        le=5,
        description="방문 인원. 기본 1. 문장은 5명까지 쓸 수 있으니 6명 이상이면 5를 넣고 답변에 알린다",
    )
    first_visit: bool = Field(
        default=True,
        description="이 병원에 처음 가면 true(기본). 사용자가 다녀온 적 있다고 했을 때만 false",
    )
    order_requests: list[OrderRequestId] = Field(
        default_factory=lambda: list(DEFAULT_ORDER_REQUESTS),
        max_length=4,
        description=(
            "접수 메모에 넣을 부탁. 기본은 no-upsell(추가 시술 권유 사양)과 total-first(마취·팁·부가세 포함 "
            "총액 먼저 확인). 통증에 약하다고 하면 gentle-numbing(마취 충분히), 시간이 없다고 하면 "
            "one-hour(1시간 이내 종료)를 더한다. 사용자가 빼 달라고 한 것만 뺀다"
        ),
    )

    @field_validator("preferred_date")
    @classmethod
    def _month_day(cls, value: str | None) -> str | None:
        """월/일만 받고 앞 0과 공백을 지운다. 없는 날짜(2/30 등)는 오류다. 연도를 몰라 2/29는 받는다."""
        if value is None:
            return None
        parts = [p.strip() for p in value.strip().split("/")]
        if len(parts) != 2 or not all(p.isascii() and p.isdigit() for p in parts):
            raise ValueError(
                "preferred_date must be month/day in digits, such as 11/28"
            )
        month, day = (int(p) for p in parts)
        if not 1 <= month <= 12 or not 1 <= day <= monthrange(2024, month)[1]:
            raise ValueError(f"{value!r} is not a real date")
        return f"{month}/{day}"


class GetBookingOptionsArtifact(BaseModel):
    """화면용 결과(LLM에는 보내지 않음). finalize가 예약 카드로 조립한다."""

    booking: BookingCardData


DESCRIPTION = """\
병원 한 곳의 공식 예약·상담 채널(LINE, 카카오톡, 네이버 예약, 인스타그램, 공식 웹 예약)과 한국어·일본어
문장 두 가지를 만든다. 문의 문장은 방문 전에 채널로 보내고, 접수 메모는 방문한 날 접수대에서 보여 준다.
결과는 화면에 예약 카드로 나간다. 채널 링크와 문장은 카드에만 있으니 답변에 URL이나 문장을 옮겨 적지 않는다.
필수 입력: place_id(search_clinics 결과의 card_id 앞부분, 또는 search_bundle 묶음·plan_trip 방문 card_id 그대로). "첫 번째 병원"처럼 앞에서 보여 준 병원을 가리키면
그 결과의 순위로 card_id를 찾는다. 어느 병원인지 모르면 부르지 말고 먼저 묻는다.
기본값: 날짜 미정, 시간 무관, 1명, 첫 방문, 접수 메모 부탁은 추가 권유 사양·총액 먼저 확인. 사용자가 말한 값만
넣는다. 이름은 받지 않는다(문장에 빈칸으로 남음).
예약과 결제는 앱이 대신하지 않는다. 사용자가 채널에서 직접 문의·예약한다고 안내한다.
쓰면 안 되는 경우: 병원을 아직 정하지 않았을 때, 데이터에 없는 병원."""


@tool(
    TOOL_NAME,
    description=DESCRIPTION,
    args_schema=GetBookingOptionsInput,
    response_format="content_and_artifact",
)
def get_booking_options(
    place_id: str,
    menu_ids: list[MenuId] | None = None,
    preferred_date: str | None = None,
    time_slot: TimeSlot = "any",
    people: int = 1,
    first_visit: bool = True,
    order_requests: list[OrderRequestId] | None = None,
) -> tuple[str, dict]:
    catalog = get_catalog()
    # card_id(<place_id>:<menu_id>, 묶음은 <place_id>:<menu_id>+<menu_id>)를 그대로 넣었으면 병원 ID와
    # 시술을 나눠 쓴다. menu_ids를 따로 주지 않았을 때만 card_id의 시술을 쓴다(빈 배열을 주면 시술 미정).
    place_id, _, card_menu = (part.strip() for part in place_id.partition(":"))
    if card_menu and menu_ids is None:
        card_menus = [m.strip() for m in card_menu.split("+")]
        unknown = [m for m in card_menus if m not in {x.id for x in catalog.menus}]
        if unknown:
            raise ValueError(
                f"Unknown menu {card_menu!r} in card_id. Pass place_id only, or a card_id from a "
                "search_clinics or search_bundle result as is."
            )
        if len(card_menus) > 3:
            raise ValueError(
                "At most three treatments fit in one inquiry. Pass place_id with up to three "
                "menu_ids."
            )
        menu_ids = card_menus
    try:
        place = catalog.place(place_id)
    except KeyError:
        raise ValueError(
            f"Unknown place_id {place_id!r}. Use the part before ':' of a card_id from a "
            "search_clinics result. If you do not know which clinic the user means, ask."
        ) from None
    requested = list(dict.fromkeys(menu_ids or []))
    ko, ja = inquiry_messages(
        place,
        catalog.menus,
        requested,
        date=preferred_date or "",
        time_slot=time_slot,
        people=people,
        first_visit=first_visit,
    )
    included = [
        m.id for m in catalog.menus if m.id in requested and m.id in place.prices
    ]
    fx = get_fx_provider().current()
    requests = (
        list(DEFAULT_ORDER_REQUESTS) if order_requests is None else order_requests
    )
    order = order_sheet_messages(
        place,
        catalog.menus,
        requested,
        requests=requests,
        krw_per_100_jpy=fx.krw_per_100_jpy,
        vat_rate=catalog.meta.vat_rate,
    )
    card = BookingCardData(
        place_id=place.id,
        clinic_name_ja=place.name_ja,
        clinic_name_ko=place.name_ko,
        menu_ids=included,
        japanese_support=place.japanese_support,
        channels=booking_channels(place),
        central_channel=place.central_channel,
        official_site_url=place.source_url,
        inquiry_ko=ko,
        inquiry_ja=ja,
        order_sheet_ko=order.ko,
        order_sheet_ja=order.ja,
        order_total_krw=order.total_krw,
        order_total_jpy=order.total_jpy,
        krw_per_100_jpy=fx.krw_per_100_jpy,
        fx_date_label=fx.date_label,
    )

    lines = [
        f"get_booking_options: {place.name_ko} / {place.name_ja}.",
        (
            f"Booking channels: {channel_names(place)}. Their links are on the booking card; "
            "do not write URLs."
        ),
        (
            "The card has two messages, each in Korean and Japanese: an inquiry to send through a "
            "channel before the visit, and an order sheet to show at the reception desk on the day. "
            "Do not paste them into the answer."
        ),
        (
            "Japanese support: yes. The user may send the Korean or the Japanese inquiry."
            if place.japanese_support
            else "Japanese support: no. Tell the user to send the Korean inquiry."
        ),
    ]
    if order.total_krw:
        lines.append(
            f"Order sheet total: ₩{order.total_krw:,} incl. VAT ≈ ¥{order.total_jpy:,} "
            f"(FX ₩{fx.krw_per_100_jpy} per ¥100, {fx.date_label})."
        )
    if not place.channels:
        lines.append(
            "No booking channel in the data. Point the user to the official site on the card."
        )
    if place.central_channel:
        lines.append(
            "The channel is the brand's shared desk; the user must name this branch when booking."
        )
    missing = [m for m in requested if m not in included]
    if missing:
        lines.append(
            f"This clinic has no price in the data for {', '.join(missing)}; both messages leave it out."
        )
    lines.append(
        "Dopa does not book or pay. The user books directly through the channel."
    )
    return "\n".join(lines), GetBookingOptionsArtifact(booking=card).model_dump()
