"""병원에 보낼 예약 문의 문장과 접수 메모(설계 문서 9장 get_booking_options, FR-05).

web `apps/web/src/lib/clinic-helpers.ts`의 두 함수를 옮긴 것이다.

- `inquiry_messages` ← `buildInquiryMessages`: 방문 전에 LINE 등으로 보내는 예약 문의. 가격이 없다.
- `order_sheet_messages` ← `buildOrderSheetMessages`: 방문한 날 접수대에서 보여 주는 메모. web 예약
  시트가 보여 주는 문장이다. 금액은 calc.py로 계산해 병원 추천 카드와 같다.

web 함수로 만든 기대값(`tests/fixtures/inquiry-expected.json`, `order-sheet-expected.json`)과 같은지
`tests/test_inquiry.py`가 대조한다. web 문장이 바뀌면 이 파일을 고치고
`pnpm --filter @repo/api export:web-data`로 기대값을 다시 만든다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from api.domain.calc import js_round, krw_to_jpy
from api.domain.catalog import Menu, Place

TimeSlot = Literal["any", "morning", "afternoon", "evening"]
OrderRequestId = Literal["no-upsell", "total-first", "gentle-numbing", "one-hour"]

# web ORDER_GUARD_OPTIONS의 직원에게 보여 줄 문장(한국어, 일본어). 순서도 web과 같다.
ORDER_REQUESTS: dict[OrderRequestId, tuple[str, str]] = {
    "no-upsell": (
        "오늘은 미리 정한 시술만 받고 싶습니다. 추가 시술 권유는 정중히 사양하겠습니다.",
        "本日は事前に決めた施術のみ希望します。追加施術のおすすめは丁重に辞退いたします。",
    ),
    "total-first": (
        "마취비·진정관리·전용 팁·부가세(10%) 포함 최종 총액을 시술 전에 먼저 알려주세요.",
        "麻酔代・鎮静ケア・専用チップ・税金(10%)込みの最終総額を施術前に教えてください。",
    ),
    "gentle-numbing": (
        "통증에 약한 편이라 마취크림을 충분히 도포하고 시간을 넉넉히 두어 주시면 감사하겠습니다.",
        "痛みに弱いため、麻酔クリームをしっかり塗り十分な時間をおいていただけると助かります。",
    ),
    "one-hour": (
        "다음 일정이 있어 가능하면 1시간 이내로 마칠 수 있는지 먼저 알려주시면 감사하겠습니다.",
        "次の予定があるため、可能であれば1時間以内に終わるか事前に教えていただけると助かります。",
    ),
}
# web 예약 시트가 처음에 켜 두는 부탁
DEFAULT_ORDER_REQUESTS: tuple[OrderRequestId, ...] = ("no-upsell", "total-first")

TIME_LABELS: dict[TimeSlot, tuple[str, str]] = {
    "any": ("시간 상관없어요", "時間はいつでも大丈夫です"),
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
    date_ko = date or "(희망 날짜 선택)"
    date_ja = date or "（希望日を選択）"
    time_ko, time_ja = TIME_LABELS[time_slot]
    name = guest_name.strip()
    name_ko = name or "(여권 영문명 입력)"
    name_ja = name or "（パスポート英字名を入力）"
    count = max(1, min(5, js_round(people)))
    selected = [m for m in menus if m.id in menu_ids and m.id in place.prices]
    menus_ko = ", ".join(m.name_ko for m in selected) or "상담 후 결정"
    menus_ja = "・".join(m.name_ja for m in selected) or "相談して決定"

    if place.japanese_support:
        lang_ko = "가능하시면 일본어 상담되는 시간대로 잡아주시면 감사하겠습니다 :)"
        lang_ja = "できれば日本語で相談できる時間帯にしていただけるとうれしいです！"
    else:
        lang_ko = (
            "제가 한국어가 서툴러서 번역 앱으로 대화하고 있어요. "
            "짧은 한국어나 숫자로 답장해 주시면 정말 감사하겠습니다 :)"
        )
        lang_ja = (
            "韓国語が不慣れで翻訳アプリを使っています。"
            "短い韓国語や数字でお返事いただけると助かります！"
        )

    ko = "\n".join(
        [
            f"안녕하세요! {place.name_ko} 예약하고 싶어서 연락드렸어요.",
            "일본에서 여행 가는 길에 처음 방문할 예정이에요."
            if first_visit
            else "전에 방문했었고 이번에 다시 예약하려고 해요.",
            "",
            f"이름(여권 영문): {name_ko}",
            f"희망 일시: {date_ko} · {time_ko}",
            f"인원: {count}명",
            f"받고 싶은 시술: {menus_ko}",
            "",
            "혹시 이 날짜에 예약 가능할까요? 마취크림·전용 팁·부가세까지 포함해서 대략 총 얼마 정도 나올지도 같이 알려주시면 감사해요!",
            lang_ko,
        ]
    )
    ja = "\n".join(
        [
            f"こんにちは！{place.name_ja}を予約したくてご連絡しました。",
            "日本から旅行で伺う予定で、初診です。"
            if first_visit
            else "以前伺ったことがあり、再診で予約したいです。",
            "",
            f"名前（パスポート英字）：{name_ja}",
            f"希望日時：{date_ja}・{time_ja}",
            f"人数：{count}名",
            f"希望の施術：{menus_ja}",
            "",
            "こちらの日程で予約できますでしょうか？麻酔クリーム・専用チップ・税金込みで大体いくらくらいになるかも教えていただけるとうれしいです！",
            lang_ja,
        ]
    )
    return ko, ja


@dataclass(frozen=True)
class OrderItem:
    menu: Menu
    base_krw: int
    vat_krw: int
    total_jpy: int


@dataclass(frozen=True)
class OrderSheet:
    ko: str
    ja: str
    total_krw: int
    total_jpy: int


def _krw(amount: int) -> str:
    return f"₩{amount:,}"


def _jpy(amount: int) -> str:
    return f"¥{amount:,}"


def order_sheet_messages(
    place: Place,
    menus: Sequence[Menu],
    menu_ids: Sequence[str],
    *,
    requests: Sequence[OrderRequestId],
    krw_per_100_jpy: float,
    vat_rate: float,
) -> OrderSheet:
    """접수 메모(한국어·일본어)와 합계를 돌려준다. 부가세 포함, 1회 기준이다.

    - 시술은 `menus`의 순서로 적고, 이 병원에 가격이 없는 시술은 뺀다(web `computeClinicBundle`).
      남는 시술이 없으면 "상담 시 선택 예정"이다.
    - 합계 엔화는 시술마다 환산한 값의 합이다(web과 같음). 시술 하나면 병원 카드의 엔화와 같다.
    - 부탁 문장은 `requests`에 든 것만, web 순서대로 넣는다.
    """
    items: list[OrderItem] = []
    for menu in menus:
        offer = place.prices.get(menu.id)
        if menu.id not in menu_ids or offer is None:
            continue
        apply_vat = place.vat_excluded and menu.vat_applies_in_korea
        vat_krw = js_round(offer.krw * vat_rate) if apply_vat else 0
        items.append(
            OrderItem(
                menu=menu,
                base_krw=offer.krw,
                vat_krw=vat_krw,
                total_jpy=krw_to_jpy(offer.krw + vat_krw, krw_per_100_jpy),
            )
        )
    total_krw = sum(i.base_krw + i.vat_krw for i in items)
    total_jpy = sum(i.total_jpy for i in items)

    lines_ko = [
        f"- {i.menu.name_ko} (공식가 {_krw(i.base_krw)}{' + VAT 10%' if i.vat_krw > 0 else ''})"
        for i in items
    ] or ["- 상담 시 선택 예정"]
    lines_ja = [
        f"・{i.menu.name_ja}（公式 {_krw(i.base_krw)}{' + 税10%' if i.vat_krw > 0 else ''}"
        f" / 約{_jpy(i.total_jpy)}）"
        for i in items
    ] or ["・カウンセリング時に決定"]
    chosen = [ORDER_REQUESTS[r] for r in ORDER_REQUESTS if r in requests]

    ko = [
        "안녕하세요. 일본에서 방문한 고객입니다.",
        f"[{place.name_ko}] 공식 홈페이지 가격을 보고 아래 시술을 희망합니다.",
        "",
        "[희망 시술]",
        *lines_ko,
        f"예상 합계: {_krw(total_krw)} (부가세 10% 포함 예상 / 약 {_jpy(total_jpy)})",
        "",
    ]
    if chosen:
        ko += ["[부탁드리는 사항]", *(f"• {k}" for k, _ in chosen), ""]
    ko += [f"확인 사항: {place.caveat_ko}", "친절히 안내해 주셔서 감사합니다."]

    ja = [
        "こんにちは。日本から来院した患者です。",
        f"【{place.name_ja}】の公式料金を見て、以下の施術を希望しています。",
        "",
        "【希望メニュー】",
        *lines_ja,
        f"目安合計: {_jpy(total_jpy)}（{_krw(total_krw)}・税込10%目安）",
        "",
    ]
    if chosen:
        ja += ["【お願いしたいこと】", *(f"・{j}" for _, j in chosen), ""]
    ja += [
        f"事前確認メモ: {place.caveat_ja}",
        "ご丁寧なご案内をよろしくお願いいたします。",
    ]

    return OrderSheet(
        ko="\n".join(ko), ja="\n".join(ja), total_krw=total_krw, total_jpy=total_jpy
    )
