"""위치 데이터와 해석 규칙(설계 문서 9장, FR-16)."""

import pytest

from api.domain.catalog import get_catalog
from api.domain.locations import get_locations, load_stations, station_names


def test_every_station_in_clinic_text_has_coordinates() -> None:
    """web 데이터에 새 역이 생기면 실패한다.

    고치는 법: 역사마스터 JSON을 받아 `uv run python scripts/build_stations.py <파일> <날짜>`
    """
    known = {s.name_ko for s in load_stations().stations}
    used = {ko for p in get_catalog().places for ko, _ in station_names(p)}
    assert used <= known, f"좌표가 없는 역: {sorted(used - known)}"


def test_station_data_has_source_and_seoul_coordinates() -> None:
    data = load_stations()
    assert "OA-21232" in data.source.name
    assert data.source.license
    for s in data.stations:
        assert 37.4 < s.lat < 37.7 and 126.7 < s.lng < 127.2, s.name_ko
        assert s.lines


def test_station_names_pair_korean_and_japanese() -> None:
    place = next(p for p in get_catalog().places if "마곡나루" in p.station_ko)
    # 일본어 문장의 부연 '(空港鉄道 金浦1駅)'은 짝짓지 않는다.
    assert station_names(place) == [("마곡나루", "麻谷ナル")]


def test_shinchon_uses_line2_station_not_same_name_gyeongui_station() -> None:
    """경의중앙선 신촌역은 2호선 신촌역과 715m 떨어진 다른 역이라 평균에 섞지 않는다."""
    shinchon = next(s for s in load_stations().stations if s.name_ko == "신촌")
    assert shinchon.lines == ("2호선",)


@pytest.mark.parametrize(
    ("query", "location_id"),
    [
        ("홍대 숙소", "area:hongdae"),
        ("弘大のホテル", "area:hongdae"),
        ("hongdae", "area:hongdae"),
        # 더 긴 이름(역)이 안에 든 지역 이름보다 우선한다.
        ("홍대입구역 근처", "station:홍대입구"),
        ("江南駅", "station:강남"),
        ("압구정로데오", "station:압구정로데오"),
        ("동대문역사문화공원", "station:동대문역사문화공원"),
        # 지역의 첫 이름은 지역, 나머지 이름은 역이다.
        ("신촌", "area:sinchon"),
        ("신촌역", "station:신촌"),
        ("강남", "area:gangnam"),
        ("공덕 숙소", "station:공덕"),
        ("신사", "station:신사"),
        # '김포공항권'의 '권'은 뗀다.
        ("김포공항", "area:magok"),
        ("ｈｏｎｇｄａｅ", "area:hongdae"),
    ],
)
def test_resolve_found(query: str, location_id: str) -> None:
    result = get_locations().resolve(query)
    assert result.status == "found"
    assert [loc.id for loc in result.locations] == [location_id]


@pytest.mark.parametrize("query", ["이태원", "도쿄", "ホンデ", "숙소"])
def test_resolve_unknown(query: str) -> None:
    result = get_locations().resolve(query)
    assert (result.status, result.locations) == ("unknown", [])


def test_resolve_two_places_is_ambiguous_in_sentence_order() -> None:
    result = get_locations().resolve("강남이랑 홍대 중에")
    assert result.status == "ambiguous"
    assert [loc.id for loc in result.locations] == ["area:gangnam", "area:hongdae"]


def test_same_expression_gives_same_coordinates() -> None:
    """ADR 0009: 같은 위치는 같은 좌표여야 비교·재검색 판정이 맞는다."""
    index = get_locations()
    a, b = index.resolve("홍대 숙소"), index.resolve("홍대 근처")
    assert a.locations[0] == b.locations[0]


def test_area_all_is_not_a_location() -> None:
    assert get_locations().get("area:all") is None
    assert get_locations().resolve("서울 전역").status == "unknown"
