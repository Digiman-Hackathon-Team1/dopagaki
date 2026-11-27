"""get_booking_options: 병원의 공식 예약 채널과 병원에 보낼 한·일 문의 문장(설계 문서 9장, 11.2, FR-05).

채널 링크는 web `clinic-channels.ts` 데이터 그대로이고, 문의 문장은 web 예약 문의 문장과 같은 틀이다
(`domain/inquiry.py`). LLM에는 채널 종류만 알리고 링크와 문장은 예약 카드로만 내보낸다. LLM이 링크를
옮겨 적다 틀리는 일을 막기 위해서다. 예약과 결제는 대신하지 않는다.
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from api.agent.schemas import BookingCardData
from api.agent.tools.search_clinics import MenuId, booking_channels, channel_names
from api.domain.catalog import get_catalog
from api.domain.inquiry import TimeSlot, inquiry_messages

TOOL_NAME = "get_booking_options"


class GetBookingOptionsInput(BaseModel):
    place_id: str = Field(
        min_length=1,
        max_length=60,
        description=(
            "병원 ID. search_clinics 결과 card_id(<place_id>:<menu_id>)의 ':' 앞부분을 그대로 쓴다. "
            "예: card_id=vands-hongdae:shrink-300 이면 vands-hongdae. ID를 지어내지 않는다"
        ),
    )
    menu_ids: list[MenuId] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "문의 문장에 넣을 시술 ID. 보통 그 카드의 menu_id 하나. 시술을 정하지 않았으면 빈 배열"
            "(문장에 '상담 후 결정'으로 들어감)"
        ),
    )
    preferred_date: str | None = Field(
        default=None,
        pattern=r"^(1[0-2]|[1-9])/(3[01]|[12][0-9]|[1-9])$",
        description=(
            "사용자가 말한 희망 날짜를 월/일로. 예: 11/28. 말하지 않았거나 날짜가 정해지지 않았으면 null"
        ),
    )
    time_slot: TimeSlot = Field(
        default="any",
        description=(
            "희망 시간대. any 무관(기본), morning 오전, afternoon 오후, evening 저녁. "
            "사용자가 말했을 때만 바꾼다"
        ),
    )
    people: int = Field(default=1, ge=1, le=5, description="방문 인원. 기본 1")
    first_visit: bool = Field(
        default=True,
        description="이 병원에 처음 가면 true(기본). 사용자가 다녀온 적 있다고 했을 때만 false",
    )


class GetBookingOptionsArtifact(BaseModel):
    """화면용 결과(LLM에는 보내지 않음). finalize가 예약 카드로 조립한다."""

    booking: BookingCardData


DESCRIPTION = """\
병원 한 곳의 공식 예약·상담 채널(LINE, 카카오톡, 네이버 예약, 인스타그램, 공식 웹 예약)과 병원에 보낼
한국어·일본어 문의 문장을 만든다. 결과는 화면에 예약 카드로 나간다. 채널 링크와 문의 문장은 카드에만 있으니
답변에 URL이나 문의 문장을 옮겨 적지 않는다.
필수 입력: place_id(search_clinics 결과의 card_id 앞부분). "첫 번째 병원"처럼 앞에서 보여 준 병원을 가리키면
그 결과의 순위로 card_id를 찾는다. 어느 병원인지 모르면 부르지 말고 먼저 묻는다.
기본값: 날짜 미정, 시간 무관, 1명, 첫 방문. 사용자가 말한 값만 넣는다. 이름은 받지 않는다(문장에 빈칸으로 남음).
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
) -> tuple[str, dict]:
    catalog = get_catalog()
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
    )

    lines = [
        f"get_booking_options: {place.name_ko} / {place.name_ja}.",
        (
            f"Booking channels: {channel_names(place)}. Their links are on the booking card; "
            "do not write URLs."
        ),
        (
            "Inquiry messages (Korean to send, Japanese to read) are on the card; tell the user to copy "
            "the Korean one and send it through a channel. Do not paste them into the answer."
        ),
        f"Japanese support: {'yes' if place.japanese_support else 'no'}.",
    ]
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
            f"This clinic has no price in the data for {', '.join(missing)}; the inquiry leaves it out."
        )
    lines.append(
        "Dopa does not book or pay. The user books directly through the channel."
    )
    return "\n".join(lines), GetBookingOptionsArtifact(booking=card).model_dump()
