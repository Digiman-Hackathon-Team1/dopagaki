"""데모 응답 캐싱(설계 문서 2장 대체 동작, 13장 데모 고정).

시연 전에 정한 대본을 실제 Claude로 한 번 돌려 턴마다 응답과 대화 기록을 녹화해 둔다
(`scripts/record_demo.py`). 서버를 `AGENT_DEMO_CACHE=1`로 켜면, 새 대화의 메시지가 대본과 같은 동안은
Claude를 부르지 않고 녹화한 응답을 돌려준다. 현장 네트워크가 느리거나 Claude가 답하지 못해도 시연이
멈추지 않고, 매번 같은 답이 나온다.

대본과 다른 메시지가 오면 그 턴부터 실제 Claude가 답한다. 녹화한 대화 기록을 대화 기억에 넣어 두므로
앞 턴 맥락(찾은 병원, 기준 위치 등)을 이어받는다. 이때 녹화한 thinking 블록은 뺀다. thinking 블록은
만들어질 때와 요청 앞부분(시스템 프롬프트, 도구 설명)이 같을 때만 받아들여지는데, 녹화 뒤 프롬프트가
바뀌었을 수 있기 때문이다.

대본 비교는 앞뒤 공백, 전각·반각, 끝 문장부호(。？! 등)를 무시한다. 시연자가 직접 쳐도 맞게 하기 위해서다.
"""

import json
import logging
import re
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, messages_from_dict
from pydantic import BaseModel, Field

from api.agent.schemas import ChatResponse, Lang

logger = logging.getLogger(__name__)

DEMO_PATH = Path(__file__).resolve().parent / "data" / "demo-replies.json"

_TRAILING = re.compile(r"[\s。．.、,，？?！!~〜～]+$")


class DemoTurn(BaseModel):
    message: str
    # 녹화한 응답. thread_id는 요청마다 바꿔 넣는다.
    response: dict
    # 이 턴에 대화 기억에 더해진 메시지(langchain messages_to_dict 형식)
    messages: list[dict]


class DemoScript(BaseModel):
    id: str
    lang: Lang
    recorded_at: str
    model: str
    turns: list[DemoTurn] = Field(min_length=1)


class DemoFile(BaseModel):
    scripts: list[DemoScript] = Field(default_factory=list)


def normalize(message: str) -> str:
    text = unicodedata.normalize("NFKC", message).strip()
    text = _TRAILING.sub("", text)
    return re.sub(r"\s+", " ", text)


@lru_cache
def load_demo(path: Path = DEMO_PATH) -> DemoFile:
    if not path.exists():
        return DemoFile()
    return DemoFile.model_validate_json(path.read_text())


def without_thinking(messages: list[BaseMessage]) -> list[BaseMessage]:
    """녹화한 assistant 메시지에서 thinking 블록을 뺀다."""
    out: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, AIMessage) and isinstance(message.content, list):
            content = [
                b
                for b in message.content
                if not (
                    isinstance(b, dict)
                    and b.get("type") in ("thinking", "redacted_thinking")
                )
            ]
            message = message.model_copy(update={"content": content})
        out.append(message)
    return out


class DemoSessions:
    """대화마다 대본의 몇 번째 턴까지 왔는지 기억한다. 서버 메모리에 두므로 재시작하면 사라진다."""

    # 기억할 대화 수 상한. 넘으면 오래된 대화부터 잊는다(설계 문서 8장 대화 수 상한과 같은 값).
    MAX_THREADS = 1000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 대화 키 → (대본 ID, 다음 턴 번호). 대본에서 벗어난 대화는 None이다. 넣은 순서를 지킨다.
        self._threads: dict[str, tuple[str, int] | None] = {}

    def _set(self, key: str, value: tuple[str, int] | None) -> None:
        self._threads.pop(key, None)
        self._threads[key] = value
        while len(self._threads) > self.MAX_THREADS:
            del self._threads[next(iter(self._threads))]

    def take(
        self, demo: DemoFile, key: str, is_new: bool, message: str, lang: Lang
    ) -> tuple[DemoScript, DemoTurn] | None:
        """이번 메시지가 대본의 다음 턴과 같으면 그 턴을 돌려주고 진행을 한 칸 옮긴다."""
        wanted = normalize(message)
        with self._lock:
            if key not in self._threads and not is_new:
                # 데모가 아닌 대화(또는 서버 재시작 전 대화)는 손대지 않는다.
                return None
            position = self._threads.get(key)
            if key not in self._threads:
                candidates = [(s, 0) for s in demo.scripts]
            elif position is None:
                return None
            else:
                script_id, index = position
                candidates = [(s, index) for s in demo.scripts if s.id == script_id]
            for script, index in candidates:
                if (
                    script.lang == lang
                    and index < len(script.turns)
                    and normalize(script.turns[index].message) == wanted
                ):
                    self._set(key, (script.id, index + 1))
                    return script, script.turns[index]
            # 대본에서 벗어나면 그 대화는 끝까지 실제 Claude가 답한다.
            self._set(key, None)
            return None


def recorded_messages(turn: DemoTurn) -> list[BaseMessage]:
    return without_thinking(messages_from_dict(turn.messages))


def demo_response(turn: DemoTurn, thread_id) -> ChatResponse:
    return ChatResponse.model_validate({**turn.response, "threadId": str(thread_id)})


def dump_demo(demo: DemoFile, path: Path = DEMO_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(demo.model_dump(), ensure_ascii=False, indent=2) + "\n")
    load_demo.cache_clear()
