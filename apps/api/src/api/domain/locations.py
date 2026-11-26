"""위치 해석 데이터: 지역 13곳(스냅샷 `areas`)과 주요 역(`data/stations.json`). 설계 문서 9장, FR-16.

- 지역 좌표는 web prices.json의 지역 중심 좌표다.
- 역은 병원 역 문장(`stationKo`/`stationJa`)에 나오는 역만 둔다. 좌표는 서울 열린데이터광장
  "서울시 역사마스터 정보"의 실제 좌표다. `scripts/build_stations.py`로 만든다.
- 위치 ID(`area:hongdae`, `station:홍대입구`)로 위치를 가리킨다. search_clinics는 좌표 대신 이 ID를
  받으므로 LLM이 좌표를 옮겨 적거나 지어낼 수 없고, 같은 ID면 항상 같은 좌표다(ADR 0009).
"""

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from api.domain.catalog import Catalog, GeoPoint, Place, SnapshotModel, get_catalog

STATIONS_PATH = Path(__file__).resolve().parent / "data" / "stations.json"

LocationType = Literal["area", "station"]

_KO_STATION = re.compile(r"([가-힣A-Za-z0-9]+)역")
_JA_STATION = re.compile(r"([^\s・·()（）]+?)駅")


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
    """비교용: 전각·반각을 맞추고(NFKC) 소문자로 바꾼 뒤 공백을 지운다."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).lower())


def _area_parts(name: str) -> list[str]:
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
        primary: dict[str, str] = {}
        area_aliases: dict[str, str] = {}
        for area in catalog.areas:
            if area.id == "all":
                continue
            ko, ja = _area_parts(area.name_ko), _area_parts(area.name_ja)
            location = Location(
                id=f"area:{area.id}",
                type="area",
                name_ko=ko[0],
                name_ja=ja[0],
                lat=area.lat,
                lng=area.lng,
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
            )
            self.locations[location.id] = location
            for alias in [
                station.name_ko,
                f"{station.name_ko}역",
                station.name_ja,
                f"{station.name_ja}駅",
            ]:
                station_aliases[normalize(alias)] = location.id

        self.aliases = {**area_aliases, **station_aliases}
        for alias, location_id in primary.items():
            self.aliases[alias] = location_id

    def get(self, location_id: str) -> Location | None:
        return self.locations.get(location_id)

    def resolve(self, query: str) -> Resolution:
        """문장에 들어 있는 별칭 중 가장 긴 것을 고른다.

        '홍대입구역 근처' → 홍대입구역(안에 든 '홍대'는 버림). 서로 다른 위치가 둘 이상 남으면
        ('홍대랑 강남') ambiguous, 하나도 없으면 unknown이다.
        """
        text = normalize(query)
        found = [alias for alias in self.aliases if alias in text]
        kept = [a for a in found if not any(a != b and a in b for b in found)]
        kept.sort(key=lambda a: (text.find(a), -len(a)))
        ids: list[str] = []
        for alias in kept:
            if self.aliases[alias] not in ids:
                ids.append(self.aliases[alias])
        locations = [self.locations[i] for i in ids]
        if not locations:
            return Resolution(status="unknown", locations=[])
        if len(locations) > 1:
            return Resolution(status="ambiguous", locations=locations)
        return Resolution(status="found", locations=locations)


@lru_cache
def get_locations() -> LocationIndex:
    return LocationIndex(get_catalog(), load_stations())
