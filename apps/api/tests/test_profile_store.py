"""사용자 기억 저장소(설계 문서 8장 사용자 기억 관리, ADR 0019): 칸 단위 합치기, 보관 기한, 사용자 수 상한."""

import threading
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from api.domain.profile import (
    InMemoryProfileStore,
    ProfileBudget,
    ProfilePatch,
    seoul_today,
)

START = datetime(2026, 11, 28, 3, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


def store(clock: Clock | None = None, max_users: int = 100) -> InMemoryProfileStore:
    return InMemoryProfileStore(
        ttl=timedelta(days=30), max_users=max_users, now=clock or Clock()
    )


def test_update_merges_fields_and_keeps_others() -> None:
    profiles, user = store(), uuid4()
    profiles.update(
        user, ProfilePatch(budget=ProfileBudget(amount=30000, currency="JPY"))
    )
    profiles.update(user, ProfilePatch(return_date=date(2026, 11, 30)))

    saved = profiles.get(user)
    assert saved.budget == ProfileBudget(amount=30000, currency="JPY")
    assert saved.return_date == date(2026, 11, 30)
    assert saved.updated_at == START


def test_later_value_overwrites_earlier() -> None:
    profiles, user = store(), uuid4()
    profiles.update(
        user, ProfilePatch(budget=ProfileBudget(amount=30000, currency="JPY"))
    )
    profiles.update(
        user, ProfilePatch(budget=ProfileBudget(amount=50000, currency="JPY"))
    )

    assert profiles.get(user).budget.amount == 50000


def test_arrival_after_return_keeps_the_newer_one() -> None:
    profiles, user = store(), uuid4()
    profiles.update(user, ProfilePatch(arrival_date=date(2026, 12, 5)))
    profiles.update(user, ProfilePatch(return_date=date(2026, 12, 1)))

    saved = profiles.get(user)
    assert saved.return_date == date(2026, 12, 1)
    assert saved.arrival_date is None


def test_delete_is_idempotent() -> None:
    profiles, user = store(), uuid4()
    profiles.update(user, ProfilePatch(visitor="traveler"))

    assert profiles.delete(user) is True
    assert profiles.delete(user) is False
    assert profiles.get(user) is None


def test_profile_expires_30_days_after_last_save() -> None:
    clock = Clock()
    profiles, user = store(clock), uuid4()
    profiles.update(user, ProfilePatch(visitor="traveler"))

    clock.now = START + timedelta(days=20)
    profiles.update(user, ProfilePatch(avoid_needle=True))  # 저장할 때마다 다시 30일
    clock.now = START + timedelta(days=45)
    assert profiles.get(user).visitor == "traveler"

    clock.now = START + timedelta(days=51)
    assert profiles.get(user) is None


def test_expired_profile_is_not_merged_into_new_save() -> None:
    clock = Clock()
    profiles, user = store(clock), uuid4()
    profiles.update(user, ProfilePatch(visitor="traveler"))
    clock.now = START + timedelta(days=31)

    saved = profiles.update(user, ProfilePatch(avoid_needle=True))

    assert saved.visitor is None
    assert saved.avoid_needle is True


def test_oldest_profile_dropped_over_capacity() -> None:
    clock = Clock()
    profiles = store(clock, max_users=2)
    first, second, third = uuid4(), uuid4(), uuid4()
    for i, user in enumerate((first, second, third)):
        clock.now = START + timedelta(minutes=i)
        profiles.update(user, ProfilePatch(visitor="traveler"))

    assert profiles.get(first) is None
    assert profiles.get(second) is not None
    assert profiles.get(third) is not None


def test_concurrent_updates_keep_all_fields() -> None:
    profiles, user = store(), uuid4()
    patches = [
        ProfilePatch(visitor="resident"),
        ProfilePatch(budget=ProfileBudget(amount=50000, currency="KRW")),
        ProfilePatch(avoid_needle=False),
        ProfilePatch(concerns=("lift",)),
    ] * 25
    threads = [
        threading.Thread(target=profiles.update, args=(user, p)) for p in patches
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    saved = profiles.get(user)
    assert saved.visitor == "resident"
    assert saved.budget.amount == 50000
    assert saved.avoid_needle is False
    assert saved.concerns == ("lift",)


def test_seoul_today_crosses_midnight_before_utc() -> None:
    # UTC 15:00은 서울 다음 날 0시다.
    assert seoul_today(datetime(2026, 11, 28, 14, 59, tzinfo=UTC)) == date(2026, 11, 28)
    assert seoul_today(datetime(2026, 11, 28, 15, 0, tzinfo=UTC)) == date(2026, 11, 29)


def test_only_if_exists_skips_when_profile_was_deleted() -> None:
    profiles, user = store(), uuid4()

    assert (
        profiles.update(user, ProfilePatch(visitor="traveler"), only_if_exists=True)
        is None
    )
    assert profiles.get(user) is None

    profiles.update(user, ProfilePatch(visitor="traveler"))
    saved = profiles.update(user, ProfilePatch(avoid_needle=True), only_if_exists=True)
    assert saved.visitor == "traveler"
    assert saved.avoid_needle is True
