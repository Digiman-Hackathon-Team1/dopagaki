"""안전 정지용 건강 정보 키워드(설계 문서 8장 safety_check, 12장, FR-06).

키워드는 Claude가 놓치지 않게 표시만 한다. 멈출지는 Claude가 문맥으로 정한다. 그래서 "임신 계획은
없어요"처럼 부정하는 말도 표시되지만, 멈추지는 않는다. 표시가 넓어도 비용은 안내 문장 하나다.

같은 키워드로 화면에 나가는 칸(조건 요약, 주의사항, 다음 질문 제안, 카드 이유)에서 건강 정보를
거른다(설계 문서 4.8). 그래서 "병원"의 "병", 수유역의 "수유"처럼 다른 말에 들어가는 글자는 쓰지 않는다.
"""

import re
from typing import Literal

SafetyTopic = Literal["pregnancy", "breastfeeding", "disease", "medication"]

_KEYWORDS: dict[SafetyTopic, str] = {
    "pregnancy": r"임신|임산부|출산|妊娠|妊婦|妊活|懐妊|出産|pregnan",
    # 수유역(서울 지하철역)과 수유동은 빼고 찾는다.
    "breastfeeding": r"수유(?![역동])|모유|授乳|母乳|breast-?feed",
    "disease": (
        r"질환|지병|질병|당뇨|고혈압|저혈압|심장병|심장 질환|암 (?:치료|환자|수술)|항암|켈로이드|"
        r"아토피|건선|루푸스|자가면역|헤르페스|피부염|뇌전증|간질|갑상선|혈우병|"
        r"持病|病気|疾患|糖尿病|高血圧|低血圧|心臓病|心疾患|がん治療|癌|抗がん剤|ケロイド|"
        r"アトピー|乾癬|膠原病|自己免疫|ヘルペス|皮膚炎|てんかん|甲状腺|血友病|"
        r"disease|diabet|keloid"
    ),
    "medication": (
        r"복용|약을 먹|약 먹|먹는 약|먹고 있는 약|처방약|처방받은 약|항응고|아스피린|와파린|"
        r"이소트레티노인|로아큐탄|스테로이드|혈액 ?희석|"
        r"服用|服薬|内服|薬を飲|薬飲|飲み薬|処方薬|処方され|抗凝固|アスピリン|ワーファリン|ワルファリン|"
        r"イソトレチノイン|アキュテイン|ロアキュテイン|ステロイド|血液サラサラ|"
        r"medication|isotretinoin|accutane"
    ),
}

_PATTERNS: dict[SafetyTopic, re.Pattern[str]] = {
    topic: re.compile(pattern, re.IGNORECASE) for topic, pattern in _KEYWORDS.items()
}


def find_topics(text: str) -> list[SafetyTopic]:
    """문장에 나온 건강 정보 종류. 순서는 _KEYWORDS 순서다."""
    return [topic for topic, pattern in _PATTERNS.items() if pattern.search(text)]


def mentions_health(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PATTERNS.values())


# 조건 요약은 "3万円、弘大、妊娠予定なし"처럼 조건을 나열하므로 조건 하나 단위로 거른다.
_CONDITION_SEPARATOR = re.compile(r"(\s*[,、，·・/|]\s*)")


def strip_health_conditions(summary: str) -> str:
    """조건 요약에서 건강 정보가 든 조건만 뺀다. 나머지 조건은 원래 구분자로 다시 잇는다."""
    if not mentions_health(summary):
        return summary
    parts = _CONDITION_SEPARATOR.split(summary)
    kept: list[str] = []
    for i in range(0, len(parts), 2):
        item = parts[i]
        if not item.strip() or mentions_health(item):
            continue
        if kept:
            kept.append(parts[i - 1])
        kept.append(item)
    return "".join(kept).strip()
