"""가격 조건 태그 분류(설계 문서 4.7)."""

import pytest

from api.domain.catalog import get_catalog
from api.domain.price_tags import _note_tags, classify, price_tags


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


@pytest.mark.parametrize(
    ("note", "krw", "expected"),
    [
        # 메모에 다른 가격(체험가·첫방문가)이 함께 적혀 있으면, 정가 카드에는 체험가 태그를 붙이지 않는다.
        ("턱보톡스 50U 국산 24,000원 · 카톡플친 1회 체험 19,000원", 24000, set()),
        ("홈페이지 국산 사각턱보톡스 1.9만 원(1회 체험 9,900원 별도)", 19000, set()),
        ("사각턱 보톡스 국산 20,000원 · 첫방문 19,000원 · 부가세 별도", 20000, set()),
        # 카드 가격이 그 체험가이면 붙인다.
        (
            "턱보톡스 50U 국산 24,000원 · 카톡플친 1회 체험 19,000원",
            19000,
            {"first_visit", "app_benefit"},
        ),
        # 팁 비용은 어느 구절에 있어도 붙인다.
        ("포텐자 1회 220,000원 · 개인팁 50,000원/부가세 별도", 220000, {"extra_fee"}),
        # 금액이 하나뿐이면 메모 전체를 읽는다.
        ("공식 가격표 · 인모드 FX 1부위 (1회 체험가)", 9900, {"first_visit"}),
    ],
)
def test_note_tags_follow_the_card_price(note: str, krw: int, expected: set) -> None:
    assert _note_tags(note, krw) == expected


def test_clinic_sentence_naming_treatments_applies_only_to_them() -> None:
    place = next(
        p for p in get_catalog().places if p.caveat_ko == "포텐자·보톡스 특가 확인"
    )
    tagged = {m for m, o in place.prices.items() if "event" in price_tags(place, m, o)}

    assert tagged == {"potenza", "jaw-botox"}
