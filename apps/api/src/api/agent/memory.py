"""사용자 기억을 그래프에 잇는다(설계 문서 8장 사용자 기억 관리, ADR 0019).

- 저장: Claude가 응답 칸 `remember`에 이번 사용자 메시지의 조건을 적는다. 여기서 사용자가 이 대화에서 직접
  말한 값인지 대조하고, 상대 날짜를 서울 날짜로 바꾸고, 건강 정보를 거른 뒤 저장할 칸만 남긴다. 어긋난 칸은
  오류로 돌리지 않고 버린다. 오류로 돌리면 Claude가 다시 시도해 LLM 호출이 는다.
- 불러오기: 저장된 프로필을 영어 안내 한 블록으로 만든다. 그래프가 그 턴 사용자 메시지에 붙인다. 시스템
  프롬프트에 넣으면 사용자마다 프롬프트 캐시가 갈라진다.
"""

import logging
import re
import unicodedata
from collections.abc import Sequence
from datetime import date, timedelta
from functools import lru_cache

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage

from api.agent.amounts import MEMORY_NOTE_HEAD, user_amounts
from api.agent.config import get_settings
from api.agent.safety import mentions_health
from api.agent.schemas import Remember
from api.agent.tools.resolve_location import TOOL_NAME as RESOLVE_LOCATION
from api.agent.tools.resolve_location import ResolveLocationArtifact
from api.domain.locations import get_locations
from api.domain.profile import (
    MAX_CONCERNS,
    MAX_JP_TREATMENT_CHARS,
    MAX_JP_TREATMENTS,
    SEOUL,
    InMemoryProfileStore,
    ProfileBudget,
    ProfilePatch,
    ProfileStore,
    UserProfile,
)

logger = logging.getLogger(__name__)

# 날짜는 오늘부터 1년 안만 받는다.
MAX_DAYS_AHEAD = 365
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

TODAY_NOTE = "[Note from Dopa app] Today in Seoul: {today} ({weekday})."

# 방문 유형의 근거가 되는 말. 여행 중이거나 귀국한다(traveler), 서울에 산다(resident).
_VISITOR_EVIDENCE = {
    "traveler": re.compile(
        r"여행|관광|귀국|숙소|호텔|게하|\d\s?박|旅行|観光|帰国|ホテル|宿|\d\s?泊|来韓|訪韓|travel|trip",
        re.IGNORECASE,
    ),
    "resident": re.compile(
        r"살아|살고|사는|거주|이사|住んで|住み|在住|暮らし|live in|living in",
        re.IGNORECASE,
    ),
}
# 일본에서 받은 시술 칸에서 시술명 대신 몸 상태를 적은 글을 거른다(설계 6장 개인정보). 안전 키워드 밖의 말.
_SIDE_EFFECT = re.compile(
    r"알레르기|알러지|부작용|화상|흉터가|염증|アレルギー|副作用|やけど|火傷|炎症|allerg|side effect|burn",
    re.IGNORECASE,
)

_SCOPE_TEXT = {"per_treatment": " per treatment", "total": " for the whole trip"}


@lru_cache
def get_profile_store() -> ProfileStore:
    """서버 메모리 사용자 기억 저장소. 서버를 재시작하면 사라진다(설계 문서 15장 저장소 종류 결정 전)."""
    settings = get_settings()
    return InMemoryProfileStore(
        ttl=timedelta(seconds=settings.agent_profile_ttl_seconds),
        max_users=settings.agent_max_profiles,
    )


def today_note(today: date) -> str:
    return TODAY_NOTE.format(
        today=today.isoformat(), weekday=_WEEKDAYS[today.weekday()]
    )


def _user_texts(messages: Sequence[BaseMessage]) -> list[str]:
    """사용자가 쓴 본문(첫 블록)만. 뒤 블록은 앱이 붙인 안내다."""
    texts = []
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        if isinstance(message.content, str):
            texts.append(message.content)
        elif message.content:
            first = message.content[0]
            texts.append(
                first.get("text", "") if isinstance(first, dict) else str(first)
            )
    return texts


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _found_location_ids(messages: Sequence[BaseMessage]) -> set[str]:
    found = set()
    for message in messages:
        if (
            isinstance(message, ToolMessage)
            and message.name == RESOLVE_LOCATION
            and message.status != "error"
        ):
            location = ResolveLocationArtifact.model_validate(message.artifact).location
            if location:
                found.add(location.location_id)
    return found


def _day(in_days: int | None, iso: str | None, today: date) -> date | None:
    """며칠 뒤(오늘 0) 또는 YYYY-MM-DD를 서울 날짜로. 오늘부터 1년 안이 아니면 None."""
    if in_days is not None:
        day = (
            today + timedelta(days=in_days) if 0 <= in_days <= MAX_DAYS_AHEAD else None
        )
    elif iso:
        try:
            day = date.fromisoformat(iso.strip())
        except ValueError:
            day = None
    else:
        return None
    if day is None or not today <= day <= today + timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError
    return day


def to_patch(
    remember: Remember, messages: Sequence[BaseMessage], today: date
) -> tuple[ProfilePatch | None, list[str]]:
    """`remember`에서 저장할 칸만 남긴다. (바꿀 칸, 버린 칸 이름).

    - 예산 금액: 이 대화의 사용자 본문에 같은 금액이 있어야 한다("3万"은 엔·원 둘 다로 본다).
    - 일본에서 받은 시술: 사용자 본문에 있는 글이어야 하고(NFKC·대소문자 무시), 건강 정보가 들면 버린다.
    - 위치: 이 대화에서 resolve_location이 찾은 위치 ID여야 한다.
    - 날짜: 오늘(서울)부터 1년 안. 도착일이 귀국일보다 늦으면 도착일을 버린다.
    - 방문 유형·고민·바늘 기피는 대조할 수 없어 형식만 본다. 고민은 3개까지다.
    """
    update: dict = {}
    dropped: list[str] = []
    texts = _user_texts(messages)

    if remember.visitor is not None:
        # 기본값(여행자)이 저장된 거주자를 덮어쓰지 않도록, 사용자 글에 근거가 있을 때만 저장한다.
        if _VISITOR_EVIDENCE[remember.visitor].search("\n".join(texts)):
            update["visitor"] = remember.visitor
        else:
            dropped.append("visitor")
    if remember.avoid_needle is not None:
        update["avoid_needle"] = remember.avoid_needle

    if (budget := remember.budget) is not None:
        if budget.amount > 0 and (budget.currency, budget.amount) in user_amounts(
            messages
        ):
            update["budget"] = ProfileBudget(
                amount=budget.amount, currency=budget.currency, scope=budget.scope
            )
        else:
            dropped.append("budget")

    dates: dict[str, date] = {}
    for name, in_days, iso in (
        ("arrival_date", remember.arrival_in_days, remember.arrival_date),
        ("return_date", remember.return_in_days, remember.return_date),
    ):
        try:
            if (day := _day(in_days, iso, today)) is not None:
                dates[name] = day
        except ValueError:
            dropped.append(name)
    if (
        "arrival_date" in dates
        and "return_date" in dates
        and dates["arrival_date"] > dates["return_date"]
    ):
        del dates["arrival_date"]
        dropped.append("arrival_date")
    update.update(dates)

    if (location_id := remember.location_id) is not None:
        if location_id in _found_location_ids(messages) and get_locations().get(
            location_id
        ):
            update["location_id"] = location_id
        else:
            dropped.append("location_id")

    if remember.concerns:
        update["concerns"] = tuple(dict.fromkeys(remember.concerns))[:MAX_CONCERNS]

    if remember.jp_treatments:
        said = _normalize("\n".join(texts))
        kept = []
        for treatment in remember.jp_treatments:
            text = treatment.strip()
            if (
                text
                and len(text) <= MAX_JP_TREATMENT_CHARS
                and _normalize(text) in said
                and not mentions_health(text)
                and not _SIDE_EFFECT.search(text)
            ):
                kept.append(text)
            else:
                dropped.append("jp_treatments")
        if kept:
            update["jp_treatments"] = tuple(dict.fromkeys(kept))[:MAX_JP_TREATMENTS]

    patch = ProfilePatch(**update) if update else None
    return patch, list(dict.fromkeys(dropped))


def _in_days(days: int) -> str:
    return {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")


def _ago(days: int) -> str:
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


def profile_note(profile: UserProfile | None, today: date) -> str | None:
    """저장된 조건을 안내 한 블록으로. 안내할 조건이 없으면 None.

    남은 날수는 코드가 계산해 적는다. 귀국일이 지났으면 체류 기간을 넣지 않고, 도착일이 지났으면 도착일만 뺀다.
    """
    if profile is None or profile.updated_at is None:
        return None
    lines: list[str] = []
    if profile.visitor:
        lines.append(f"- visitor: {profile.visitor}")
    if budget := profile.budget:
        scope = _SCOPE_TEXT.get(budget.scope or "", "")
        lines.append(f"- budget: {budget.amount:,} {budget.currency}{scope}")
    stay_ongoing = profile.return_date is None or profile.return_date >= today
    if profile.arrival_date and profile.arrival_date >= today and stay_ongoing:
        days = (profile.arrival_date - today).days
        lines.append(
            f"- arrival in Seoul: {profile.arrival_date.isoformat()} ({_in_days(days)})"
        )
    if profile.return_date and stay_ongoing:
        days = (profile.return_date - today).days
        lines.append(
            f"- return date: {profile.return_date.isoformat()} ({_in_days(days)}; "
            f"from today to the return day is {days + 1} days)"
        )
    if profile.location_id and (location := get_locations().get(profile.location_id)):
        lines.append(
            f"- where they stay or live: {location.name_ko} / {location.name_ja} "
            f"(location_id {location.id})"
        )
    if profile.concerns:
        lines.append(f"- concerns: {', '.join(profile.concerns)}")
    if profile.avoid_needle is not None:
        lines.append(
            "- avoids needles" if profile.avoid_needle else "- needles are fine"
        )
    if profile.jp_treatments:
        lines.append(
            f"- treatments they had in Japan: {', '.join(profile.jp_treatments)}"
        )
    if not lines:
        return None
    saved = profile.updated_at.astimezone(SEOUL).date()
    head = (
        f"{MEMORY_NOTE_HEAD} (what the user said before; last saved "
        f"{saved.isoformat()}, {_ago((today - saved).days)}). Use them only for what the user "
        "has not said in this conversation and do not ask for them again; what the user says "
        "now wins. Say the ones you use in condition_summary."
    )
    return "\n".join([head, *lines])
