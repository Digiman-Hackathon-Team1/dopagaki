"""대화 턴 보관소(설계 문서 10장 대화 기록 불러오기).

새로고침하거나 탭을 다시 열었을 때 화면이 같은 대화를 다시 그리도록, 턴마다 사용자 메시지와 돌려준 응답을
보관한다. 그래프 상태는 마지막 턴의 응답만 들고 있어서 지난 턴을 다시 그릴 수 없다.

- 키는 대화 기억과 같은 `"{userId}:{threadId}"`다(`thread_key`). 다른 사용자의 대화 ID로는 읽지 못한다.
- 서버 메모리에만 둔다. 서버를 재시작하면 대화 기억과 함께 사라진다.
- 보관 기한은 따로 두지 않는다. 대화 기억이 오래된 대화를 지울 때 `delete`를 같이 부른다. 다만 그 연결이
  빠져도 메모리가 계속 늘지 않도록 대화 수 상한을 넘으면 가장 오래 쓰지 않은 대화부터 버린다.
"""

import threading
from collections import OrderedDict, deque
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from api.agent.config import get_settings
from api.agent.schemas import ThreadTurn


def thread_key(user_id: UUID, thread_id: UUID) -> str:
    """대화 기억과 턴 보관소가 같이 쓰는 대화 키."""
    return f"{user_id}:{thread_id}"


class ThreadStore(Protocol):
    def append(self, key: str, turn: ThreadTurn) -> None:
        """대화 끝에 턴을 붙인다. 턴 상한을 넘으면 오래된 턴부터 버린다."""
        ...

    def get(self, key: str) -> list[ThreadTurn] | None:
        """보관된 턴(오래된 턴부터). 없으면 None."""
        ...

    def delete(self, key: str) -> bool:
        """지웠으면 True, 보관된 게 없었으면 False."""
        ...


class InMemoryThreadStore:
    """서버 메모리 보관소. 잠금 하나로 붙이기·읽기·지우기를 줄 세운다."""

    def __init__(self, max_turns: int, max_threads: int) -> None:
        self._max_turns = max_turns
        self._max_threads = max_threads
        self._lock = threading.Lock()
        # 마지막으로 턴을 붙인 순서. 오래된 대화가 앞에 온다.
        self._threads: OrderedDict[str, deque[ThreadTurn]] = OrderedDict()

    def append(self, key: str, turn: ThreadTurn) -> None:
        with self._lock:
            turns = self._threads.get(key)
            if turns is None:
                turns = self._threads[key] = deque(maxlen=self._max_turns)
            turns.append(turn)
            self._threads.move_to_end(key)
            while len(self._threads) > self._max_threads:
                self._threads.popitem(last=False)

    def get(self, key: str) -> list[ThreadTurn] | None:
        with self._lock:
            turns = self._threads.get(key)
            return None if turns is None else list(turns)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._threads.pop(key, None) is not None


@lru_cache
def get_thread_store() -> ThreadStore:
    """서버 메모리 턴 보관소. 턴·대화 수 상한은 대화 기억과 같다."""
    settings = get_settings()
    return InMemoryThreadStore(
        max_turns=settings.agent_max_turns, max_threads=settings.agent_max_threads
    )
