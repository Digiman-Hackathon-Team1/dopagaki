"""calc.py가 web calc.ts와 같은 숫자를 내는지 대조한다(설계 문서 13장).

기대값 `fixtures/calc-expected.json`은 `scripts/export-web-snapshot.mts`가 web calc.ts로 만든다.
"""

import json
from pathlib import Path

import pytest

from api.domain.calc import (
    is_point_in_south_korea,
    jpy_to_krw,
    js_round,
    krw_to_jpy,
    rank_rows,
)
from api.domain.catalog import GeoPoint, get_catalog

EXPECTED = json.loads(
    (Path(__file__).parent / "fixtures" / "calc-expected.json").read_text("utf-8")
)
ORIGINS = {
    name: GeoPoint.model_validate(point) if point else None
    for name, point in EXPECTED["origins"].items()
}


def test_fixture_uses_snapshot_exchange_rate() -> None:
    assert EXPECTED["krwPer100Jpy"] == get_catalog().fx.krw_per_100_jpy


@pytest.mark.parametrize("case", EXPECTED["rows"], ids=lambda c: c["menuId"])
def test_rows_match_calc_ts(case: dict) -> None:
    rows = rank_rows(get_catalog(), case["menuId"], origin=ORIGINS[case["origin"]])

    actual = [
        {
            "placeId": r.place.id,
            "baseKrw": r.base_krw,
            "vatKrw": r.vat_krw,
            "totalKrw": r.total_krw,
            "krTotalJpy": r.total_jpy,
            "jpBenchmarkJpy": r.jp_benchmark_jpy,
            "savingsJpy": r.savings_jpy,
            "savingsPct": r.savings_pct,
            "distanceMeters": r.distance_meters,
            "walkMinutes": r.walk_minutes,
            "aiRating": r.place.ai_rating,
        }
        for r in rows
    ]
    assert actual == case["rows"]


@pytest.mark.parametrize(
    "case",
    EXPECTED["orders"],
    ids=lambda c: (
        f"{c['menuId']}-{c['sort']}-{c['origin']}-jp{c['japaneseOnly']}-{c['area']}"
    ),
)
def test_order_matches_calc_ts(case: dict) -> None:
    rows = rank_rows(
        get_catalog(),
        case["menuId"],
        sort=case["sort"],
        origin=ORIGINS[case["origin"]],
        japanese_only=case["japaneseOnly"],
        area=None if case["area"] == "all" else case["area"],
    )

    assert [r.place.id for r in rows] == case["placeIds"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 1), (1.5, 2), (2.5, 3), (-0.5, 0), (-1.5, -1), (2.4999, 2), (-2.6, -3)],
)
def test_js_round_rounds_half_up_like_javascript(value: float, expected: int) -> None:
    assert js_round(value) == expected


def test_currency_conversion() -> None:
    # calc.test.ts와 같은 예: 148,500원 ÷ 849.99원/100엔
    assert krw_to_jpy(148500, 849.99) == 17471
    assert jpy_to_krw(10000, 848.99) == 84899


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
def test_invalid_rate_raises(rate: float) -> None:
    with pytest.raises(ValueError):
        krw_to_jpy(10000, rate)


@pytest.mark.parametrize(
    ("lat", "lng", "inside"),
    [
        (37.5563, 126.9228, True),  # 홍대
        (33.4996, 126.5312, True),  # 제주
        (35.1796, 129.0756, True),  # 부산
        (34.2, 129.3, False),  # 쓰시마
        (35.6812, 139.7671, False),  # 도쿄
    ],
)
def test_point_in_south_korea(lat: float, lng: float, inside: bool) -> None:
    assert is_point_in_south_korea(GeoPoint(lat=lat, lng=lng)) is inside
