import { useEffect, useRef, useState } from 'react';
import { api, type Schemas } from '@repo/api-client';
import mark from '@/assets/dopagaki-mark.png';
import { SvgIcon } from '@/components/SvgIcon';
import { Reply, type QuestionAnswer } from './Reply';
import { readSiteLanguage, saveSiteLanguage } from '@/lib/site-language';
import { copy } from './copy';
import './agent.css';

type Turn = {
  id: number;
  message: string;
  answers?: QuestionAnswer[];
  status: 'waiting' | 'writing' | 'unavailable' | 'invalid' | 'offline' | 'serverError' | null;
  httpStatus?: number;
  reply?: Schemas['ChatResponse'];
};

function anonymousId() {
  const key = 'dopagaki.agent-dev.userId';
  let saved: string | null = null;
  try { saved = localStorage.getItem(key); } catch { /* Storage may be disabled. */ }
  if (saved && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(saved)) return saved;
  // getRandomValues also works on a phone using the development server over LAN HTTP.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  const id = [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  try { localStorage.setItem(key, id); } catch { /* Keep the ID in memory. */ }
  return id;
}

export default function AgentDev() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [lang, setLang] = useState<Schemas['ChatRequest']['lang']>(() => readSiteLanguage(new URLSearchParams(location.search).get('lang')));
  const t = copy[lang];
  const tooLong = [...draft.trim()].length > 2000;
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const userId = useRef<string>(undefined);
  const lifetime = useRef<AbortController>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  const chain = useRef(Promise.resolve());
  const thread = useRef<string>(undefined);
  const sequence = useRef(0);
  const log = useRef<HTMLDivElement>(null);
  const follow = useRef(true);

  const scrollTarget = useRef<number>(undefined);
  useEffect(() => {
    if (scrollTarget.current !== undefined && follow.current) {
      const turn = log.current?.querySelector('[data-turn="' + scrollTarget.current + '"]');
      const reply = turn?.querySelector('.agent-reply');
      if (reply) reply.scrollIntoView({ block: 'start' });
      else if (log.current) log.current.scrollTop = log.current.scrollHeight;
    }
    scrollTarget.current = undefined;
  }, [turns]);

  function send(value: string, answers?: QuestionAnswer[]) {
    const message = value.trim();
    if (!message || [...message].length > 2000) return;
    const id = ++sequence.current;
    follow.current = true;
    scrollTarget.current = id;
    setTurns(previous => [...previous, { id, message, answers, status: 'waiting' }]);
    function update(changes: Partial<Turn>) {
      setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, ...changes } : turn));
    }
    const signal = lifetime.current?.signal;
    chain.current = chain.current.then(async () => {
      if (signal?.aborted) return;
      update({ status: 'writing' });
      try {
        userId.current ??= anonymousId();
        const { data, response } = await api.POST('/api/agent/chat', {
          signal,
          body: { userId: userId.current, threadId: thread.current, message, lang }
        });
        if (!data) {
          update({ status: response.status === 503 ? 'unavailable' : response.status >= 500 ? 'serverError' : 'invalid', httpStatus: response.status });
          return;
        }
        thread.current = data.threadId;
        scrollTarget.current = id;
        update({ reply: data, status: null });
      } catch {
        update({ status: 'offline' });
      }
    });
  }

  return <main className="agent-dev">
    <header><img src={mark} alt="" /><h1>{t.title}</h1><small>{t.dev}</small>
      <select aria-label={t.language} value={lang} onChange={e => {
        const next = e.target.value === 'ko' ? 'ko' : 'ja';
        setLang(next);
        saveSiteLanguage(next);
        const url = new URL(location.href);
        if (url.searchParams.has('lang')) {
          url.searchParams.set('lang', next);
          history.replaceState(history.state, '', url);
        }
      }}>
        <option value="ko">한국어</option><option value="ja">日本語</option>
      </select>
    </header>
    <div className="agent-log" ref={log} onScroll={e => {
      const el = e.currentTarget;
      follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }}>
      {!turns.length && <section className="agent-welcome"><img src={mark} alt="" /><h2>{t.welcome}</h2>
        <p>{t.intro}</p>
        <button onClick={() => send(t.prompt)}>{t.example} <SvgIcon name="chevronRight" /></button>
      </section>}
      {turns.map(turn => <section data-turn={turn.id} key={turn.id} className="agent-turn">
        {turn.answers?.length ? <dl className="agent-user agent-user-answers">
          {turn.answers.map((item, index) => <div key={index}>
            <dt>{item.question}</dt>
            <dd>{item.answer}</dd>
          </div>)}
        </dl> : <p className="agent-user">{turn.message}</p>}
        {turn.reply ? <Reply lang={lang} reply={turn.reply} send={send} /> : <small role="status">{turn.status && t[turn.status]} {turn.httpStatus && '(' + turn.httpStatus + ')'}</small>}
      </section>)}
    </div>
    <form className="agent-composer" onSubmit={e => { e.preventDefault(); if (!tooLong && draft.trim()) { send(draft); setDraft(''); } }}>
      <input aria-label={t.message} placeholder={t.placeholder} aria-invalid={tooLong} value={draft} onChange={e => setDraft(e.target.value)} />
      <button disabled={!draft.trim() || tooLong}>{t.send}</button>
      {tooLong && <small role="alert">{t.tooLong}</small>}
    </form>
  </main>;
}
