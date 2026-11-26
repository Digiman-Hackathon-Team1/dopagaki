"""가격 계산. web `apps/web/src/lib/calc.ts`의 가격 계산만 옮겼다(ADR 0007).

web 순위표와 숫자가 같아야 하므로(설계 문서 4.7) 반올림까지 calc.ts와 같게 한다. 결과는
calc.ts로 만든 기대값(`tests/fixtures/calc-expected.json`)과 대조한다.

- 한국 가격은 항상 부가세 포함으로 계산한다(web 기본값과 같음).
- 절약액 = 일본 주요 5곳 세금 포함 공식가 평균 - 한국 가격(부가세 포함)의 엔화 환산.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from api.domain.catalog import AreaId, Catalog, GeoPoint, Menu, Offer, Place

EARTH_RADIUS_M = 6371000
WALK_METERS_PER_MINUTE = 75
# 기준 위치가 없거나 한국 밖이면 서울 시청을 기준으로 거리를 잰다(calc.ts DEFAULT_SEOUL_CENTER).
DEFAULT_SEOUL_CENTER = GeoPoint(lat=37.5665, lng=126.978)

SortId = Literal["savings", "distance", "rating"]


def js_round(value: float) -> int:
    """JavaScript `Math.round`: 가장 가까운 정수, 0.5는 +무한대 쪽으로 올린다.

    Python `round`는 0.5를 짝수 쪽으로 보내 calc.ts와 1씩 어긋날 수 있다.
    """
    floor = math.floor(value)
    return floor + 1 if value - floor >= 0.5 else floor


def _to_rad(deg: float) -> float:
    # math.radians는 deg * (pi / 180)이라 calc.ts의 (deg * PI) / 180과 마지막 자리가 다를 수 있다.
    return (deg * math.pi) / 180


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    d_lat = _to_rad(lat2 - lat1)
    d_lng = _to_rad(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(_to_rad(lat1)) * math.cos(_to_rad(lat2)) * math.sin(d_lng / 2) ** 2
    )
    return js_round(2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def _check_rate(krw_per_100_jpy: float) -> None:
    if not math.isfinite(krw_per_100_jpy) or krw_per_100_jpy <= 0:
        raise ValueError("Invalid exchange rate")


def krw_to_jpy(krw: float, krw_per_100_jpy: float) -> int:
    _check_rate(krw_per_100_jpy)
    return js_round((krw * 100) / krw_per_100_jpy)


def jpy_to_krw(jpy: float, krw_per_100_jpy: float) -> int:
    _check_rate(krw_per_100_jpy)
    return js_round((jpy * krw_per_100_jpy) / 100)


def is_point_in_south_korea(point: GeoPoint | None) -> bool:
    """calc.ts `isPointInSouthKorea`와 같은 경계 상자 판정."""
    if point is None or not (math.isfinite(point.lat) and math.isfinite(point.lng)):
        return False
    lat, lng = point.lat, point.lng
    if 33.05 <= lat <= 33.65 and 126.05 <= lng <= 127.05:
        return True
    if 37.1 <= lat <= 37.65 and 130.7 <= lng <= 132.0:
        return True
    if 34.05 <= lat <= 38.65 and 124.5 <= lng <= 129.65:
        return not (lat < 34.78 and lng > 129.1)
    return False


@dataclass(frozen=True)
class PriceRow:
    """병원 한 곳 × 시술 하나의 가격 비교(calc.ts `ComputedRow`의 가격 부분)."""

    place: Place
    menu: Menu
    offer: Offer
    base_krw: int
    vat_krw: int
    total_krw: int
    total_jpy: int
    jp_benchmark_jpy: int
    savings_jpy: int
    savings_pct: int
    distance_meters: int
    walk_minutes: int


def compute_row(
    place: Place,
    menu: Menu,
    krw_per_100_jpy: float,
    vat_rate: float,
    origin: GeoPoint,
) -> PriceRow | None:
    offer = place.prices.get(menu.id)
    if offer is None:
        return None

    apply_vat = place.vat_excluded and menu.vat_applies_in_korea
    vat_krw = js_round(offer.krw * vat_rate) if apply_vat else 0
    total_krw = offer.krw + vat_krw
    total_jpy = krw_to_jpy(total_krw, krw_per_100_jpy)
    benchmark = menu.jp_benchmark_jpy
    savings_jpy = benchmark - total_jpy
    savings_pct = js_round(savings_jpy / benchmark * 100) if benchmark > 0 else 0
    distance = haversine_meters(origin.lat, origin.lng, place.lat, place.lng)

    return PriceRow(
        place=place,
        menu=menu,
        offer=offer,
        base_krw=offer.krw,
        vat_krw=vat_krw,
        total_krw=total_krw,
        total_jpy=total_jpy,
        jp_benchmark_jpy=benchmark,
        savings_jpy=savings_jpy,
        savings_pct=savings_pct,
        distance_meters=distance,
        walk_minutes=max(1, js_round(distance / WALK_METERS_PER_MINUTE)),
    )


def _sort_key(sort: SortId) -> Callable[[PriceRow], tuple[float, ...]]:
    # calc.ts buildRankedRows의 비교 함수와 같은 순서. 두 쪽 모두 안정 정렬이라 동률은 데이터 순서를 따른다.
    if sort == "distance":
        return lambda r: (r.distance_meters, r.total_krw)
    if sort == "rating":
        return lambda r: (-r.place.ai_rating, -r.savings_jpy, r.distance_meters)
    return lambda r: (-r.savings_jpy, r.distance_meters)


def rank_rows(
    catalog: Catalog,
    menu_id: str,
    sort: SortId = "savings",
    origin: GeoPoint | None = None,
    japanese_only: bool = False,
    area: AreaId | None = None,
    krw_per_100_jpy: float | None = None,
) -> list[PriceRow]:
    """calc.ts `buildRankedRows`(부가세 포함)와 같은 거르기·정렬.

    환율을 주지 않으면 스냅샷 환율을 쓴다.
    """
    menu = catalog.menu(menu_id)
    rate = (
        krw_per_100_jpy if krw_per_100_jpy is not None else catalog.fx.krw_per_100_jpy
    )
    base = origin if is_point_in_south_korea(origin) else DEFAULT_SEOUL_CENTER
    rows = []
    for place in catalog.places:
        if place.category != menu.category:
            continue
        if area is not None and place.area != area:
            continue
        if japanese_only and not place.japanese_support:
            continue
        row = compute_row(place, menu, rate, catalog.meta.vat_rate, base)
        if row is not None:
            rows.append(row)
    return sorted(rows, key=_sort_key(sort))
