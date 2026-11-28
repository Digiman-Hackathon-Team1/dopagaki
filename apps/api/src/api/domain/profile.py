"""사용자 기억(프로필) 저장소(설계 문서 8장 사용자 기억 관리, ADR 0019).

대화가 바뀌어도 남는 사용자 조건을 사용자 ID별로 저장한다. 저장소는 `ProfileStore` 인터페이스로 감싸고,
지금은 서버 메모리 구현(`InMemoryProfileStore`)만 둔다. 메모리 저장소 종류가 정해지면(설계 문서 15장) 같은
인터페이스로 DB 구현을 더한다. 서버 메모리라 재시작하면 사라지고, 서버가 여러 개면 공유되지 않는다.

값 검사(사용자가 직접 말한 값인지, 건강 정보인지)는 에이전트가 저장 전에 한다. 여기서는 칸 단위로 합치고
보관 규칙만 맡는다.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from api.domain.catalog import ConcernId

Visitor = Literal["traveler", "resident"]
BudgetScope = Literal["per_treatment", "total"]

# 서울은 서머타임이 없어 고정 오프셋으로 둔다(배포 이미지의 tz 데이터에 기대지 않는다).
SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")

MAX_CONCERNS = 3
MAX_JP_TREATMENTS = 3
MAX_JP_TREATMENT_CHARS = 40


def seoul_today(now: datetime | None = None) -> date:
    """서울 날짜. 상대 날짜("모레")는 이 날짜를 기준으로 절대 날짜로 바꾼다."""
    return (now or datetime.now(UTC)).astimezone(SEOUL).date()


class ProfileBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: int = Field(gt=0)
    currency: Literal["JPY", "KRW"]
    # 병원 검색 예산은 시술 1회, 일정 예산은 전체다. 사용자가 말하지 않았으면 None.
    scope: BudgetScope | None = None


class UserProfile(BaseModel):
    """저장된 사용자 조건. 칸이 None이거나 비어 있으면 저장된 값이 없다."""

    model_config = ConfigDict(frozen=True)

    visitor: Visitor | None = None
    budget: ProfileBudget | None = None
    arrival_date: date | None = None
    return_date: date | None = None
    location_id: str | None = None
    concerns: tuple[ConcernId, ...] = Field(default=(), max_length=MAX_CONCERNS)
    avoid_needle: bool | None = None
    jp_treatments: tuple[str, ...] = Field(default=(), max_length=MAX_JP_TREATMENTS)
    updated_at: AwareDatetime | None = None

    def is_empty(self) -> bool:
        return not any(
            (
                self.visitor,
                self.budget,
                self.arrival_date,
                self.return_date,
                self.location_id,
                self.concerns,
                self.avoid_needle is not None,
                self.jp_treatments,
            )
        )


class ProfilePatch(BaseModel):
    """이번 턴에 바꿀 칸. None인 칸은 그대로 둔다. 목록 칸은 통째로 바꾼다."""

    model_config = ConfigDict(frozen=True)

    visitor: Visitor | None = None
    budget: ProfileBudget | None = None
    arrival_date: date | None = None
    return_date: date | None = None
    location_id: str | None = None
    concerns: tuple[ConcernId, ...] | None = None
    avoid_needle: bool | None = None
    jp_treatments: tuple[str, ...] | None = None

    def fields(self) -> list[str]:
        """값이 있는(바꿀) 칸 이름."""
        return [name for name, value in self if value is not None]


def merge(profile: UserProfile, patch: ProfilePatch, now: datetime) -> UserProfile:
    """칸 단위로 합친다. 같은 칸은 나중 값이 이긴다.

    도착일과 귀국일이 어긋나면(도착일 > 귀국일) 이번에 받은 쪽을 남기고 다른 쪽을 지운다.
    """
    update = {name: getattr(patch, name) for name in patch.fields()}
    merged = profile.model_copy(update={**update, "updated_at": now})
    if (
        merged.arrival_date
        and merged.return_date
        and merged.arrival_date > merged.return_date
    ):
        drop = "return_date" if "arrival_date" in update else "arrival_date"
        merged = merged.model_copy(update={drop: None})
    return merged


class ProfileStore(Protocol):
    def get(self, user_id: UUID) -> UserProfile | None:
        """저장된 프로필. 없거나 보관 기한이 지났으면 None."""
        ...

    def update(
        self, user_id: UUID, patch: ProfilePatch, *, only_if_exists: bool = False
    ) -> UserProfile | None:
        """칸 단위로 합쳐 저장하고 합친 결과를 돌려준다.

        only_if_exists면 저장된 프로필이 있을 때만 합친다. 턴을 시작할 때 있던 기억을 사용자가 턴 도중에 지웠으면
        저장하지 않고 None을 돌려준다(지운 기억을 되살리지 않는다).
        """
        ...

    def delete(self, user_id: UUID) -> bool:
        """지웠으면 True, 저장된 게 없었으면 False."""
        ...


class InMemoryProfileStore:
    """서버 메모리 저장소. 마지막 저장 뒤 ttl이 지난 프로필과, 상한을 넘은 가장 오래된 프로필을 지운다.

    잠금 하나로 읽기·합치기·지우기를 줄 세운다. 같은 사용자가 탭 두 개로 대화해도 서로 다른 칸은 둘 다 남는다.
    """

    def __init__(
        self,
        ttl: timedelta,
        max_users: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = ttl
        self._max_users = max_users
        self._now = now
        self._lock = threading.Lock()
        # 마지막으로 저장한 순서. 오래된 프로필이 앞에 온다.
        self._profiles: OrderedDict[UUID, UserProfile] = OrderedDict()

    def _expired(self, profile: UserProfile, now: datetime) -> bool:
        return profile.updated_at is None or now - profile.updated_at > self._ttl

    def get(self, user_id: UUID) -> UserProfile | None:
        now = self._now()
        with self._lock:
            profile = self._profiles.get(user_id)
            if profile is None:
                return None
            if self._expired(profile, now):
                del self._profiles[user_id]
                return None
            return profile

    def update(
        self, user_id: UUID, patch: ProfilePatch, *, only_if_exists: bool = False
    ) -> UserProfile | None:
        now = self._now()
        with self._lock:
            current = self._profiles.get(user_id)
            if current is None or self._expired(current, now):
                if only_if_exists:
                    return None
                current = UserProfile()
            merged = merge(current, patch, now)
            self._profiles[user_id] = merged
            self._profiles.move_to_end(user_id)
            self._evict(now)
            return merged

    def delete(self, user_id: UUID) -> bool:
        with self._lock:
            return self._profiles.pop(user_id, None) is not None

    def _evict(self, now: datetime) -> None:
        """보관 기한이 지난 프로필과 상한을 넘은 오래된 프로필을 지운다. 잠금 안에서 부른다."""
        # 저장한 순서로 놓여 있으므로 앞에서부터 보다가 기한이 남은 프로필에서 멈춘다.
        while self._profiles:
            oldest = next(iter(self._profiles.values()))
            if not self._expired(oldest, now):
                break
            self._profiles.popitem(last=False)
        while len(self._profiles) > self._max_users:
            self._profiles.popitem(last=False)
