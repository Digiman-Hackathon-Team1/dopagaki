import type { Schemas } from '@repo/api-client';
import { DopaThinkingOrb, type DopaOrbPhase } from './DopaThinkingOrb';
import { AnswerText, Reply } from './Reply';
import { PriceTableLink } from './PriceTableLink';
import type { Send } from './Questions';
import { copy } from './copy';
import { failed, type Turn } from './useAgentChat';

/**
 * 사용자 말과 에이전트 답을 차례로 그린다.
 * 대기·스트리밍 중에는 도파 탐색 오브가 움직이고, 최종 응답이 도착하면 도파민 버스트로 전환된다.
 * 답이 없거나 끊기면 상태와 시세표 안내를 보여 준다(FR-08).
 */
export function Turns({
  turns,
  lang,
  send,
  savedIds,
  onToggleSaveClinic,
  onOpenClinic,
  onPriceTable
}: {
  turns: Turn[];
  lang: Schemas['ChatRequest']['lang'];
  send: Send;
  savedIds?: string[];
  onToggleSaveClinic?: (placeId: string, menuIds?: string[]) => void;
  onOpenClinic?: (placeId: string, menuIds: string[]) => void;
  onPriceTable?: () => void;
}) {
  const t = copy[lang];
  const ja = lang === 'ja';

  return turns.map(turn => {
    const hasStream = Boolean(turn.streamed?.trim());
    const isFailed = failed(turn.status);
    const phase: DopaOrbPhase = turn.reply
      ? turn.reply.fallback
        ? 'calm'
        : 'bliss'
      : isFailed || !turn.status
        ? 'calm'
        : hasStream
          ? 'streaming'
          : 'thinking';

    const statusText =
      phase === 'thinking'
        ? turn.status === 'waiting'
          ? (ja ? '前の質問が終わるのを待っています…' : '앞선 질문이 끝나기를 기다리는 중…')
          : (ja ? 'ドパがソウル137院の公式価格を探索中…' : '도파가 서울 137곳 공식가 쏙쏙 찾는 중…')
        : phase === 'streaming'
          ? (ja ? 'ドパがぴったりの提案をまとめ中…' : '도파가 딱 맞는 추천을 정리하는 중…')
          : undefined;

    const doneLabel =
      phase === 'bliss'
        ? (ja ? 'ドパミン放出！最安まとめ完了' : '도파민 팡! 최저가 정리 완료')
        : undefined;

    return (
      <section data-turn={turn.id} key={turn.id} className="agent-turn">
        {turn.answers?.length ? (
          <dl className="agent-user agent-user-answers">
            {turn.answers.map((item, index) => (
              <div key={index}>
                <dt>{item.question}</dt>
                <dd>{item.answer}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="agent-user">{turn.message}</p>
        )}

        <div className="dopa-agent-turn-reply">
          <DopaThinkingOrb
            phase={phase}
            statusText={statusText}
            doneLabel={doneLabel}
            brandName={t.counselor}
            isJa={ja}
          />

          {turn.reply ? (
            <Reply
              lang={lang}
              reply={turn.reply}
              send={send}
              savedIds={savedIds}
              onToggleSaveClinic={onToggleSaveClinic}
              onOpenClinic={onOpenClinic}
              onPriceTable={onPriceTable}
            />
          ) : (
            <>
              {hasStream && (
                <div className="agent-reply agent-streaming" aria-busy={!isFailed}>
                  <AnswerText text={turn.streamed!} />
                </div>
              )}
              {isFailed && (
                <div className="agent-status">
                  <small role="status">
                    {turn.status && t[turn.status]} {turn.httpStatus && '(' + turn.httpStatus + ')'}
                  </small>
                  <PriceTableLink lang={lang} onClick={onPriceTable} />
                </div>
              )}
            </>
          )}
        </div>
      </section>
    );
  });
}
