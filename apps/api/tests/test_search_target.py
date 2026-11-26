"""같은 턴의 두 검색이 비교인지 재검색인지 가르는 규칙(ADR 0009)."""

import pytest

from api.agent.tools.search_clinics import SearchTarget
from api.domain.catalog import GeoPoint

HONGDAE = GeoPoint(lat=37.5563, lng=126.9228)
GANGNAM = GeoPoint(lat=37.4979, lng=127.0276)


@pytest.mark.parametrize(
    ("first", "second", "compare"),
    [
        # 비교: 서로 다르게 지정
        (SearchTarget(area="hongdae"), SearchTarget(area="gangnam"), True),
        (SearchTarget(origin=HONGDAE), SearchTarget(origin=GANGNAM), True),
        (
            SearchTarget(clinic_tokens=["밴스", "홍대"]),
            SearchTarget(clinic_tokens=["블리비", "명동"]),
            True,
        ),
        (
            SearchTarget(clinic_tokens=["밴스"]),
            SearchTarget(clinic_tokens=["블리비"]),
            True,
        ),
        # 재검색: 한쪽만 지정했거나 같거나 더 좁아짐
        (SearchTarget(), SearchTarget(area="gangnam"), False),
        (SearchTarget(area="gangnam"), SearchTarget(area="gangnam"), False),
        (SearchTarget(), SearchTarget(), False),
        (
            SearchTarget(clinic_tokens=["밴스"]),
            SearchTarget(clinic_tokens=["밴스", "홍대"]),
            False,
        ),
        (
            SearchTarget(clinic_tokens=["밴스", "홍대"]),
            SearchTarget(clinic_tokens=["밴스"]),
            False,
        ),
        (SearchTarget(clinic_tokens=["밴스"]), SearchTarget(area="hongdae"), False),
    ],
)
def test_compares_with(
    first: SearchTarget, second: SearchTarget, compare: bool
) -> None:
    assert first.compares_with(second) is compare
    assert second.compares_with(first) is compare
