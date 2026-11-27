"""같은 턴의 두 검색이 비교인지 재검색인지 가르는 규칙(ADR 0009)."""

import pytest

from api.agent.tools.search_clinics import SearchTarget
from api.domain.calc import matches_place
from api.domain.catalog import get_catalog

HONGDAE = "area:hongdae"
GANGNAM = "station:강남"


def named(name: str, **kwargs) -> SearchTarget:
    """search_clinics와 같은 방식으로 지점 이름 검색의 대상을 만든다."""
    tokens = name.lower().split()
    ids = sorted(p.id for p in get_catalog().places if matches_place(p, tokens))
    return SearchTarget(clinic_tokens=tokens, clinic_ids=ids, **kwargs)


@pytest.mark.parametrize(
    ("first", "second", "compare"),
    [
        # 비교: 서로 다른 대상
        (SearchTarget(area="hongdae"), SearchTarget(area="gangnam"), True),
        (SearchTarget(origin_id=HONGDAE), SearchTarget(origin_id=GANGNAM), True),
        (named("밴스 홍대"), named("블리비 명동"), True),
        (named("밴스"), named("블리비"), True),
        (
            named("밴스"),
            named("홍대입구"),
            True,
        ),  # 겹치지만 어느 쪽도 다른 쪽 안에 있지 않음
        (SearchTarget(area="gangnam"), named("밴스"), True),  # 종류가 다른 대상
        (
            named("밴스"),
            named("없는병원"),
            True,
        ),  # 아무것도 못 찾은 검색은 앞 결과를 지우지 않음
        # 재검색: 한쪽이 다른 쪽 조건을 모두 지킴
        (SearchTarget(), SearchTarget(area="gangnam"), False),
        (SearchTarget(area="gangnam"), SearchTarget(area="gangnam"), False),
        (SearchTarget(), SearchTarget(), False),
        (named("밴스"), named("밴스 홍대"), False),
        (named("밴스 홍대"), named("밴스"), False),
        (
            named("밴스"),
            named("밴스의원"),
            False,
        ),  # 단어가 달라도 찾은 지점이 같으면 재검색
        (named("밴스 홍대"), named("밴스의원 홍대점"), False),
        (named("밴스"), named("밴스의원", area="myeongdong"), False),
        (SearchTarget(area="hongdae"), named("밴스", area="hongdae"), False),
    ],
)
def test_compares_with(
    first: SearchTarget, second: SearchTarget, compare: bool
) -> None:
    assert first.compares_with(second) is compare
    assert second.compares_with(first) is compare
