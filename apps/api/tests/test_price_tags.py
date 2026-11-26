"""가격 조건 태그 분류(설계 문서 4.7)."""

import pytest

from api.domain.price_tags import classify


@pytest.mark.parametrize(
    ("text", "tags"),
    [
        (
            "체험 이벤트가・마취비 별도 확인 필요",
            ["first_visit", "event", "anesthesia"],
        ),
        ("최저가급 이벤트・첫 방문 조건 있음", ["first_visit", "event"]),
        ("공식 가격표 · 인모드 FX 1부위 (1회 체험가)", ["first_visit"]),
        ("요일별 특가 있음・마취비 확인", ["event", "day_limited", "anesthesia"]),
        ("화수목 타임특가 있음・마취비 확인", ["event", "day_limited", "anesthesia"]),
        ("앱・카카오 예약 혜택 기준", ["app_benefit"]),
        ("쁨글로벌의원 홍대점 공식 가격 (포텐자 190,000원 · 팁 별도)", ["extra_fee"]),
        (
            "홈페이지 포텐자 1회 체험가 5.9만 원(팁값 별도)",
            ["first_visit", "extra_fee"],
        ),
        # 조건이 아닌 문장
        (
            "타임스퀘어 인접・마취비 확인",
            ["anesthesia"],
        ),  # 타임스퀘어는 시간대 한정이 아니다
        ("제이필의원 홍대점 공식 시술예약 (포텐자 모공팁 풀페이스 250,000원)", []),
        ("공식 가격표 · 아쿠아필 1회", []),  # 1회는 시술 횟수
        ("부가세 별도", []),  # 부가세는 calc.py가 이미 더한다
        ("전 지점 같은 가격표 운영", []),
    ],
)
def test_classify(text: str, tags: list[str]) -> None:
    assert classify(text) == tags
