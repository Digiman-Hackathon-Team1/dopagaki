import type { Schemas } from '@repo/api-client';
import { AnswerText, Reply } from './Reply';
import { PriceTableLink } from './PriceTableLink';
import type { Send } from './Questions';
import { copy } from './copy';
import { failed, type Turn } from './useAgentChat';

/**
 * 사용자 말과 에이전트 답을 차례로 그린다. 최종 응답 전에는 이어 붙인 답변 글을 먼저 보여 주고,
 * 답이 없거나 끊기면 상태와 시세표 안내를 보여 준다(FR-08).
 */
export function Turns({ turns, lang, send, onPriceTable }: {
  turns: Turn[];
  lang: Schemas['ChatRequest']['lang'];
  send: Send;
  onPriceTable?: () => void;
}) {
  const t = copy[lang];
  return turns.map(turn => <section data-turn={turn.id} key={turn.id} className="agent-turn">
    {turn.answers?.length ? <dl className="agent-user agent-user-answers">
      {turn.answers.map((item, index) => <div key={index}>
        <dt>{item.question}</dt>
        <dd>{item.answer}</dd>
      </div>)}
    </dl> : <p className="agent-user">{turn.message}</p>}
    {turn.reply ? <Reply lang={lang} reply={turn.reply} send={send} onPriceTable={onPriceTable} /> : <>
      {turn.streamed?.trim() && <div className="agent-reply agent-streaming" aria-busy={!failed(turn.status)}><AnswerText text={turn.streamed} /></div>}
      <div className="agent-status">
        {!(turn.streamed?.trim() && turn.status === 'writing') && <small role="status">{turn.status && t[turn.status]} {turn.httpStatus && '(' + turn.httpStatus + ')'}</small>}
        {failed(turn.status) && <PriceTableLink lang={lang} onClick={onPriceTable} />}
      </div>
    </>}
  </section>);
}
