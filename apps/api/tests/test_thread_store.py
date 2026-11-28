"""대화 턴 보관소(설계 문서 10장 대화 기록 불러오기): 순서, 없는 키, 지우기, 턴·대화 수 상한."""

import threading
from uuid import uuid4

from api.agent.schemas import ThreadTurn
from api.agent.service import fallback_response
from api.agent.threads import InMemoryThreadStore, get_thread_store, thread_key


def turn(message: str) -> ThreadTurn:
    return ThreadTurn(
        message=message, response=fallback_response(uuid4(), "ko", "error")
    )


def messages(turns: list[ThreadTurn] | None) -> list[str] | None:
    return None if turns is None else [t.message for t in turns]


def test_returns_turns_in_append_order() -> None:
    store = InMemoryThreadStore(max_turns=50, max_threads=10)
    for text in ("첫째", "둘째", "셋째"):
        store.append("a", turn(text))

    assert messages(store.get("a")) == ["첫째", "둘째", "셋째"]


def test_missing_key_is_none() -> None:
    store = InMemoryThreadStore(max_turns=50, max_threads=10)
    store.append("a", turn("첫째"))

    assert store.get("b") is None


def test_delete_removes_thread() -> None:
    store = InMemoryThreadStore(max_turns=50, max_threads=10)
    store.append("a", turn("첫째"))

    assert store.delete("a") is True
    assert store.get("a") is None
    assert store.delete("a") is False


def test_drops_oldest_turns_over_turn_limit() -> None:
    store = InMemoryThreadStore(max_turns=3, max_threads=10)
    for i in range(5):
        store.append("a", turn(str(i)))

    assert messages(store.get("a")) == ["2", "3", "4"]


def test_drops_least_recently_used_thread_over_thread_limit() -> None:
    store = InMemoryThreadStore(max_turns=50, max_threads=2)
    store.append("a", turn("a1"))
    store.append("b", turn("b1"))
    store.append("a", turn("a2"))
    store.append("c", turn("c1"))

    assert store.get("b") is None
    assert messages(store.get("a")) == ["a1", "a2"]
    assert messages(store.get("c")) == ["c1"]


def test_get_returns_a_copy() -> None:
    store = InMemoryThreadStore(max_turns=50, max_threads=10)
    store.append("a", turn("첫째"))
    store.get("a").append(turn("밖에서 붙인 턴"))  # type: ignore[union-attr]

    assert messages(store.get("a")) == ["첫째"]


def test_concurrent_appends_keep_every_turn() -> None:
    store = InMemoryThreadStore(max_turns=1000, max_threads=10)
    one = turn("x")

    def worker() -> None:
        for _ in range(100):
            store.append("a", one)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store.get("a") or []) == 800


def test_key_is_the_conversation_memory_key() -> None:
    user, thread = uuid4(), uuid4()

    assert thread_key(user, thread) == f"{user}:{thread}"


def test_default_store_uses_conversation_limits() -> None:
    store = get_thread_store()

    assert isinstance(store, InMemoryThreadStore)
    assert store is get_thread_store()
