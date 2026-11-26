"""위치 해석 데이터: 지역 12곳(스냅샷 `areas`에서 서울 전역 제외)과 병원 역(`data/stations.json`).

설계 문서 9장, FR-16, ADR 0011.

- 지역 좌표는 web prices.json의 지역 중심 좌표다.
- 역은 병원 역 문장(`stationKo`/`stationJa`)에 나오는 역만 둔다. 좌표는 서울 열린데이터광장
  "서울시 역사마스터 정보"의 실제 좌표다. `scripts/build_stations.py`로 만든다.
- 위치 ID(`area:hongdae`, `station:홍대입구`)로 위치를 가리킨다. search_clinics는 좌표 대신 이 ID를
  받으므로 LLM이 좌표를 옮겨 적거나 지어낼 수 없고, 같은 ID면 항상 같은 좌표다(ADR 0009).
"""

import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from api.domain.catalog import (
    AreaId,
    Catalog,
    GeoPoint,
    Place,
    SnapshotModel,
    get_catalog,
)

STATIONS_PATH = Path(__file__).resolve().parent / "data" / "stations.json"

LocationType = Literal["area", "station"]

_KO_STATION = re.compile(r"([가-힣A-Za-z0-9]+)역")
_JA_STATION = re.compile(r"([^\s・·()（）]+?)駅")

# 이름 뒤에 붙어도 그 이름을 가리키는 말(조사, 위치 표현). 이 밖의 글자가 붙으면 다른 단어의 일부로 본다.
# 예: '서울대병원'의 '서울대'는 버리고, '홍대숙소'·'신사동'·'弘大周辺'의 이름은 쓴다.
_SUFFIXES = (
    *(
        "역",
        "근처",
        "부근",
        "인근",
        "주변",
        "쪽",
        "앞",
        "동네",
        "동",
        "숙소",
        "호텔",
        "집",
    ),
    *(
        "에서",
        "에",
        "이랑",
        "랑",
        "하고",
        "과",
        "와",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
    ),
    *("의", "으로", "로"),
    *(
        "駅",
        "周辺",
        "近く",
        "付近",
        "辺り",
        "エリア",
        "方面",
        "側",
        "洞",
        "ホテル",
        "宿",
    ),
)
# 이 말이 붙으면 구 이름이다('강남구', '마포구'). 지역 이름에만 맞춘다. '마포구'를 마포역으로 보지 않는다.
_DISTRICT_SUFFIXES = ("구", "区")


class StationSource(SnapshotModel):
    name: str
    url: str
    license: str
    downloaded_date: str


class Station(SnapshotModel):
    name_ko: str
    name_ja: str
    lat: float
    lng: float
    # 역사마스터의 노선 이름. 여러 노선 역은 노선별 좌표의 평균을 쓴다.
    lines: tuple[str, ...]


class StationData(SnapshotModel):
    source: StationSource
    stations: tuple[Station, ...]


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: LocationType
    name_ko: str
    name_ja: str
    lat: float
    lng: float
    # 지역이면 그 지역, 역이면 그 역을 쓴 병원이 가장 많은 지역
    area: AreaId

    @property
    def point(self) -> GeoPoint:
        return GeoPoint(lat=self.lat, lng=self.lng)


class Resolution(BaseModel):
    status: Literal["found", "ambiguous", "unknown"]
    # found면 하나, ambiguous면 문장에 나온 순서대로 여러 개, unknown이면 빈 목록
    locations: list[Location]


def station_names(place: Place) -> list[tuple[str, str]]:
    """병원 역 문장에서 (한국어 역 이름, 일본어 역 이름) 목록을 뽑는다. '역'·'駅'은 뺀다.

    한·일 문장의 역 개수가 다르면(예: '麻谷ナル駅直結 (空港鉄道 金浦1駅)'의 부연) 첫 역만 짝짓는다.
    """
    ko = _KO_STATION.findall(place.station_ko)
    ja = _JA_STATION.findall(place.station_ja)
    if len(ko) != len(ja):
        return [(ko[0], ja[0])] if ko and ja else []
    return list(zip(ko, ja, strict=True))


def normalize(text: str) -> str:
    """비교용: 전각·반각을 맞추고(NFKC) 소문자로 바꾼다. 공백은 단어 경계라 하나로 줄여 남긴다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower()).strip()


def _is_word(char: str) -> bool:
    """이름의 일부가 될 수 있는 글자. 히라가나는 조사(の, は)로 보고 경계로 친다."""
    if "\u3040" <= char <= "\u309f":
        return False
    return char.isalnum()


class _Match(NamedTuple):
    start: int
    end: int
    location_id: str


def area_parts(name: str) -> list[str]:
    """'신촌 (행사장 도보권)' → ['신촌'], '마곡・김포공항권' → ['마곡', '김포공항']."""
    name = re.sub(r"\s*[(（].*?[)）]", "", name)
    return [
        re.sub(r"[권圏]$", "", part.strip())
        for part in name.split("・")
        if part.strip()
    ]


def load_stations(path: Path = STATIONS_PATH) -> StationData:
    return StationData.model_validate_json(path.read_text(encoding="utf-8"))


class LocationIndex:
    """위치 표현 → 위치. 같은 표현에는 항상 같은 결과를 돌려준다.

    별칭: 지역은 ID와 한·일 이름의 각 부분('강남・신사・압구정' → 강남, 신사, 압구정), 역은 한·일 이름과
    '역'·'駅'을 붙인 이름. 지역 이름과 역 이름이 같으면, 지역의 첫 이름(강남, 신촌, 명동 등)은 지역으로,
    나머지(신사, 압구정, 공덕 등)는 역으로 본다. 지역 중심보다 역이 그 이름의 위치에 가깝기 때문이다.
    """

    def __init__(self, catalog: Catalog, stations: StationData) -> None:
        self.locations: dict[str, Location] = {}
        station_areas: dict[str, Counter[str]] = {}
        for place in catalog.places:
            for ko, _ in station_names(place):
                station_areas.setdefault(ko, Counter())[place.area] += 1
        primary: dict[str, str] = {}
        area_aliases: dict[str, str] = {}
        for area in catalog.areas:
            if area.id == "all":
                continue
            ko, ja = area_parts(area.name_ko), area_parts(area.name_ja)
            location = Location(
                id=f"area:{area.id}",
                type="area",
                name_ko=ko[0],
                name_ja=ja[0],
                lat=area.lat,
                lng=area.lng,
                area=area.id,
            )
            self.locations[location.id] = location
            for alias in [area.id, *ko, *ja]:
                area_aliases[normalize(alias)] = location.id
            primary[normalize(ko[0])] = primary[normalize(ja[0])] = location.id

        station_aliases: dict[str, str] = {}
        for station in stations.stations:
            location = Location(
                id=f"station:{station.name_ko}",
                type="station",
                name_ko=f"{station.name_ko}역",
                name_ja=f"{station.name_ja}駅",
                lat=station.lat,
                lng=station.lng,
                # 병원 수가 같으면 지역 ID 순으로 정해 실행마다 같게 한다.
                area=min(
                    station_areas[station.name_ko].items(), key=lambda x: (-x[1], x[0])
                )[0],
            )
            self.locations[location.id] = location
            for alias in [
                station.name_ko,
                f"{station.name_ko}역",
                station.name_ja,
                f"{station.name_ja}駅",
            ]:
                station_aliases[normalize(alias)] = location.id

        self.area_aliases = area_aliases
        self.aliases = {**area_aliases, **station_aliases}
        for alias, location_id in primary.items():
            self.aliases[alias] = location_id

    def get(self, location_id: str) -> Location | None:
        return self.locations.get(location_id)

    def _matches(self, text: str) -> list[_Match]:
        """단어 경계에 있는 별칭만 찾는다.

        - 앞: 문장 시작이거나 이름이 될 수 없는 글자(공백, 기호, 히라가나). '중구의'의 '구의'는 버린다.
        - 뒤: 문장 끝, 이름이 될 수 없는 글자, 또는 `_SUFFIXES`의 말. '역'·'駅'으로 끝나는 별칭은
          뒤를 보지 않는다('江南駅三番出口' → 강남역). 뒤에 '구'·'区'가 오면 지역 이름일 때만 쓴다.
        """
        matches: list[_Match] = []
        for alias, location_id in self.aliases.items():
            start = text.find(alias)
            while start != -1:
                end = start + len(alias)
                rest = text[end:]
                if start == 0 or not _is_word(text[start - 1]):
                    if rest.startswith(_DISTRICT_SUFFIXES):
                        if alias in self.area_aliases:
                            matches.append(_Match(start, end, self.area_aliases[alias]))
                    elif (
                        not rest
                        or not _is_word(rest[0])
                        or alias.endswith(("역", "駅"))
                        or rest.startswith(_SUFFIXES)
                    ):
                        matches.append(_Match(start, end, location_id))
                start = text.find(alias, start + 1)
        return matches

    def resolve(self, query: str) -> Resolution:
        """문장의 위치를 찾는다. 같은 표현에는 항상 같은 결과다.

        - 다른 별칭 안에 든 별칭은 버린다. '홍대입구역 근처' → 홍대입구역(안에 든 '홍대'는 버림).
        - 지역과 그 지역 안의 역이 함께 나오면 더 좁은 역을 쓴다. '강남구 역삼동' → 역삼역.
        - 서로 다른 위치가 둘 이상 남으면('홍대랑 강남') ambiguous, 하나도 없으면 unknown이다.
        """
        matches = self._matches(normalize(query))
        kept = [
            m
            for m in matches
            if not any(
                o.start <= m.start
                and m.end <= o.end
                and o.end - o.start > m.end - m.start
                for o in matches
            )
        ]
        kept.sort(key=lambda m: (m.start, -m.end))
        ids: list[str] = []
        for m in kept:
            if m.location_id not in ids:
                ids.append(m.location_id)
        locations = [self.locations[i] for i in ids]
        station_areas = {loc.area for loc in locations if loc.type == "station"}
        locations = [
            loc
            for loc in locations
            if not (loc.type == "area" and loc.area in station_areas)
        ]
        if not locations:
            return Resolution(status="unknown", locations=[])
        if len(locations) > 1:
            return Resolution(status="ambiguous", locations=locations)
        return Resolution(status="found", locations=locations)


@lru_cache
def get_locations() -> LocationIndex:
    return LocationIndex(get_catalog(), load_stations())
