"""예약 문의 문장과 접수 메모가 web buildInquiryMessages·buildOrderSheetMessages와 같은지 대조한다."""

import json
from pathlib import Path

import pytest

from api.domain.calc import rank_rows
from api.domain.catalog import get_catalog
from api.domain.inquiry import inquiry_messages, order_sheet_messages

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


ORDER_FIXTURE = Path(__file__).parent / "fixtures" / "order-sheet-expected.json"
ORDER_CASES = json.loads(ORDER_FIXTURE.read_text("utf-8"))


def test_order_fixture_covers_vat_excluded_and_included() -> None:
    catalog = get_catalog()
    places = {c["placeId"] for c in ORDER_CASES}
    assert {catalog.place(p).vat_excluded for p in places} == {True, False}


@pytest.mark.parametrize(
    "case",
    ORDER_CASES,
    ids=lambda c: f"{c['placeId']}-{'+'.join(c['menuIds'])}-{'+'.join(c['guards'])}",
)
def test_order_sheet_matches_web(case: dict) -> None:
    catalog = get_catalog()
    sheet = order_sheet_messages(
        catalog.place(case["placeId"]),
        catalog.menus,
        case["menuIds"],
        requests=case["guards"],
        krw_per_100_jpy=case["krwPer100Jpy"],
        vat_rate=case["vatRate"],
    )
    assert sheet.ko == case["ko"]
    assert sheet.ja == case["ja"]
    assert (sheet.total_krw, sheet.total_jpy) == (case["totalKrw"], case["totalJpy"])


def test_single_menu_order_total_matches_clinic_card() -> None:
    """시술 하나의 접수 메모 합계는 search_clinics 카드 금액(calc.py)과 같다."""
    catalog = get_catalog()
    for row in rank_rows(catalog, "shrink-300")[:20]:
        sheet = order_sheet_messages(
            row.place,
            catalog.menus,
            ["shrink-300"],
            requests=[],
            krw_per_100_jpy=catalog.fx.krw_per_100_jpy,
            vat_rate=catalog.meta.vat_rate,
        )
        assert (sheet.total_krw, sheet.total_jpy) == (row.total_krw, row.total_jpy)
