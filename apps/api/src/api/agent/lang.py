"""응답 언어 판별(설계 문서 2장 응답 언어, 이슈 #76, ADR 0020).

언어는 두 단계로 정한다.

1. 1차 판별(`turn_lang`): 코드가 사용자 메시지의 글자를 센다. 이 값은 Claude를 부르지 않는 안내(처리 중·턴 상한·
   하루 한도·오류), 데모 녹화본 찾기, Claude에게 주는 "모호할 때 쓸 언어"에 쓴다.
2. 최종 언어: Claude가 메시지의 뜻(명시 요청, 고유명사를 뺀 문장)을 보고 답할 언어를 고른다. 코드는 Claude가
   실제로 쓴 답의 글자를 다시 세어(`reply_lang`) 면책 문구·금액 주의 문구를 같은 언어로 붙이고, 다음 턴의
   "직전 턴 언어"로 저장한다.
"""

from collections.abc import Iterable

from api.agent.schemas import Lang

Ranges = tuple[tuple[int, int], ...]

# 완성형 음절, 자모(ㅋㅋ 같은 입력 포함), 호환용 자모
HANGUL: Ranges = ((0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F))
HIRAGANA: Ranges = ((0x3040, 0x309F),)
# 가타카나(장음 ー 포함), 가타카나 확장, 반각 가타카나
KATAKANA: Ranges = ((0x30A0, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9F))
# CJK 통합 한자, 확장 A, 호환 한자
KANJI: Ranges = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))


def _within(ch: str, ranges: Ranges) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in ranges)


def detect_lang(text: str) -> Lang | None:
    """글자 수로 정한 언어. 정하지 못하면 None.

    한글 수와 일본어 글자 수(히라가나 + 가타카나 + 한자)를 세어 많은 쪽으로 정한다. 같거나 둘 다 0이면 None.
    다만 한글이 있고 일본어 글자가 가타카나뿐이면 한국어로 본다. "リジュラン 얼마?"처럼 한국어 문장에
    가타카나 시술 이름만 들어간 경우다. 일본어 문장이면 조사·어미(히라가나)나 한자가 함께 있다.
    """
    hangul = hiragana = katakana = kanji = 0
    for ch in text:
        if _within(ch, HANGUL):
            hangul += 1
        elif _within(ch, HIRAGANA):
            hiragana += 1
        elif _within(ch, KATAKANA):
            katakana += 1
        elif _within(ch, KANJI):
            kanji += 1
    japanese = hiragana + katakana + kanji
    if hangul and katakana == japanese:
        return "ko"
    if hangul > japanese:
        return "ko"
    if japanese > hangul:
        return "ja"
    return None


def turn_lang(message: str, previous: Lang | None, screen: Lang) -> Lang:
    """1차 판별: 메시지 글자 → 직전 턴 응답 언어 → 화면 언어 순서로 정한다."""
    return detect_lang(message) or previous or screen


def reply_lang(texts: Iterable[str]) -> Lang | None:
    """Claude가 실제로 쓴 답(답변, 조건 요약, 질문, 다음 질문 제안)의 언어. 정하지 못하면 None."""
    return detect_lang("\n".join(texts))
