"""답변 속 금액을 도구 결과와 대조한다(설계 문서 6장 정확성, 12장 숫자 검증, FR-03).

- 통화 표시(¥, 円, 엔, ₩, 원, ウォン 등)가 붙은 숫자만 금액으로 본다. "300샷", "50%"는 보지 않는다.
- 이 대화의 도구 결과(카드 데이터와 결과 문장)와 사용자가 직접 쓴 금액(예산 등)에 같은 값이 있으면 맞는
  금액이다. 값은 정확히 같아야 한다. "約¥21,000"처럼 반올림한 금액도 걸러진다.
- 맞지 않는 금액은 그 금액이 든 괄호를 지우고, 괄호 밖이면 그 문장(표·목록이면 그 줄)을 지운다.
  어느 금액이 맞는지 코드가 알 수 없으므로 고쳐 쓰지 않는다.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

Currency = Literal["JPY", "KRW"]

_UNITS = {"억": 100_000_000, "万": 10_000, "만": 10_000, "천": 1_000, "千": 1_000}
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
# 28만 4천, 2万1,111, 21,111, 2.1万
_AMOUNT = rf"{_NUMBER}(?:\s?[억万만천千](?:\s?{_NUMBER})?)*"
_JPY_PREFIX = r"[¥￥]|JPY\s?"
_KRW_PREFIX = r"[₩￦]|KRW\s?"
_JPY_SUFFIX = r"円|엔|yen\b|JPY\b"
_KRW_SUFFIX = r"원|ウォン|won\b|KRW\b"
_RANGE = r"\s?[~〜～–—-]\s?"

_MONEY = re.compile(
    rf"(?:(?P<jpy_pre>{_JPY_PREFIX})|(?P<krw_pre>{_KRW_PREFIX}))(?P<pre_num>{_AMOUNT})"
    rf"|(?<![\d.,])(?P<suf_num>{_AMOUNT})\s?(?:(?P<jpy_suf>{_JPY_SUFFIX})|(?P<krw_suf>{_KRW_SUFFIX}))",
    re.IGNORECASE,
)
# 통화 표시 없이 금액 앞에 붙은 범위의 시작 값. 예: 26,000~66,000円의 26,000
_RANGE_START = re.compile(rf"(?<![\d.,])(?P<num>{_AMOUNT}){_RANGE}$")
# 사용자 메시지의 "3万", "5만"처럼 통화 없이 만·万 단위로 쓴 예산
_BARE_MAN = re.compile(rf"(?<![\d.,])({_NUMBER}\s?[万만](?:\s?{_NUMBER})?)")


@dataclass(frozen=True)
class Money:
    currency: Currency
    value: int
    start: int
    end: int


def parse_number(text: str) -> int | None:
    """'28만 4천' → 284000, '2万1,111' → 21111, '21,111' → 21111."""
    total = 0.0
    current: float | None = None
    for token in re.findall(rf"{_NUMBER}|[억万만천千]", text.replace(" ", "")):
        if token in _UNITS:
            total += (current if current is not None else 1) * _UNITS[token]
            current = None
        else:
            try:
                current = float(token.replace(",", ""))
            except ValueError:
                return None
    total += current or 0
    return round(total)


def find_money(text: str) -> list[Money]:
    """통화 표시가 붙은 금액. 범위(26,000~66,000円)는 두 값 모두 낸다."""
    found: list[Money] = []
    for m in _MONEY.finditer(text):
        is_jpy = bool(m.group("jpy_pre") or m.group("jpy_suf"))
        currency: Currency = "JPY" if is_jpy else "KRW"
        number = m.group("pre_num") or m.group("suf_num")
        value = parse_number(number)
        if value is None:
            continue
        if m.group("suf_num"):
            start = _RANGE_START.search(text[: m.start()])
            if start and not found_overlaps(found, start.start()):
                low = parse_number(start.group("num"))
                if low is not None:
                    found.append(Money(currency, low, start.start(), start.end()))
        found.append(Money(currency, value, m.start(), m.end()))
    return found


def found_overlaps(found: Sequence[Money], position: int) -> bool:
    return any(f.start <= position < f.end for f in found)


def _artifact_amounts(value: object, key: str = "") -> Iterable[tuple[Currency, int]]:
    """도구 artifact 안의 금액 필드(이름이 _jpy·_krw로 끝나는 정수)."""
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _artifact_amounts(v, str(k))
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _artifact_amounts(v, key)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        if key.endswith("_jpy"):
            yield "JPY", abs(round(value))
        elif key.endswith("_krw"):
            yield "KRW", abs(round(value))
        elif key == "krw_per_100_jpy":
            # "100엔당 905.3원", "1엔당 9.05원" 같은 환율 문장
            yield "JPY", 100
            yield "KRW", round(value)
            yield "KRW", int(value)
            yield "JPY", 1
            yield "KRW", round(value / 100)


def _message_text(message: BaseMessage) -> str:
    """사용자 메시지는 사용자가 쓴 첫 블록만 본다. 뒤 블록은 앱이 붙인 안내다."""
    if isinstance(message, HumanMessage) and isinstance(message.content, list):
        first = message.content[0] if message.content else ""
        return first.get("text", "") if isinstance(first, dict) else str(first)
    return message.text if hasattr(message, "text") else str(message.content)


def known_amounts(messages: Sequence[BaseMessage]) -> set[tuple[Currency, int]]:
    """이 대화에서 답변에 써도 되는 금액: 도구 결과와 사용자가 직접 쓴 금액."""
    known: set[tuple[Currency, int]] = set()
    for message in messages:
        if isinstance(message, ToolMessage):
            if message.status == "error":
                continue
            known.update(_artifact_amounts(message.artifact))
            known.update(
                (m.currency, m.value) for m in find_money(_message_text(message))
            )
        elif isinstance(message, HumanMessage):
            text = _message_text(message)
            known.update((m.currency, m.value) for m in find_money(text))
            for bare in _BARE_MAN.findall(text):
                value = parse_number(bare)
                if value is not None:
                    known.update({("JPY", value), ("KRW", value)})
    return known


# 문장 하나: 마침표·물음표 뒤에 공백이나 줄 끝이 오거나, 일본어 구두점에서 끝난다. 앞 공백은 문장에 붙는다.
_SENTENCE = re.compile(r".+?(?:[.!?](?=\s|$)|[。！？]|$)")
_PARENS = re.compile(r"\s?[(（][^()（）\n]*[)）]")


def _drop_parentheticals(line: str, known: set[tuple[Currency, int]]) -> str:
    def replace(m: re.Match[str]) -> str:
        bad = [x for x in find_money(m.group(0)) if (x.currency, x.value) not in known]
        return "" if bad else m.group(0)

    return _PARENS.sub(replace, line)


def _is_bad(text: str, known: set[tuple[Currency, int]]) -> list[Money]:
    return [m for m in find_money(text) if (m.currency, m.value) not in known]


def drop_unknown_amounts(
    text: str, known: set[tuple[Currency, int]]
) -> tuple[str, list[Money]]:
    """맞지 않는 금액을 괄호·문장·줄 단위로 지운다. (남은 글, 지운 금액)."""
    dropped = _is_bad(text, known)
    if not dropped:
        return text, []
    lines: list[str] = []
    for line in text.split("\n"):
        if not _is_bad(line, known):
            lines.append(line)
            continue
        line = _drop_parentheticals(line, known)
        if not _is_bad(line, known):
            lines.append(line)
            continue
        stripped = line.lstrip()
        if stripped.startswith(("|", "-", "*", "・", "•")) or re.match(
            r"\d+[.)]\s", stripped
        ):
            continue  # 표의 행, 목록 한 항목
        kept = "".join(
            s for s in _SENTENCE.findall(line) if not _is_bad(s, known)
        ).strip()
        if kept:
            lines.append(kept)
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return result, dropped
