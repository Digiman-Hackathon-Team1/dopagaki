"""에이전트 테스트용 가짜 LLM. API 키 없이 그래프를 돌린다."""

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import Field


class FakeChatModel(GenericFakeChatModel):
    """정해진 응답을 차례로 내고, 받은 메시지 목록을 기록하는 가짜 LLM."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)
    # 호출마다 받은 bind 인자(tools 제외). 캐시 표시처럼 요청 전체에 붙는 값을 확인할 때 쓴다.
    call_kwargs: list[dict[str, Any]] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        return self.bind(tools=tools, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        self.call_kwargs.append({k: v for k, v in kwargs.items() if k != "tools"})
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """스트리밍(stream_mode="messages")에서도 도구 호출을 잃지 않게 한다.

        GenericFakeChatModel은 스트리밍 때 글만 조각내 보내고 tool_calls를 버린다. 도구 호출 메시지는
        한 조각으로 보내고, 글은 공백 단위로 나눠 보낸다.
        """
        message = (
            self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            .generations[0]
            .message
        )
        if message.tool_calls or not message.content:
            chunks = [
                AIMessageChunk(
                    content=message.content,
                    id=message.id,
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"], ensure_ascii=False),
                            "id": call["id"],
                            "index": i,
                        }
                        for i, call in enumerate(message.tool_calls)
                    ],
                )
            ]
        else:
            chunks = [
                AIMessageChunk(content=token, id=message.id)
                for token in re.split(r"(\s)", message.content)
                if token
            ]
        for chunk in chunks:
            generation = ChatGenerationChunk(message=chunk)
            if run_manager:
                run_manager.on_llm_new_token(chunk.text, chunk=generation)
            yield generation


def fake_model(*responses: AIMessage) -> FakeChatModel:
    return FakeChatModel(messages=iter(responses))


def reply_message(
    answer: str = "답변",
    card_reasons: dict[str, str] | None = None,
    *,
    safety_stop: bool = False,
    condition_summary: str = "",
    cautions: Sequence[str] = (),
    suggestions: Sequence[str] = ("a", "b", "c"),
) -> AIMessage:
    """응답 칸 JSON을 내는 AI 메시지. card_reasons는 {card_id: 추천 이유}."""
    reply = {
        "condition_summary": condition_summary,
        "answer": answer,
        "card_reasons": [
            {"card_id": card_id, "reason": reason}
            for card_id, reason in (card_reasons or {}).items()
        ],
        "questions": [],
        "suggestions": list(suggestions),
        "cautions": list(cautions),
        "safety_stop": safety_stop,
    }
    return AIMessage(content=json.dumps(reply, ensure_ascii=False))


def tool_call(name: str, args: dict, call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


def user_texts(messages: list[BaseMessage]) -> list[str]:
    """사용자 메시지의 본문(첫 텍스트 블록)만 꺼낸다. 두 번째 블록은 앱이 붙인 언어 지시다."""
    texts = []
    for m in messages:
        if m.type != "human":
            continue
        texts.append(m.content if isinstance(m.content, str) else m.content[0]["text"])
    return texts


class FailingChatModel(FakeChatModel):
    """정해진 횟수만큼 예외를 낸 뒤 정해진 응답을 내는 가짜 LLM. API 장애(401, 529, 시간 초과 등)를 흉내 낸다."""

    failures: int = 1
    error: type[Exception] = RuntimeError

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.failures > 0:
            self.failures -= 1
            self.calls.append(list(messages))
            raise self.error("simulated API failure")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
