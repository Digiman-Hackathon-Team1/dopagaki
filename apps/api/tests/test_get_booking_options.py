"""get_booking_options 도구(설계 문서 9장, FR-05)."""

import re
from pathlib import Path

import pytest

from api.agent.tools.get_booking_options import (
    GetBookingOptionsArtifact,
    get_booking_options,
)
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


def web_channel_urls(place_id: str) -> set[str]:
    """web clinic-channels.ts의 CLINIC_CHANNELS에서 이 병원 항목의 링크를 직접 읽는다."""
    text = WEB_CHANNELS.read_text("utf-8")
    block = re.search(rf'"{re.escape(place_id)}": \{{(.*?)\}}', text, re.DOTALL)
    assert block, place_id
    return set(re.findall(r'"(https?://[^"]+)"', block.group(1)))


@pytest.mark.skipif(not WEB_CHANNELS.exists(), reason="web 데이터가 없는 환경")
@pytest.mark.parametrize("place", get_catalog().places, ids=lambda p: p.id)
def test_card_links_match_web_clinic_channels(place) -> None:
    """완료 기준: 예약 카드의 링크가 clinic-channels 데이터와 같다. 모든 병원을 확인한다."""
    _, artifact = call(place_id=place.id)
    urls = {c.url_ja for c in artifact.booking.channels} | {
        c.url_ko for c in artifact.booking.channels
    }

    assert urls == web_channel_urls(place.id)


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


def test_unknown_place_returns_error_to_model() -> None:
    with pytest.raises(ValueError, match="Unknown place_id"):
        get_booking_options.invoke({"place_id": "vands-hongdae:shrink-300"})


@pytest.mark.parametrize("date", ["2026-11-28", "13/1", "11/32", "내일"])
def test_date_must_be_month_slash_day(date: str) -> None:
    with pytest.raises(ValueError):
        get_booking_options.invoke(
            {"place_id": "vands-hongdae", "preferred_date": date}
        )
