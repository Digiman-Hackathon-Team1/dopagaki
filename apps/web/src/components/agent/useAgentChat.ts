import { useEffect, useRef, useState } from 'react';
import { api, type Schemas } from '@repo/api-client';
import { EventSourceParserStream } from 'eventsource-parser/stream';
import type { QuestionAnswer } from './Questions';
import { anonymousId } from './user-id';

/**
 * 새로고침하거나 탭을 닫았다 다시 열어도 같은 대화를 이어 보도록, 대화 ID를 localStorage에 두고 화면을 열 때
 * 서버가 보관한 지난 턴(getAgentThread)을 받아 다시 그린다(#94). 대화 내용은 브라우저에 두지 않는다.
 * 서버가 그 대화를 모르면(보관 기한 24시간이 지났거나 서버를 다시 시작함) 404라서 새 대화로 시작한다.
 */
const THREAD_KEY = 'dopagaki.agent.threadId';
/** 1단계(같은 탭 sessionStorage 백업, #99)에서 쓰던 키. 남아 있으면 지운다. */
const OLD_SESSION_KEY = 'dopagaki.agent.session';

function savedThread(): string | undefined {
  try { sessionStorage.removeItem(OLD_SESSION_KEY); } catch { /* 없거나 못 지워도 상관없다. */ }
  try { return localStorage.getItem(THREAD_KEY) ?? undefined; } catch { return undefined; }
}

function saveThread(id: string | undefined) {
  try {
    if (id) localStorage.setItem(THREAD_KEY, id);
    else localStorage.removeItem(THREAD_KEY);
  } catch { /* 저장할 수 없으면(사생활 보호 모드) 이번 화면에서만 대화가 이어진다. */ }
}

/**
 * 서버가 보관한 지난 턴을 화면의 턴으로 바꾼다. 이미 답한 턴의 질문 묶음은 비운다. 질문 묶음이 있는 응답을
 * 그리면 '질문에 답하기' 시트가 열리므로, 비우지 않으면 지난 시트가 한꺼번에 열린다. 마지막 턴의 질문만 남긴다.
 */
function restoredTurns(history: Schemas['ThreadTurn'][], firstId: number): Turn[] {
  return history.map((turn, index) => ({
    id: firstId + index,
    message: turn.message,
    reply: index === history.length - 1 ? turn.response : { ...turn.response, questions: [] },
    status: null
  }));
}

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
    const controller = new AbortController();
    lifetime.current = controller;
    // reset()이 컨트롤러를 바꾸므로 화면을 떠날 때는 그때의 컨트롤러를 취소한다.
    const current = lifetime;
    restore(controller.signal);
    return () => current.current?.abort();
  }, []);

  // 대화 ID는 마지막으로 답을 받은 턴의 것을 남긴다. 첫 답을 받기 전에 새로고침하면 새 대화로 시작한다.
  const lastThread = turns.filter(turn => turn.reply && turn.status === null).at(-1)?.reply?.threadId;
  useEffect(() => { if (lastThread) saveThread(lastThread); }, [lastThread]);

  /**
   * 남겨 둔 대화 ID가 있으면 지난 턴을 받아 앞에 붙인다. 그 사이 보낸 메시지는 복원이 끝난 뒤 보내도록 같은
   * 줄(chain)에 세운다. 연결할 수 없으면 대화 ID는 남겨 두고 이번 화면은 빈 대화로 시작한다.
   */
  function restore(signal: AbortSignal) {
    const saved = savedThread();
    if (!saved) return;
    chain.current = chain.current.then(async () => {
      try {
        userId.current ??= anonymousId();
        const { data, response } = await api.GET('/api/agent/users/{userId}/threads/{threadId}', {
          signal,
          params: { path: { userId: userId.current, threadId: saved } }
        });
        if (signal.aborted) return;
        if (response.status === 404) {
          saveThread(undefined);
          return;
        }
        if (!data || data.turns.length === 0) return;
        const firstId = sequence.current + 1;
        sequence.current += data.turns.length;
        thread.current = data.threadId;
        setTurns(previous => [...restoredTurns(data.turns, firstId), ...previous]);
      } catch { /* 연결할 수 없음: 빈 대화로 시작한다. */ }
    });
  }

  /** 대화를 비우고 새 대화로 시작한다. 보내는 중인 요청은 취소한다. */
  function reset() {
    lifetime.current?.abort();
    lifetime.current = new AbortController();
    chain.current = Promise.resolve();
    thread.current = undefined;
    saveThread(undefined);
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
      // 응답을 받기 시작한 뒤의 예외(파싱 실패·연결 끊김)는 '연결할 수 없음'이 아니라 '중간에 끊김'이다.
      let connected = false;
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
          connected = true;
          let reply: Schemas['ChatResponse'] | undefined;
          const reader = data
            .pipeThrough(new TextDecoderStream())
            .pipeThrough(new EventSourceParserStream())
            .getReader();
          try {
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
              } else if (event.type === 'done') {
                reply = event.response;
                break;
              }
              // 모르는 이벤트는 건너뛴다.
            }
          } finally {
            // done 뒤의 나머지나 예외로 남은 응답을 닫아, 서버가 더 보내지 않게 한다.
            await reader.cancel().catch(() => undefined);
          }
          // done 없이 끝났으면 답이 중간에 끊긴 것이다. 보인 글은 남기고 시세표로 안내한다.
          result = reply
            ? { id, message, answers, reply, streamed, status: null }
            : { id, message, answers, streamed, status: 'interrupted' };
        }
      } catch {
        result = { id, message, answers, streamed, status: connected ? 'interrupted' : 'offline' };
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
