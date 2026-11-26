"""환율 조회(ADR 0010).

web은 빌드할 때 네이버 환율을 받아 `naver-fx.json`으로 배포하고, 화면은 그 파일을 읽는다.
에이전트도 배포된 같은 파일을 읽어 web과 같은 환율을 쓴다.

읽는 순서: 배포된 web의 `naver-fx.json` → 마지막으로 읽은 값(서버 메모리) → 스냅샷 값.
- 한 번 읽은 값은 `ttl_seconds` 동안 다시 읽지 않는다. web 파일은 web을 배포할 때만 바뀐다.
- 읽기 실패(시간 초과, HTTP 오류, 형식 오류, 600~1500원 범위 밖)는 다음 순서의 값으로 넘어간다.
- 마지막으로 읽은 값은 메모리에만 있어 API를 재시작하면 지워진다.
"""

import logging
import threading
import time
from collections.abc import Callable

import httpx
from pydantic import ValidationError

from api.domain.catalog import Fx

logger = logging.getLogger(__name__)

# web App.tsx가 받아들이는 범위와 같다.
MIN_KRW_PER_100_JPY = 600
MAX_KRW_PER_100_JPY = 1500


def fetch_json(url: str, timeout: float) -> object:
    # 캐시를 피하려고 v를 붙인다. 주소에 원래 있던 쿼리는 그대로 둔다.
    target = httpx.URL(url).copy_merge_params({"v": str(int(time.time()))})
    response = httpx.get(target, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.json()


class FxProvider:
    def __init__(
        self,
        url: str | None,
        snapshot: Fx,
        ttl_seconds: float = 600,
        timeout_seconds: float = 2,
        fetch: Callable[[str, float], object] = fetch_json,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._snapshot = snapshot
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._fetch = fetch
        self._clock = clock
        self._last: Fx | None = None
        self._checked_at: float | None = None
        self._lock = threading.Lock()

    def current(self) -> Fx:
        """지금 쓸 환율. 예외를 내지 않는다.

        web 파일을 읽는 요청 하나만 기다리고, 그동안 들어온 다른 요청은 이전 값을 바로 쓴다.
        실패해도 ttl 동안은 다시 시도하지 않는다. web이 내려가 있을 때 요청마다 기다리지 않게 한다.
        """
        if not self._url:
            return self._snapshot
        with self._lock:
            now = self._clock()
            due = self._checked_at is None or now - self._checked_at >= self._ttl
            if due:
                self._checked_at = now
        if due:
            fresh = self._read(self._url)
            if fresh is not None:
                with self._lock:
                    self._last = fresh
        return self._last or self._snapshot

    def _read(self, url: str) -> Fx | None:
        try:
            raw = self._fetch(url, self._timeout)
        # HTTPError: 시간 초과·연결·HTTP 상태 오류, InvalidURL: 잘못된 WEB_FX_URL,
        # ValueError: JSON이 아님, OSError: 그 밖의 네트워크 오류
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, OSError) as error:
            logger.warning("web 환율을 읽지 못해 이전 값을 씁니다: %s", error)
            return None
        rate = raw.get("krwPer100Jpy") if isinstance(raw, dict) else None
        # web App.tsx와 같은 검사: 숫자이고 600~1500 범위여야 한다. 문자열 "900"은 받지 않는다.
        if (
            isinstance(rate, bool)
            or not isinstance(rate, int | float)
            or not MIN_KRW_PER_100_JPY <= rate <= MAX_KRW_PER_100_JPY
        ):
            logger.warning("web 환율 값이 올바르지 않아 쓰지 않습니다: %r", rate)
            return None
        # web처럼 빠진 칸은 앞의 값으로 채운다(web: {...prev, ...fresh}).
        try:
            return Fx.model_validate(
                {**self._snapshot.model_dump(by_alias=True), **raw}
            )
        except ValidationError as error:
            logger.warning("web 환율 파일 형식이 달라 쓰지 않습니다: %s", error)
            return None
