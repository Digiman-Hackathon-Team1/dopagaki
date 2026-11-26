"""가격 조건 태그(설계 문서 4.7).

가격 조건은 데이터에 문장으로만 있다. 병원 문장(`caveatKo`)과 가격 메모(`noteKo`)를 규칙으로
한 번 분류해 카드에 태그로 붙인다. 조건별 금액은 데이터에 없으므로 절약액 계산에는 쓰지 않는다.
"""

import re
from typing import Literal

from api.domain.catalog import Offer, Place

PriceTag = Literal[
    "first_visit",  # 첫 방문·1회 체험가
    "event",  # 이벤트가·특가·프로모션
    "day_limited",  # 요일·시간대 한정가
    "app_benefit",  # 앱·카카오 예약 혜택가
    "anesthesia",  # 마취비 별도 또는 확인 필요
    "extra_fee",  # 시술 팁 비용 별도
]

# 태그 순서는 이 표의 순서다. 부가세 별도는 calc.py가 이미 더하므로 태그로 보지 않는다.
_RULES: tuple[tuple[PriceTag, re.Pattern[str]], ...] = (
    ("first_visit", re.compile(r"첫\s?방문|체험")),
    ("event", re.compile(r"이벤트|특가|프로모션|메가세일")),
    ("day_limited", re.compile(r"요일|평일|주말|화수목|타임\s?특가")),
    ("app_benefit", re.compile(r"앱|카카오|카톡")),
    ("anesthesia", re.compile(r"마취")),
    # "팁 30,000원 별도"처럼 팁과 별도 사이에 금액이 낄 수 있다. "팁 포함"은 추가 비용이 아니다.
    # 괄호나 가운뎃점에서 구절이 끝나므로 "모공팁 풀페이스 250,000원)"은 걸리지 않는다.
    ("extra_fee", re.compile(r"팁(?!\s?포함)값?[^·()]*?(?:별도|미포함)")),
)

# 가격의 종류를 말하는 태그. 메모에 다른 가격이 함께 적혀 있으면 그 가격의 조건일 수 있다.
_PRICE_KIND: frozenset[str] = frozenset(
    {"first_visit", "event", "day_limited", "app_benefit"}
)

_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:(만)\s*원?|원)")
_CLAUSE_SPLIT = re.compile(r"[·()]")

# 병원 문장이 특정 시술을 말하면(예: "포텐자·보톡스 특가 확인") 그 시술 카드에만 붙인다.
_MENU_WORDS: dict[str, re.Pattern[str]] = {
    "rejuran-2cc": re.compile(r"리쥬란"),
    "shrink-300": re.compile(r"슈링크"),
    "potenza": re.compile(r"포텐자"),
    "jaw-botox": re.compile(r"보톡스|톡신"),
    "juvelook-2cc": re.compile(r"쥬베룩"),
    "re2o-5cc": re.compile(r"리투오"),
    "inmode-fx": re.compile(r"인모드"),
    "lalapeel": re.compile(r"라라필"),
    "aquapeel": re.compile(r"아쿠아필"),
}


def classify(text: str) -> list[PriceTag]:
    return [tag for tag, pattern in _RULES if pattern.search(text)]


def _amounts(text: str) -> set[int]:
    return {
        round(float(number.replace(",", "")) * (10000 if man else 1))
        for number, man in _AMOUNT.findall(text)
    }


def _note_tags(note: str, krw: int) -> set[str]:
    """가격 메모의 태그.

    메모에 금액이 여럿이고 그중 하나가 카드 가격이면, 가격 종류 태그는 다른 금액이 적힌 구절에서
    읽지 않는다. 예: "24,000원 · 카톡플친 1회 체험 19,000원"의 카드 가격이 24,000원이면
    체험가·앱 혜택 태그를 붙이지 않는다. 팁·마취비 같은 추가 비용 태그는 메모 전체에서 읽는다.
    """
    tags = set(classify(note))
    amounts = _amounts(note)
    if len(amounts) < 2 or krw not in amounts:
        return tags
    kept: set[str] = set()
    for clause in _CLAUSE_SPLIT.split(note):
        clause_amounts = _amounts(clause)
        if clause_amounts and krw not in clause_amounts:
            continue
        kept |= set(classify(clause))
    return (tags - _PRICE_KIND) | (kept & _PRICE_KIND)


def _caveat_tags(caveat: str, menu_id: str) -> set[str]:
    named = [mid for mid, pattern in _MENU_WORDS.items() if pattern.search(caveat)]
    if named and menu_id not in named:
        return set()
    return set(classify(caveat))


def price_tags(place: Place, menu_id: str, offer: Offer) -> list[PriceTag]:
    """병원 문장과 그 가격의 메모를 함께 분류한다."""
    found = _caveat_tags(place.caveat_ko, menu_id) | _note_tags(
        offer.note_ko, offer.krw
    )
    return [tag for tag, _ in _RULES if tag in found]
