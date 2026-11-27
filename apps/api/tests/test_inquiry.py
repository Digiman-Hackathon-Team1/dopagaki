"""예약 문의 문장이 web buildInquiryMessages와 같은지 대조한다."""

import json
from pathlib import Path

import pytest

from api.domain.catalog import get_catalog
from api.domain.inquiry import inquiry_messages

FIXTURE = Path(__file__).parent / "fixtures" / "inquiry-expected.json"
CASES = json.loads(FIXTURE.read_text("utf-8"))


def test_fixture_covers_japanese_support_and_missing_menu() -> None:
    catalog = get_catalog()
    places = {c["placeId"] for c in CASES}
    support = {catalog.place(p).japanese_support for p in places}
    assert support == {True, False}
    assert any("potenza" not in catalog.place(p).prices for p in places)


@pytest.mark.parametrize(
    "case", CASES, ids=lambda c: f"{c['placeId']}-{'+'.join(c['menuIds'])}"
)
def test_inquiry_matches_web(case: dict) -> None:
    catalog = get_catalog()
    form = case["form"]
    ko, ja = inquiry_messages(
        catalog.place(case["placeId"]),
        catalog.menus,
        case["menuIds"],
        date=form["date"],
        time_slot=form["timeSlot"],
        people=form["people"],
        first_visit=form["firstVisit"],
        guest_name=form["guestName"],
    )
    assert ko == case["ko"]
    assert ja == case["ja"]
