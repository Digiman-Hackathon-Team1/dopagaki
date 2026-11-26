"""주요 역 좌표 파일(src/api/domain/data/stations.json)을 만든다(설계 문서 9장, FR-16).

실행: `cd apps/api && uv run python scripts/build_stations.py <서울시 역사마스터 정보.json> <받은 날짜>`

- 원자료: 서울 열린데이터광장 "서울시 역사마스터 정보"(OA-21232)의 JSON 내려받기. 이용허락범위 제한 없음.
- 역 목록: web 스냅샷 병원 역 문장(stationKo/stationJa)에 나오는 역만 둔다.
- 이름 대조: 역사마스터 이름의 괄호 부기(예: '잠실(송파구청)')를 떼고 같은 이름을 찾는다.
- 좌표: 여러 노선 역은 노선별 좌표의 평균이다. 다만 이름이 같은 다른 역(2호선 신촌과 경의중앙선 신촌은
  715m 떨어짐)을 섞지 않도록, 그 역을 쓴 병원들의 평균 위치에서 가장 가까운 노선 좌표를 기준으로
  500m 안의 노선 좌표만 평균한다.

web 데이터에 새 역이 생기면 tests/test_locations.py가 이 스크립트를 다시 돌리라고 실패한다.
"""

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

from api.domain.catalog import get_catalog
from api.domain.locations import STATIONS_PATH, station_names

SOURCE = {
    "name": "서울시 역사마스터 정보 (서울 열린데이터광장 OA-21232)",
    "url": "https://data.seoul.go.kr/dataList/OA-21232/S/1/datasetView.do",
    "license": "이용허락범위 제한 없음",
}
SAME_STATION_METERS = 500


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    x = (lng2 - lng1) * math.cos((lat1 + lat2) / 2)
    return 6371000 * math.hypot(x, lat2 - lat1)


def main(master_path: str, downloaded_date: str) -> None:
    master = json.loads(Path(master_path).read_text(encoding="utf-8"))["DATA"]
    rows: dict[str, list[dict]] = defaultdict(list)
    for row in master:
        rows[re.sub(r"\(.*?\)$", "", row["bldn_nm"]).strip()].append(row)

    catalog = get_catalog()
    ja_names: dict[str, str] = {}
    clinics: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for place in catalog.places:
        for ko, ja in station_names(place):
            ja_names.setdefault(ko, ja)
            clinics[ko].append((place.lat, place.lng))

    stations = []
    missing = []
    for ko in sorted(ja_names):
        found = rows.get(ko)
        if not found:
            missing.append(ko)
            continue
        points = [(float(r["lat"]), float(r["lot"]), r["route"]) for r in found]
        here = clinics[ko]
        centroid = (
            sum(p[0] for p in here) / len(here),
            sum(p[1] for p in here) / len(here),
        )
        anchor = min(points, key=lambda p: meters(p[:2], centroid))
        same = [p for p in points if meters(p[:2], anchor[:2]) <= SAME_STATION_METERS]
        stations.append(
            {
                "nameKo": ko,
                "nameJa": ja_names[ko],
                "lat": round(sum(p[0] for p in same) / len(same), 6),
                "lng": round(sum(p[1] for p in same) / len(same), 6),
                "lines": sorted({p[2] for p in same}),
            }
        )
    if missing:
        raise SystemExit(f"역사마스터에 없는 역: {', '.join(missing)}")

    data = {
        "source": {**SOURCE, "downloadedDate": downloaded_date},
        "stations": stations,
    }
    STATIONS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {STATIONS_PATH.name}: {len(stations)} stations")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
