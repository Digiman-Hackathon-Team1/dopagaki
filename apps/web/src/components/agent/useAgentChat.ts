import { useEffect, useRef, useState } from 'react';
import { api, type Schemas } from '@repo/api-client';
import { EventSourceParserStream } from 'eventsource-parser/stream';
import type { QuestionAnswer } from './Questions';
import { anonymousId } from './user-id';

export type TurnStatus = 'waiting' | 'writing' | 'invalid' | 'offline' | 'serverError' | 'interrupted';

type StreamEvent = Schemas['ChatStreamStart'] | Schemas['ChatStreamDelta'] | Schemas['ChatStreamDone'];

export type Turn = {
  id: number;
  message: string;
  answers?: QuestionAnswer[];
  status: TurnStatus | null;
  httpStatus?: number;
  reply?: Schemas['ChatResponse'];
  /** 최종 응답(done) 전까지 이어 붙인 답변 글(delta). done이 오면 reply.answer로 바뀐다. */
  streamed?: string;
};

/** 답이 없는 상태. 이때 화면은 시세표 안내를 보여 준다(설계 문서 FR-08). */
export const failed = (status: Turn['status']) =>
  status === 'invalid' || status === 'offline' || status === 'serverError' || status === 'interrupted';

/**
 * POST /api/agent/chat/stream 대화 한 건. 답변 글은 만들어지는 대로 이어 붙이고, 마지막 done 이벤트의 응답으로
 * 답변·카드·질문을 그린다(#44, #49). SSE는 eventsource-parser로 읽는다. 앞 메시지의 답을 기다리는 중에 보낸 메시지는 순서대로 보낸다(설계 문서 8장).
 * onSettled는 답을 받았거나 실패했을 때 한 번 불린다. 탭을 바꿔도 대화가 남도록 App에서 부른다.
 */
export function useAgentChat(lang: Schemas['ChatRequest']['lang'], onSettled?: (turn: Turn) => void) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const userId = useRef<string>(undefined);
  const lifetime = useRef<AbortController>(null);
  const chain = useRef(Promise.resolve());
  const thread = useRef<string>(undefined);
  const sequence = useRef(0);
  const settled = useRef(onSettled);
  settled.current = onSettled;

  useEffect(() => {
    lifetime.current = new AbortController();
    // reset()이 컨트롤러를 바꾸므로 화면을 떠날 때는 그때의 컨트롤러를 취소한다.
    const current = lifetime;
    return () => current.current?.abort();
  }, []);

  /** 대화를 비우고 새 대화로 시작한다. 보내는 중인 요청은 취소한다. */
  function reset() {
    lifetime.current?.abort();
    lifetime.current = new AbortController();
    chain.current = Promise.resolve();
    thread.current = undefined;
    setTurns([]);
  }

  /** 보냈으면 새 턴 번호, 빈 메시지나 2,000자를 넘는 메시지면 undefined. */
  function send(value: string, answers?: QuestionAnswer[]) {
    const message = value.trim();
    if (!message || [...message].length > 2000) return undefined;
    const id = ++sequence.current;
    setTurns(previous => [...previous, { id, message, answers, status: 'waiting' }]);
    function update(changes: Partial<Turn>) {
      setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, ...changes } : turn));
    }
    const signal = lifetime.current?.signal;
    chain.current = chain.current.then(async () => {
      if (signal?.aborted) return;
      update({ status: 'writing' });
      let result: Turn;
      let streamed = '';
      try {
        userId.current ??= anonymousId();
        const { data, response } = await api.POST('/api/agent/chat/stream', {
          signal,
          parseAs: 'stream',
          body: { userId: userId.current, threadId: thread.current, message, lang }
        });
        if (!response.ok || !data) {
          result = { id, message, answers, status: response.status >= 500 ? 'serverError' : 'invalid', httpStatus: response.status };
        } else {
          let reply: Schemas['ChatResponse'] | undefined;
          const reader = data
            .pipeThrough(new TextDecoderStream())
            .pipeThrough(new EventSourceParserStream())
            .getReader();
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            const event = JSON.parse(value.data) as StreamEvent;
            if (signal?.aborted) return;
            if (event.type === 'start') {
              thread.current = event.threadId;
            } else if (event.type === 'delta') {
              streamed += event.text;
              update({ streamed });
            } else {
              reply = event.response;
              await reader.cancel().catch(() => undefined);
              break;
            }
          }
          // done 없이 끝났으면 답이 중간에 끊긴 것이다. 보인 글은 남기고 시세표로 안내한다.
          result = reply
            ? { id, message, answers, reply, streamed, status: null }
            : { id, message, answers, streamed, status: 'interrupted' };
        }
      } catch {
        result = { id, message, answers, streamed, status: streamed ? 'interrupted' : 'offline' };
      }
      if (signal?.aborted) return;
      if (result.reply) thread.current = result.reply.threadId;
      // 화면 쪽 콜백이 실패해도 받은 결과와 뒤에 기다리는 메시지는 그대로 처리한다.
      update(result);
      try { settled.current?.(result); } catch { /* 분석·스크롤 실패는 대화에 영향을 주지 않는다. */ }
    });
    return id;
  }

  return { turns, send, reset };
}

export type AgentChat = ReturnType<typeof useAgentChat>;
