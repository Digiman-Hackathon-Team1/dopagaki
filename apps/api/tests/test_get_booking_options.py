"""get_booking_options 도구(설계 문서 9장, FR-05)."""

import re
from pathlib import Path

import pytest

from api.agent.tools.get_booking_options import (
    GetBookingOptionsArtifact,
    get_booking_options,
)
from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_CHANNELS = REPO_ROOT / "apps" / "web" / "src" / "data" / "clinic-channels.ts"


def call(**args) -> tuple[str, GetBookingOptionsArtifact]:
    message = get_booking_options.invoke(
        {
            "type": "tool_call",
            "name": "get_booking_options",
            "args": args,
            "id": "c1",
        }
    )
    return message.content, GetBookingOptionsArtifact.model_validate(message.artifact)


def web_channels(place_id: str, lang: str) -> list[tuple[str, str]]:
    """web clinic-channels.ts의 CLINIC_CHANNELS 원문에서 (채널 종류, 링크)를 화면 순서대로 읽는다.

    순서와 언어별 고르기는 web getClinicChannels와 같다: 카카오, LINE, 웹 예약, 네이버 예약, 인스타그램.
    웹 예약과 인스타그램은 그 언어의 링크가 있으면 그것을, 없으면 다른 언어 링크를 쓴다.
    """
    text = WEB_CHANNELS.read_text("utf-8")
    block = re.search(rf'"{re.escape(place_id)}": \{{(.*?)\}}', text, re.DOTALL)
    if block is None:
        return []
    entry = dict(re.findall(r'"(\w+)": "(https?://[^"]+)"', block.group(1)))

    def pick(own: str, other: str) -> str | None:
        return entry.get(own) or entry.get(other)

    ja = lang == "ja"
    found = [
        ("kakao", entry.get("kakao")),
        ("line", entry.get("line")),
        (
            "direct_reserve",
            pick("reservationJa", "reservationKo")
            if ja
            else pick("reservationKo", "reservationJa"),
        ),
        ("naver_booking", entry.get("naverBooking")),
        (
            "instagram",
            pick("instagramJa", "instagram")
            if ja
            else pick("instagram", "instagramJa"),
        ),
    ]
    return [(kind, url) for kind, url in found if url]


@pytest.mark.skipif(not WEB_CHANNELS.exists(), reason="web 데이터가 없는 환경")
@pytest.mark.parametrize("place", get_catalog().places, ids=lambda p: p.id)
def test_card_links_match_web_clinic_channels(place) -> None:
    """완료 기준: 예약 카드의 링크가 clinic-channels 데이터와 같다. 모든 병원, 언어별 링크와 순서까지 본다."""
    _, artifact = call(place_id=place.id)
    channels = artifact.booking.channels

    assert [(c.kind, c.url_ja) for c in channels] == web_channels(place.id, "ja")
    assert [(c.kind, c.url_ko) for c in channels] == web_channels(place.id, "ko")


def test_language_specific_links_are_kept() -> None:
    """일본어 계정이 따로 있는 병원은 일본어 화면에 그 계정이 간다(예: 톡스앤필 명동 인스타그램)."""
    _, artifact = call(place_id="toxnfill-myeongdong")
    [instagram] = [c for c in artifact.booking.channels if c.kind == "instagram"]

    assert instagram.url_ja == "https://www.instagram.com/toxnfill_japan_official/"
    assert instagram.url_ko == "https://www.instagram.com/toxnfill_official/"


def test_booking_card_for_first_clinic() -> None:
    content, artifact = call(
        place_id="vands-hongdae",
        menu_ids=["shrink-300"],
        preferred_date="11/28",
        time_slot="afternoon",
    )
    card = artifact.booking

    assert [c.kind for c in card.channels] == ["kakao", "line", "instagram"]
    assert card.menu_ids == ["shrink-300"]
    assert card.official_site_url == get_catalog().place("vands-hongdae").source_url
    assert "희망 일시: 11/28 / 오후 (13:00~17:00)" in card.inquiry_ko
    assert "슈링크" in card.inquiry_ko
    assert "ご希望日時：11/28 / 午後" in card.inquiry_ja
    # 이름은 받지 않으므로 빈칸 문구가 남는다.
    assert "(여권 영문명 입력)" in card.inquiry_ko
    # LLM에는 링크와 문의 문장을 보내지 않는다.
    assert "http" not in content
    assert card.inquiry_ko not in content
    assert "does not book or pay" in content


def test_menu_without_price_is_left_out_and_reported() -> None:
    place = next(p for p in get_catalog().places if "potenza" not in p.prices)

    content, artifact = call(place_id=place.id, menu_ids=["potenza"])

    assert artifact.booking.menu_ids == []
    assert "상담 후 결정" in artifact.booking.inquiry_ko
    assert "no price in the data for potenza" in content


def test_central_channel_is_flagged() -> None:
    place = next(p for p in get_catalog().places if p.central_channel)

    content, artifact = call(place_id=place.id)

    assert artifact.booking.central_channel
    assert "name this branch" in content


def test_order_sheet_has_card_prices_and_default_requests() -> None:
    """접수 메모는 web 예약 시트와 같은 틀이고, 합계는 병원 추천 카드 금액과 같다."""
    row = next(
        r for r in rank_rows(get_catalog(), "shrink-300") if r.place.vat_excluded
    )
    content, artifact = call(place_id=row.place.id, menu_ids=["shrink-300"])
    card = artifact.booking

    assert (card.order_total_krw, card.order_total_jpy) == (
        row.total_krw,
        row.total_jpy,
    )
    assert f"예상 합계: ₩{row.total_krw:,}" in card.order_sheet_ko
    assert "+ VAT 10%" in card.order_sheet_ko
    assert "추가 시술 권유는 정중히 사양" in card.order_sheet_ko
    assert "최종 총액을 시술 전에 먼저 알려주세요" in card.order_sheet_ko
    assert "마취크림을 충분히" not in card.order_sheet_ko
    assert f"目安合計: ¥{row.total_jpy:,}" in card.order_sheet_ja
    # LLM에는 합계만 알리고 메모 본문은 보내지 않는다.
    assert f"₩{row.total_krw:,}" in content
    assert card.order_sheet_ko not in content


def test_order_requests_follow_user() -> None:
    _, artifact = call(
        place_id="vands-hongdae",
        menu_ids=["shrink-300"],
        order_requests=["gentle-numbing"],
    )
    sheet = artifact.booking.order_sheet_ko

    assert "마취크림을 충분히" in sheet
    assert "추가 시술 권유" not in sheet


def test_card_id_is_accepted_and_its_menu_is_used() -> None:
    _, artifact = call(place_id="vands-hongdae:shrink-300")

    assert artifact.booking.place_id == "vands-hongdae"
    assert artifact.booking.menu_ids == ["shrink-300"]
    assert "슈링크" in artifact.booking.inquiry_ko


def test_channel_names_not_ids_go_to_model() -> None:
    place = next(
        p
        for p in get_catalog().places
        if any(c.kind == "naver_booking" for c in p.channels)
    )

    content, _ = call(place_id=place.id)

    assert "Naver Booking" in content
    assert "naver_booking" not in content


def test_unknown_place_returns_error_to_model() -> None:
    with pytest.raises(ValueError, match="Unknown place_id"):
        get_booking_options.invoke({"place_id": "no-such-clinic:shrink-300"})


@pytest.mark.parametrize(("date", "saved"), [("11/05", "11/5"), (" 2/29 ", "2/29")])
def test_date_is_normalized(date: str, saved: str) -> None:
    _, artifact = call(place_id="vands-hongdae", preferred_date=date)

    assert f"희망 일시: {saved} /" in artifact.booking.inquiry_ko


@pytest.mark.parametrize(
    "date", ["2026-11-28", "13/1", "11/32", "2/30", "4/31", "내일"]
)
def test_date_must_be_month_slash_day(date: str) -> None:
    with pytest.raises(ValueError):
        get_booking_options.invoke(
            {"place_id": "vands-hongdae", "preferred_date": date}
        )


def test_card_id_with_unknown_menu_is_an_error() -> None:
    with pytest.raises(ValueError, match="Unknown menu"):
        get_booking_options.invoke({"place_id": "vands-hongdae:nope"})


def test_card_id_with_spaces_and_explicit_empty_menus() -> None:
    _, artifact = call(place_id=" vands-hongdae : shrink-300 ", menu_ids=[])

    assert artifact.booking.place_id == "vands-hongdae"
    # 빈 배열은 "시술 미정"이다. card_id의 시술로 바꾸지 않는다.
    assert artifact.booking.menu_ids == []
    assert artifact.booking.order_total_krw == 0


def test_fullwidth_date_digits_are_rejected() -> None:
    with pytest.raises(ValueError):
        get_booking_options.invoke(
            {"place_id": "vands-hongdae", "preferred_date": "１１/２８"}
        )
