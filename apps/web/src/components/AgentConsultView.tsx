import { useEffect, useRef, useState, type FormEvent } from 'react';
import { RotateCcw, Send as SendIcon, ShieldCheck, Sparkles } from 'lucide-react';
import dopagakiMarkUrl from '@/assets/dopagaki-mark.png';
import type { Lang } from '@/types.ts';
import { track } from '@/lib/analytics.ts';
import { copy } from '@/components/agent/copy';
import type { Send } from '@/components/agent/Questions';
import { Turns } from '@/components/agent/Turns';
import { useAgentChat } from '@/components/agent/useAgentChat';
import '@/components/agent/agent.css';

/**
 * AI 상담 탭. 에이전트 대화(POST /api/agent/chat) 하나로 상담한다(설계 문서 1장, ADR 0002).
 * 에이전트가 답하지 못하면 시세표로 안내한다(FR-08).
 */
export function AgentConsultView({ lang, onOpenPriceTable, onHaptic }: {
  lang: Lang;
  onOpenPriceTable: () => void;
  onHaptic: () => void;
}) {
  const t = copy[lang];
  const [draft, setDraft] = useState('');
  const tooLong = [...draft.trim()].length > 2000;
  const log = useRef<HTMLDivElement>(null);
  const scrollTarget = useRef<{ id: number; reply: boolean }>(undefined);
  const chat = useAgentChat(lang, turn => {
    scrollTarget.current = { id: turn.id, reply: true };
    track('concierge_agent', {
      cards: turn.reply?.cards.length ?? 0,
      fallback: turn.reply ? turn.reply.fallback ?? 'none' : turn.status
    });
  });

  useEffect(() => {
    const target = scrollTarget.current;
    scrollTarget.current = undefined;
    if (!target) return;
    const turn = log.current?.querySelector('[data-turn="' + target.id + '"]');
    const el = (target.reply && turn?.querySelector('.agent-reply, .agent-status')) || turn;
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [chat.turns]);

  const send: Send = (value, answers) => {
    const id = chat.send(value, answers);
    if (id === undefined) return;
    onHaptic();
    scrollTarget.current = { id, reply: false };
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (tooLong || !draft.trim()) return;
    send(draft);
    setDraft('');
  };

  return (
    <div className="cg-view cg-counselor-view" id="concierge-view">
      <header className="cg-head cg-counselor-head">
        <div className="cg-avatar-wrap">
          <img src={dopagakiMarkUrl} alt={t.counselor} className="cg-head-avatar" width={44} height={44} />
          <span className="cg-online-dot" aria-hidden="true" />
        </div>
        <div className="cg-head-text">
          <div className="cg-head-title-row">
            <h1 className="cg-title">{t.counselor}</h1>
            <span className="cg-live-pill">{t.online}</span>
          </div>
          <p className="cg-sub">
            <ShieldCheck size={12} strokeWidth={2.4} />
            <span>{t.trust}</span>
          </p>
        </div>
        {chat.turns.length > 0 && (
          <div className="cg-head-right">
            <button
              type="button"
              className="cg-reset-icon"
              aria-label={t.restart}
              title={t.restart}
              onClick={() => {
                onHaptic();
                chat.reset();
              }}
            >
              <RotateCcw size={14} strokeWidth={2.3} />
            </button>
          </div>
        )}
      </header>

      <div className="cg-chat" ref={log} aria-live="polite">
        <div className="cg-bot-row">
          <img src={dopagakiMarkUrl} alt="" className="cg-bot-avatar" width={30} height={30} aria-hidden="true" />
          <div className="cg-bot-col">
            <span className="cg-bot-sender">{t.counselor}</span>
            <div className="cg-bot">{t.greeting}</div>
          </div>
        </div>
        {chat.turns.length === 0 && (
          <div className="cg-quick-prompts">
            <span className="cg-quick-label">
              <Sparkles size={12} strokeWidth={2.3} />
              <span>{t.examplesLabel}</span>
            </span>
            <div className="cg-quick-list">
              {t.examples.map(example => (
                <button key={example} type="button" className="cg-quick-btn" onClick={() => send(example)}>
                  “{example}”
                </button>
              ))}
            </div>
          </div>
        )}
        <Turns turns={chat.turns} lang={lang} send={send} onPriceTable={onOpenPriceTable} />
      </div>

      <form className="cg-composer" onSubmit={submit}>
        <div className="cg-composer-row">
          <input
            id="cg-ask-input"
            type="text"
            value={draft}
            onChange={event => setDraft(event.target.value)}
            placeholder={t.consultPlaceholder}
            autoComplete="off"
            enterKeyHint="send"
            aria-label={t.message}
            aria-invalid={tooLong}
          />
          <button type="submit" disabled={!draft.trim() || tooLong} aria-label={t.send}>
            <SendIcon size={17} strokeWidth={2.3} />
          </button>
        </div>
        <p className="cg-ask-note" role={tooLong ? 'alert' : undefined}>{tooLong ? t.tooLong : t.consultNote}</p>
      </form>
    </div>
  );
}
