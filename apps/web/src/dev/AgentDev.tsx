import { useEffect, useRef, useState } from 'react';
import { api, type Schemas } from '@repo/api-client';
import mark from '@/assets/dopagaki-mark.png';
import { SvgIcon } from '@/components/SvgIcon';
import { Reply } from './Reply';
import './agent.css';

type Turn = {
  id: number;
  message: string;
  status: string;
  reply?: Schemas['ChatResponse'];
};

function anonymousId() {
  const key = 'dopagaki.agent-dev.userId';
  const saved = localStorage.getItem(key);
  if (saved && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(saved)) return saved;
  // getRandomValues also works on a phone using the development server over LAN HTTP.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  const id = [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  localStorage.setItem(key, id);
  return id;
}

export default function AgentDev() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [lang, setLang] = useState<Schemas['ChatRequest']['lang']>('ko');
  const chain = useRef(Promise.resolve());
  const thread = useRef<string>(undefined);
  const sequence = useRef(0);
  const log = useRef<HTMLDivElement>(null);
  const follow = useRef(true);

  useEffect(() => {
    if (follow.current && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [turns]);

  function send(value: string) {
    const message = value.trim();
    if (!message || [...message].length > 2000) return;
    const id = ++sequence.current;
    follow.current = true;
    setTurns(previous => [...previous, { id, message, status: '대기 중' }]);
    function update(changes: Partial<Turn>) {
      setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, ...changes } : turn));
    }
    chain.current = chain.current.then(async () => {
      update({ status: '답변 작성 중…' });
      try {
        const { data, response } = await api.POST('/api/agent/chat', {
          body: { userId: anonymousId(), threadId: thread.current, message, lang }
        });
        if (!data) {
          const detail = response.status === 503 ? 'API 설정을 확인해 주세요.' : '요청을 확인해 주세요.';
          throw new Error(detail + ' (' + response.status + ')');
        }
        thread.current = data.threadId;
        update({ reply: data, status: '' });
      } catch (error) {
        update({ status: error instanceof Error ? error.message : '서버에 연결할 수 없습니다.' });
      }
    });
  }

  return <main className="agent-dev">
    <header><img src={mark} alt="" /><h1>도파가키 상담</h1><small>개발용</small>
      <select aria-label="답변 언어" value={lang} onChange={e => setLang(e.target.value === 'ja' ? 'ja' : 'ko')}>
        <option value="ko">한국어</option><option value="ja">日本語</option>
      </select>
    </header>
    <div className="agent-log" ref={log} onScroll={e => {
      const el = e.currentTarget;
      follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }}>
      {!turns.length && <section className="agent-welcome"><img src={mark} alt="" /><h2>어떤 시술을 찾고 계세요?</h2>
        <p>관심 있는 시술과 지역, 예산을 알려 주세요.</p>
        <button onClick={() => send('강남에서 슈링크를 받을 수 있는 병원과 가격을 알려 주세요.')}>강남 슈링크 가격 알아보기 <SvgIcon name="chevronRight" /></button>
      </section>}
      {turns.map(turn => <section key={turn.id} className="agent-turn">
        <p className="agent-user">{turn.message}</p>
        {turn.reply ? <Reply reply={turn.reply} send={send} /> : <small role="status">{turn.status}</small>}
      </section>)}
    </div>
    <form className="agent-composer" onSubmit={e => { e.preventDefault(); send(draft); setDraft(''); }}>
      <input aria-label="메시지" placeholder="궁금한 점을 입력하세요" maxLength={2000} value={draft} onChange={e => setDraft(e.target.value)} />
      <button disabled={!draft.trim()}>보내기</button>
    </form>
  </main>;
}
