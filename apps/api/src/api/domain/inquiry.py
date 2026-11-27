"""병원에 보낼 예약 문의 문장(설계 문서 9장 get_booking_options, FR-05).

web `apps/web/src/lib/clinic-helpers.ts`의 `buildInquiryMessages`를 옮긴 것이다. 가격이 들어가지
않는 문장 틀이라 환율이 바뀌어도 결과가 같다. web 함수로 만든 기대값(`tests/fixtures/
inquiry-expected.json`)과 같은지 `tests/test_inquiry.py`가 대조한다. web 문장이 바뀌면 이 파일을
고치고 `pnpm --filter @repo/api export:web-data`로 기대값을 다시 만든다.
"""

from collections.abc import Sequence
from typing import Literal

from api.domain.calc import js_round
from api.domain.catalog import Menu, Place

TimeSlot = Literal["any", "morning", "afternoon", "evening"]

TIME_LABELS: dict[TimeSlot, tuple[str, str]] = {
    "any": ("시간 무관", "時間帯おまかせ"),
    "morning": ("오전 (10:00~12:00)", "午前（10:00〜12:00）"),
    "afternoon": ("오후 (13:00~17:00)", "午後（13:00〜17:00）"),
    "evening": ("저녁 (17:00 이후)", "夕方（17:00以降）"),
}


def inquiry_messages(
    place: Place,
    menus: Sequence[Menu],
    menu_ids: Sequence[str],
    *,
    date: str = "",
    time_slot: TimeSlot = "any",
    people: int = 1,
    first_visit: bool = True,
    guest_name: str = "",
) -> tuple[str, str]:
    """한국어·일본어 문의 문장을 돌려준다.

    - 시술은 `menus`(전체 시술 목록)의 순서로 적고, 이 병원에 가격이 없는 시술은 뺀다(web
      `computeClinicBundle`과 같음). 남는 시술이 없으면 "상담 후 결정"이다.
    - 이름·날짜를 비우면 web과 같은 자리 표시 문구가 들어간다. 인원은 1~5명으로 맞춘다.
    """
    date_ko = date or "(희망 날짜 미입력)"
    date_ja = date or "（希望日未入力）"
    time_ko, time_ja = TIME_LABELS[time_slot]
    name = guest_name.strip()
    name_ko = name or "(여권 영문명 입력)"
    name_ja = name or "（パスポート英字名を入力）"
    count = max(1, min(5, js_round(people)))
    selected = [m for m in menus if m.id in menu_ids and m.id in place.prices]
    menus_ko = ", ".join(m.name_ko for m in selected) or "상담 후 결정"
    menus_ja = "・".join(m.name_ja for m in selected) or "相談して決定"

    if place.japanese_support:
        lang_ko = "일본어 상담이 가능한 시간대로 배정 부탁드립니다."
        lang_ja = "日本語対応が可能な時間帯でご案内いただけますと幸いです。"
    else:
        lang_ko = "일본어 대응이 어려우시면 번역기나 간단한 영어로 상담 부탁드립니다."
        lang_ja = "日本語対応が難しい場合は、翻訳アプリや簡単な英語でのご対応をお願いいたします。"

    ko = "\n".join(
        [
            f"안녕하세요, {place.name_ko} 예약 문의드립니다.",
            f"일본에서 방문하는 {'첫 방문(초진)' if first_visit else '재방문'} 고객입니다.",
            "",
            f"• 성함(여권 영문): {name_ko}",
            f"• 희망 일시: {date_ko} / {time_ko}",
            f"• 인원: {count}명",
            f"• 희망 시술: {menus_ko}",
            "",
            lang_ko,
            "마취크림·전용 팁·부가세 포함 예상 금액도 함께 알려주시면 감사하겠습니다.",
        ]
    )
    ja = "\n".join(
        [
            f"こんにちは、{place.name_ja}への予約についてお問い合わせです。",
            f"日本から伺う{'初診' if first_visit else '再診'}の患者です。",
            "",
            f"・お名前（パスポート表記）：{name_ja}",
            f"・ご希望日時：{date_ja} / {time_ja}",
            f"・人数：{count}名",
            f"・希望施術：{menus_ja}",
            "",
            lang_ja,
            "麻酔クリーム・専用チップ・税金込みのおおよその総額もあわせて教えていただけますと幸いです。",
        ]
    )
    return ko, ja
