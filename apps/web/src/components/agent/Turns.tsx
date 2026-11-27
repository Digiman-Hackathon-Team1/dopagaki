import type { Schemas } from '@repo/api-client';
import { Reply } from './Reply';
import { PriceTableLink } from './PriceTableLink';
import type { Send } from './Questions';
import { copy } from './copy';
import { failed, type Turn } from './useAgentChat';

/** 사용자 말과 에이전트 답을 차례로 그린다. 답이 없으면 상태와 시세표 안내를 보여 준다(FR-08). */
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
    {turn.reply ? <Reply lang={lang} reply={turn.reply} send={send} onPriceTable={onPriceTable} /> : <div className="agent-status">
      <small role="status">{turn.status && t[turn.status]} {turn.httpStatus && '(' + turn.httpStatus + ')'}</small>
      {failed(turn.status) && <PriceTableLink lang={lang} onClick={onPriceTable} />}
    </div>}
  </section>);
}
