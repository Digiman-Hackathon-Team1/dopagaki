"""응답 언어 판별(설계 문서 2장, 이슈 #76, ADR 0020)."""

import pytest

from api.agent.lang import detect_lang, reply_lang, turn_lang


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # 이슈 #76 완료 기준의 예시
        ("リジュランはいくらですか", "ja"),
        ("리쥬란 얼마예요?", "ko"),
        ("リジュラン 강남 価格", "ja"),
        ("일본에서 ハイフ 받았는데 서울에서 얼마예요?", "ko"),
        ("明洞 皮膚科", "ja"),
        ("3万円?", "ja"),
        ("Rejuran?", None),
        ("👍", None),
        # 한국어 문장에 가타카나 시술 이름만 들어가면 한국어(가나 5자 > 한글 2자여도)
        ("リジュラン 얼마?", "ko"),
        ("ﾘｼﾞｭﾗﾝ 얼마?", "ko"),
        # 한글이 없으면 가타카나뿐이어도 일본어
        ("リジュラン", "ja"),
        ("ㅋㅋ", "ko"),
        ("리쥬란 リジュラン", "ko"),
        # 같으면 정하지 못한다
        ("강남 新村", None),
        # 문장부호(가운뎃점·장음)는 세지 않는다
        ("・", None),
        ("ー", None),
        ("Rejuran・HIFU", None),
        ("ㆍ", None),
        ("ピコトーニング 얼마?", "ko"),
    ],
)
def test_detect_lang_examples(message: str, expected: str | None) -> None:
    assert detect_lang(message) == expected


@pytest.mark.parametrize(
    ("message", "detected", "meaning"),
    [
        ("한국어로 말해줘. リジュランの価格", "ja", "ko"),
        ("韓国語で答えて", "ja", "ko"),
        ("밴스의원 홍대점 どう?", "ko", "ja"),
    ],
)
def test_detect_lang_known_limits(message: str, detected: str, meaning: str) -> None:
    """글자 수로는 뜻(명시 요청, 고유명사)을 모른다. 이런 메시지는 Claude가 최종 언어를 고른다."""
    assert detect_lang(message) == detected != meaning


def test_turn_lang_uses_message_then_previous_then_screen() -> None:
    assert turn_lang("리쥬란 얼마예요?", previous="ja", screen="ja") == "ko"
    assert turn_lang("Rejuran?", previous="ko", screen="ja") == "ko"
    assert turn_lang("Rejuran?", previous=None, screen="ja") == "ja"


def test_reply_lang_counts_every_field_together() -> None:
    # 한국어 답에 일본 시술명이 섞여도 한국어다.
    assert (
        reply_lang(["일본의 ハイフ는 한국에서 슈링크라고 불러요.", "가까운 곳 보여 줘"])
        == "ko"
    )
    assert reply_lang(["シュリンクの上位3院です", "近い順に見せて"]) == "ja"
    assert reply_lang(["", "1", "2"]) is None
