"""resolve_location: 위치 표현을 위치 ID와 좌표로 바꾼다(설계 문서 4.3, 9장, FR-16).

데이터의 지역 13곳과 주요 역만 찾는다. 못 찾으면 "모름"으로 돌려주고, LLM은 사용자에게 한 번 묻는다.
찾은 위치는 기준 위치 카드로 화면에 나간다(finalize가 artifact로 조립).
"""

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from api.agent.schemas import LocationCardData
from api.domain.catalog import get_catalog
from api.domain.locations import Location, get_locations

TOOL_NAME = "resolve_location"


class ResolveLocationInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=50,
        description=(
            "사용자가 말한 위치 표현 그대로 또는 그 안의 장소 이름. 예: '홍대 숙소', '명동역 근처', "
            "'江南駅'. 장소가 여러 곳이면 한 곳씩 따로 부른다"
        ),
    )


class ResolveLocationArtifact(BaseModel):
    """화면용 결과. found일 때만 기준 위치 카드가 된다."""

    status: Literal["found", "ambiguous", "unknown"]
    location: LocationCardData | None = None


def _label(location: Location) -> str:
    kind = "area center" if location.type == "area" else "station"
    return (
        f"location_id={location.id} ({location.name_ko} / {location.name_ja}, {kind})"
    )


def _area_names() -> str:
    return ", ".join(
        f"{a.name_ko.split(' (')[0]} / {a.name_ja.split(' (')[0]}"
        for a in get_catalog().areas
        if a.id != "all"
    )


DESCRIPTION = f"""\
사용자가 말한 위치(숙소, 사는 동네, 역)를 위치 ID로 바꾼다. 데이터의 서울 지역 13곳과 병원 근처 주요 역만 안다.
필수 입력: query. 사용자가 숙소·기준 위치를 말하면 search_clinics보다 먼저 부르고, 결과의 location_id를
search_clinics의 origin_id에 그대로 넣는다. 좌표를 직접 넘기거나 지어내지 않는다.
찾은 위치는 화면에 기준 위치 카드로 나간다. 답변 맨 위 조건 요약에 그 위치를 적어 사용자가 고칠 수 있게 한다.
못 찾으면(unknown) 위치를 지어내지 말고 가까운 역이나 아래 지역 이름을 한 번 묻는다. 그동안은 origin_id 없이 검색한다.
가타카나 읽기(ホンデ, カンナム 등)는 한국어나 한자 이름(홍대, 弘大)으로 바꿔 넣는다.
쓰면 안 되는 경우: 사용자가 위치를 말하지 않았을 때, 서울 밖 위치.
지역: {_area_names()}"""


@tool(
    TOOL_NAME,
    description=DESCRIPTION,
    args_schema=ResolveLocationInput,
    response_format="content_and_artifact",
)
def resolve_location(query: str) -> tuple[str, dict]:
    result = get_locations().resolve(query)
    head = f"resolve_location: {query!r}"
    if result.status == "unknown":
        message = (
            f"{head} → unknown. No area or station in the data matches. Do not guess a location. "
            "Ask the user once for the nearest subway station or one of the areas in the tool "
            "description, and search without origin_id until then."
        )
        return message, ResolveLocationArtifact(status="unknown").model_dump()
    if result.status == "ambiguous":
        listed = "; ".join(_label(loc) for loc in result.locations)
        message = (
            f"{head} → ambiguous, it names several places: {listed}. If the user is comparing them, "
            "call resolve_location once per place. Otherwise ask which one is their base."
        )
        return message, ResolveLocationArtifact(status="ambiguous").model_dump()

    location = result.locations[0]
    card = LocationCardData(
        location_id=location.id,
        location_type=location.type,
        query=query,
        name_ja=location.name_ja,
        name_ko=location.name_ko,
        lat=location.lat,
        lng=location.lng,
    )
    message = (
        f"{head} → found {_label(location)}. Pass origin_id={location.id!r} to search_clinics. "
        "Distances will be straight-line distances from this point."
    )
    return message, ResolveLocationArtifact(status="found", location=card).model_dump()
