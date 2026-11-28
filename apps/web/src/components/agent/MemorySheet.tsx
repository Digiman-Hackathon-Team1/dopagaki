import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { api, type Schemas } from '@repo/api-client';
import { SheetClose, SheetTitle, VaulSheet } from '@/components/ui/vaul-sheet';
import type { Lang } from '@/types.ts';
import { copy } from './copy';
import { anonymousId } from './user-id';

type Profile = Schemas['UserProfileResponse'];
type Phase = 'loading' | 'ready' | 'error' | 'deleted';

const CONCERNS: Record<Profile['concerns'][number], { ko: string; ja: string }> = {
  pores: { ko: '모공·여드름 흉터', ja: '毛穴・ニキビ跡' },
  glow: { ko: '윤기·칙칙함', ja: 'ツヤ・くすみ' },
  lift: { ko: '처짐·턱선', ja: 'たるみ・フェイスライン' },
  dry: { ko: '건조·잔주름', ja: '乾燥・小じわ' },
  jaw: { ko: '사각턱·이갈이', ja: 'エラ・食いしばり' },
  skinbase: { ko: '피부 밀도·피부결', ja: '肌育・肌質改善' },
};

function day(value: string, lang: Lang) {
  const [, month, date] = value.split('-').map(Number);
  return lang === 'ja' ? `${month}月${date}日` : `${month}월 ${date}일`;
}

function rows(profile: Profile, lang: Lang): [string, string][] {
  const t = copy[lang].memory;
  const list: [string, string][] = [];
  if (profile.visitor) list.push([t.visitor, profile.visitor === 'traveler' ? t.traveler : t.resident]);
  if (profile.budget) {
    const { amount, currency, scope } = profile.budget;
    const unit = currency === 'JPY' ? t.yen : t.won;
    const range = scope === 'per_treatment' ? ` (${t.perTreatment})` : scope === 'total' ? ` (${t.total})` : '';
    list.push([t.budget, `${amount.toLocaleString()}${unit}${range}`]);
  }
  if (profile.arrivalDate) list.push([t.arrival, day(profile.arrivalDate, lang)]);
  if (profile.returnDate) list.push([t.return, day(profile.returnDate, lang)]);
  if (profile.location) list.push([t.location, lang === 'ja' ? profile.location.nameJa : profile.location.nameKo]);
  if (profile.concerns.length) list.push([t.concerns, profile.concerns.map(c => CONCERNS[c][lang]).join(', ')]);
  if (profile.avoidNeedle !== null) list.push([t.needle, profile.avoidNeedle ? t.avoidNeedle : t.needleOk]);
  if (profile.jpTreatments.length) list.push([t.jpTreatments, profile.jpTreatments.join(', ')]);
  return list;
}

/**
 * 기억한 조건 보기·지우기(설계 문서 8장 사용자 기억 관리, 10장, ADR 0019). 시트를 열 때 서버에서 읽는다.
 * 지운 뒤 대화가 있으면 새 상담을 시작할지 묻는다. 지금 대화 기록에는 조건이 남아 있기 때문이다.
 */
export function MemorySheet({ open, onOpenChange, lang, hasTurns, onRestart }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lang: Lang;
  hasTurns: boolean;
  onRestart: () => void;
}) {
  const t = copy[lang].memory;
  const [phase, setPhase] = useState<Phase>('loading');
  const [profile, setProfile] = useState<Profile>();
  const [deleting, setDeleting] = useState(false);
  // 지우기에 실패하면 목록을 그대로 두고 다시 누를 수 있게 한다.
  const [deleteFailed, setDeleteFailed] = useState(false);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setPhase('loading');
    setDeleteFailed(false);
    api
      .GET('/api/agent/users/{userId}/profile', {
        params: { path: { userId: anonymousId() } },
        signal: controller.signal,
      })
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setProfile(data);
        setPhase(data ? 'ready' : 'error');
      })
      .catch(() => {
        if (!controller.signal.aborted) setPhase('error');
      });
    return () => controller.abort();
  }, [open]);

  async function remove() {
    setDeleting(true);
    setDeleteFailed(false);
    try {
      const { response } = await api.DELETE('/api/agent/users/{userId}/profile', {
        params: { path: { userId: anonymousId() } },
      });
      if (!response.ok) throw new Error(String(response.status));
      setProfile(undefined);
      setPhase('deleted');
    } catch {
      setDeleteFailed(true);
    } finally {
      setDeleting(false);
    }
  }

  const items = profile ? rows(profile, lang) : [];

  return (
    <VaulSheet open={open} onOpenChange={onOpenChange} className="agent-question-sheet agent-memory-sheet">
      <div className="agent-question-heading">
        <SheetTitle className="vs-title">{t.title}</SheetTitle>
        <SheetClose className="agent-question-close" aria-label={t.close}>
          <X size={20} />
        </SheetClose>
      </div>
      <div className="vs-body agent-question-options">
        {phase === 'loading' && <p className="agent-memory-note">{t.loading}</p>}
        {phase === 'error' && <p className="agent-memory-note" role="alert">{t.error}</p>}
        {deleteFailed && <p className="agent-memory-note" role="alert">{t.deleteError}</p>}
        {phase === 'deleted' && (
          <p className="agent-memory-note" role="status">{hasTurns ? t.deletedRestart : t.deleted}</p>
        )}
        {phase === 'ready' && items.length === 0 && <p className="agent-memory-note">{t.empty}</p>}
        {phase === 'ready' && items.length > 0 && (
          <dl className="agent-memory-list">
            {items.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
        <small>{t.policy}</small>
      </div>
      <div className="agent-question-footer">
        {phase === 'deleted' && hasTurns ? (
          <>
            <button type="button" className="agent-question-back" onClick={() => onOpenChange(false)}>
              {t.keep}
            </button>
            <button
              type="button"
              className="agent-question-next"
              onClick={() => {
                onRestart();
                onOpenChange(false);
              }}
            >
              {t.restart}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="agent-question-next"
            disabled={phase !== 'ready' || items.length === 0 || deleting}
            onClick={remove}
          >
            {t.deleteAll}
          </button>
        )}
      </div>
    </VaulSheet>
  );
}
