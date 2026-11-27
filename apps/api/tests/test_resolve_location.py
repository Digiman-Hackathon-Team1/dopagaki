"""resolve_location 도구: 찾음·여러 곳·모름과 화면용 위치 데이터."""

from langchain_core.messages import ToolMessage

from api.agent.tools import resolve_location


def call(query: str) -> ToolMessage:
    return resolve_location.invoke(
        {
            "type": "tool_call",
            "name": "resolve_location",
            "id": "c1",
            "args": {"query": query},
        }
    )


def test_found_area_returns_location_id_for_search_clinics() -> None:
    message = call("홍대 숙소")

    assert "location_id=area:hongdae" in message.content
    assert "origin_id='area:hongdae'" in message.content
    assert message.artifact == {
        "status": "found",
        "location": {
            "location_id": "area:hongdae",
            "location_type": "area",
            "query": "홍대 숙소",
            "name_ja": "弘大",
            "name_ko": "홍대",
            "lat": 37.5563,
            "lng": 126.9228,
        },
    }


def test_found_station_uses_station_name() -> None:
    message = call("명동역 근처 호텔")

    location = message.artifact["location"]
    assert location["location_id"] == "station:명동"
    assert (location["name_ko"], location["name_ja"]) == ("명동역", "明洞駅")
    assert location["location_type"] == "station"


def test_unknown_place_asks_once_without_guessing() -> None:
    message = call("이태원")

    assert "unknown" in message.content
    assert "Do not guess" in message.content
    assert "Ask the user once" in message.content
    assert message.artifact == {"status": "unknown", "location": None}


def test_two_places_are_ambiguous_and_make_no_card() -> None:
    message = call("홍대랑 강남")

    assert "ambiguous" in message.content
    assert "area:hongdae" in message.content and "area:gangnam" in message.content
    assert message.artifact == {"status": "ambiguous", "location": None}
