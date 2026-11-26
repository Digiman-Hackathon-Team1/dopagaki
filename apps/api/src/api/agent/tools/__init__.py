"""에이전트 도구(설계 문서 9장). 그래프에는 `TOOLS`를 넘긴다."""

from api.agent.tools.recommend_treatments import recommend_treatments
from api.agent.tools.search_clinics import search_clinics

TOOLS = [recommend_treatments, search_clinics]
