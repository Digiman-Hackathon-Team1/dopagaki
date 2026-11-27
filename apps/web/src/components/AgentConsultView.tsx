import { useEffect, useRef, useState, type FormEvent } from 'react';
import { RotateCcw, Send as SendIcon, ShieldCheck, Sparkles } from 'lucide-react';
import dopagakiMarkUrl from '@/assets/dopagaki-mark.png';
import type { Lang } from '@/types.ts';
import { copy } from '@/components/agent/copy';
import type { Send } from '@/components/agent/Questions';
import { Turns } from '@/components/agent/Turns';
import { failed, type AgentChat, type Turn } from '@/components/agent/useAgentChat';
import '@/components/agent/agent.css';

type Phase = 'pending' | 'streaming' | 'settled';
const phaseOf = (turn: Turn): Phase =>
  turn.reply || failed(turn.status) ? 'settled' : turn.streamed?.trim() ? 'streaming' : 'pending';

/**
 * AI 상담 탭. 에이전트 대화(POST /api/agent/chat/stream) 하나로 상담한다(설계 문서 1장, ADR 0002, #44).
 * 답변 글은 만들어지는 대로 보여 주고, 에이전트가 답하지 못하면 시세표로 안내한다(FR-08).
 */
export function AgentConsultView({ lang, chat, onOpenPriceTable, onHaptic }: {
  lang: Lang;
  /** App이 가진 대화. 다른 탭에 다녀와도 대화가 남는다. */
  chat: AgentChat;
  onOpenPriceTable: () => void;
  onHaptic: () => void;
}) {
  const t = copy[lang];
  const [draft, setDraft] = useState('');
  const tooLong = [...draft.trim()].length > 2000;
  const log = useRef<HTMLDivElement>(null);
  // 턴마다 마지막으로 본 단계. 처음 그릴 때는 이미 있는 대화로 스크롤하지 않는다.
  const seen = useRef<Map<number, Phase>>(null);
  seen.current ??= new Map(chat.turns.map(turn => [turn.id, phaseOf(turn)]));

  // 새로 보낸 말은 그 턴으로, 답이 처음 보이면(글 또는 최종 응답·실패 안내) 그 위치로 스크롤한다.
  // 글을 이어 받던 답이 최종 응답으로 바뀔 때는 읽던 자리를 지키도록 움직이지 않는다.
  useEffect(() => {
    const known = seen.current!;
    let target: Element | null | undefined;
    for (const turn of chat.turns) {
      const phase = phaseOf(turn);
      const before = known.get(turn.id);
      known.set(turn.id, phase);
      if (before === phase || (before === 'streaming' && phase === 'settled')) continue;
      const el = log.current?.querySelector('[data-turn="' + turn.id + '"]');
      target = phase === 'pending' ? el : el?.querySelector('.agent-reply, .agent-status') ?? el;
    }
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [chat.turns]);

  const send: Send = (value, answers) => {
    if (chat.send(value, answers) !== undefined) onHaptic();
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
