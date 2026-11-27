"""에이전트 도구(설계 문서 9장). 그래프에는 `TOOLS`를 넘긴다."""

from api.agent.tools.get_booking_options import get_booking_options
from api.agent.tools.match_jp_treatment import match_jp_treatment
from api.agent.tools.recommend_treatments import recommend_treatments
from api.agent.tools.resolve_location import resolve_location
from api.agent.tools.search_clinics import search_clinics

TOOLS = [
    match_jp_treatment,
    recommend_treatments,
    resolve_location,
    search_clinics,
    get_booking_options,
]
