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
# "모공팁"처럼 기기 팁 이름이나 "1회 체험 9,900원 별도"처럼 다른 가격 안내는 추가 비용이 아니다.
_RULES: tuple[tuple[PriceTag, re.Pattern[str]], ...] = (
    ("first_visit", re.compile(r"첫\s?방문|체험")),
    ("event", re.compile(r"이벤트|특가|프로모션|메가세일")),
    ("day_limited", re.compile(r"요일|평일|주말|화수목|타임\s?특가")),
    ("app_benefit", re.compile(r"앱|카카오|카톡")),
    ("anesthesia", re.compile(r"마취")),
    ("extra_fee", re.compile(r"팁\s?값?\s?(?:별도|미포함)")),
)


def classify(text: str) -> list[PriceTag]:
    return [tag for tag, pattern in _RULES if pattern.search(text)]


def price_tags(place: Place, offer: Offer) -> list[PriceTag]:
    """병원 문장과 그 가격의 메모를 함께 분류한다."""
    found = set(classify(place.caveat_ko)) | set(classify(offer.note_ko))
    return [tag for tag, _ in _RULES if tag in found]
