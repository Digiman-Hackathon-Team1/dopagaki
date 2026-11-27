import { useEffect, useRef, useState } from 'react';
import type { Schemas } from '@repo/api-client';
import mark from '@/assets/dopagaki-mark.png';
import { SvgIcon } from '@/components/SvgIcon';
import { Turns } from '@/components/agent/Turns';
import type { Send } from '@/components/agent/Questions';
import { useAgentChat } from '@/components/agent/useAgentChat';
import { readSiteLanguage, saveSiteLanguage } from '@/lib/site-language';
import { copy } from '@/components/agent/copy';
import './agent.css';

export default function AgentDev() {
  const [draft, setDraft] = useState('');
  const [lang, setLang] = useState<Schemas['ChatRequest']['lang']>(() => readSiteLanguage(new URLSearchParams(location.search).get('lang')));
  const t = copy[lang];
  const tooLong = [...draft.trim()].length > 2000;
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const log = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const scrollTarget = useRef<number>(undefined);
  // 글을 이어 받은 답은 이미 보이고 있으므로 최종 응답으로 바뀔 때 다시 스크롤하지 않는다.
  const chat = useAgentChat(lang, turn => { if (!turn.streamed) scrollTarget.current = turn.id; });
  useEffect(() => {
    if (scrollTarget.current !== undefined && follow.current) {
      const turn = log.current?.querySelector('[data-turn="' + scrollTarget.current + '"]');
      const reply = turn?.querySelector('.agent-reply');
      if (reply) reply.scrollIntoView({ block: 'start' });
      else if (log.current) log.current.scrollTop = log.current.scrollHeight;
    }
    scrollTarget.current = undefined;
  }, [chat.turns]);

  const send: Send = (value, answers) => {
    const id = chat.send(value, answers);
    if (id === undefined) return;
    follow.current = true;
    scrollTarget.current = id;
  };

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
      {!chat.turns.length && <section className="agent-welcome"><img src={mark} alt="" /><h2>{t.welcome}</h2>
        <p>{t.intro}</p>
        <small>{t.progress}</small>
        <div className="agent-examples">
          <button onClick={() => send(t.prompt)}>{t.example} <SvgIcon name="chevronRight" /></button>
          <button onClick={() => send(t.locationPrompt)}>{t.locationExample} <SvgIcon name="chevronRight" /></button>
          <button onClick={() => send(t.jpPrompt)}>{t.jpExample} <SvgIcon name="chevronRight" /></button>
          <button onClick={() => send(t.safetyPrompt)}>{t.safetyExample} <SvgIcon name="chevronRight" /></button>
          <button onClick={() => send(t.negationPrompt)}>{t.negationExample} <SvgIcon name="chevronRight" /></button>
        </div>
      </section>}
      <Turns turns={chat.turns} lang={lang} send={send} />
    </div>
    <form className="agent-composer" onSubmit={e => { e.preventDefault(); if (!tooLong && draft.trim()) { send(draft); setDraft(''); } }}>
      <input aria-label={t.message} placeholder={t.placeholder} aria-invalid={tooLong} value={draft} onChange={e => setDraft(e.target.value)} />
      <button disabled={!draft.trim() || tooLong}>{t.send}</button>
      {tooLong && <small role="alert">{t.tooLong}</small>}
    </form>
  </main>;
}
