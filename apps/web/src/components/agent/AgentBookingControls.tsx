import { useEffect, useId, useMemo, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { AnimatePresence, motion } from 'motion/react';
import { Calendar, Check, ChevronLeft, ChevronRight, Copy } from 'lucide-react';

export type MessageLang = 'ko' | 'ja';
export type MessagePurpose = 'inquiry' | 'order';
export type BookingTimeSlot = 'any' | 'morning' | 'afternoon' | 'evening';

interface BilingualLine {
  primary: string;
  gloss: string;
  spacer: boolean;
}

export interface InquiryQuickFill {
  guestName: string;
  dateKo: string;
  dateJa: string;
  timeSlot: BookingTimeSlot;
  people: number | null;
}

const WEEKDAYS_KO = ['일', '월', '화', '수', '목', '금', '토'];
const WEEKDAYS_JA = ['日', '月', '火', '水', '木', '金', '土'];

const TIME_SHORT: Record<BookingTimeSlot, { ko: string; ja: string }> = {
  any: { ko: '무관', ja: 'おまかせ' },
  morning: { ko: '오전', ja: '午前' },
  afternoon: { ko: '오후', ja: '午後' },
  evening: { ko: '저녁', ja: '夕方' },
};

const SLOT_TEXT: Record<BookingTimeSlot, { ko: string; ja: string }> = {
  any: { ko: '시간 무관', ja: '時間帯おまかせ' },
  morning: { ko: '오전 (10:00~12:00)', ja: '午前（10:00〜12:00）' },
  afternoon: { ko: '오후 (13:00~17:00)', ja: '午後（13:00〜17:00）' },
  evening: { ko: '저녁 (17:00 이후)', ja: '夕方（17:00以降）' },
};

export function formatBookingDate(date: Date, lang: MessageLang): string {
  const m = date.getMonth() + 1;
  const d = date.getDate();
  const w = lang === 'ja' ? WEEKDAYS_JA[date.getDay()] : WEEKDAYS_KO[date.getDay()];
  return `${m}/${d}(${w})`;
}

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function addDays(base: Date, days: number): Date {
  return new Date(base.getFullYear(), base.getMonth(), base.getDate() + days);
}

function isSameDay(a: Date | undefined, b: Date): boolean {
  return Boolean(
    a &&
      a.getFullYear() === b.getFullYear() &&
      a.getMonth() === b.getMonth() &&
      a.getDate() === b.getDate()
  );
}

function buildMonthGrid(monthStart: Date): Date[] {
  const year = monthStart.getFullYear();
  const month = monthStart.getMonth();
  const firstDay = new Date(year, month, 1);
  const startOffset = firstDay.getDay();
  const firstCell = new Date(year, month, 1 - startOffset);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const totalCells = startOffset + daysInMonth > 35 ? 42 : 35;
  const cells: Date[] = [];
  for (let i = 0; i < totalCells; i += 1) {
    cells.push(addDays(firstCell, i));
  }
  return cells;
}

function resolveDefaultSendLang(
  uiLang: MessageLang,
  japaneseSupport: boolean,
  purpose: MessagePurpose
): MessageLang {
  if (purpose === 'inquiry' && uiLang === 'ja' && japaneseSupport) {
    return 'ja';
  }
  return 'ko';
}

function buildInterlinearLines(primaryText: string, glossText: string): BilingualLine[] {
  const primaryLines = primaryText.split('\n');
  const glossLines = glossText.split('\n');
  const total = Math.max(primaryLines.length, glossLines.length);
  const rows: BilingualLine[] = [];
  for (let i = 0; i < total; i += 1) {
    const primary = (primaryLines[i] ?? '').trimEnd();
    const gloss = (glossLines[i] ?? '').trimEnd();
    if (!primary.trim() && !gloss.trim()) {
      rows.push({ primary: '', gloss: '', spacer: true });
    } else {
      rows.push({ primary, gloss, spacer: false });
    }
  }
  return rows;
}

/**
 * AI 응답의 예약 문의 문장에 여권 영문명·희망 일시·인원을 즉시 반영한다.
 * 백엔드 `api/domain/inquiry.py`의 고정 라인 접두사와 1:1 대응한다.
 */
export function applyInquiryQuickFill(
  ko: string,
  ja: string,
  fill: InquiryQuickFill
): { ko: string; ja: string } {
  const name = fill.guestName.trim();
  const dateKo = fill.dateKo.trim();
  const dateJa = fill.dateJa.trim();
  const people =
    fill.people !== null && Number.isFinite(fill.people)
      ? Math.max(1, Math.min(5, Math.round(fill.people)))
      : null;

  let nextKo = ko;
  let nextJa = ja;

  if (name) {
    nextKo = nextKo.replace(/^(•\s*성함\(여권 영문\)\s*:\s*).*$/m, `$1${name}`);
    nextJa = nextJa.replace(/^(・\s*お名前（パスポート表記）\s*：\s*).*$/m, `$1${name}`);
  }
  if (dateKo || dateJa || fill.timeSlot !== 'any') {
    const slot = SLOT_TEXT[fill.timeSlot];
    const dKo = dateKo || '(희망 날짜 미입력)';
    const dJa = dateJa || '（希望日未入力）';
    nextKo = nextKo.replace(/^(•\s*희망 일시\s*:\s*).*$/m, `$1${dKo} / ${slot.ko}`);
    nextJa = nextJa.replace(/^(・\s*ご希望日時\s*：\s*).*$/m, `$1${dJa} / ${slot.ja}`);
  }
  if (people !== null) {
    nextKo = nextKo.replace(/^(•\s*인원\s*:\s*).*$/m, `$1${people}명`);
    nextJa = nextJa.replace(/^(・\s*人数\s*：\s*).*$/m, `$1${people}名`);
  }

  return { ko: nextKo, ja: nextJa };
}

export function AgentDateTimePicker({
  lang,
  selectedDate,
  timeSlot,
  onSelectDate,
  onSelectTimeSlot,
}: {
  lang: MessageLang;
  selectedDate: Date | undefined;
  timeSlot: BookingTimeSlot;
  onSelectDate: (date: Date | undefined) => void;
  onSelectTimeSlot: (slot: BookingTimeSlot) => void;
}) {
  const isJa = lang === 'ja';
  const [open, setOpen] = useState(false);
  const today = useMemo(() => startOfToday(), []);
  const [month, setMonth] = useState<Date>(
    () => new Date((selectedDate ?? today).getFullYear(), (selectedDate ?? today).getMonth(), 1)
  );

  const quickDates = useMemo(
    () => [
      { label: isJa ? '今日' : '오늘', date: today },
      { label: isJa ? '明日' : '내일', date: addDays(today, 1) },
      { label: isJa ? '明後日' : '모레', date: addDays(today, 2) },
    ],
    [isJa, today]
  );

  const cells = useMemo(() => buildMonthGrid(month), [month]);
  const weekdays = isJa ? WEEKDAYS_JA : WEEKDAYS_KO;
  const caption = isJa
    ? `${month.getFullYear()}年 ${month.getMonth() + 1}月`
    : `${month.getFullYear()}년 ${month.getMonth() + 1}월`;

  const displayLabel = selectedDate
    ? `${formatBookingDate(selectedDate, lang)}${timeSlot !== 'any' ? ` ${TIME_SHORT[timeSlot][lang]}` : ''}`
    : timeSlot !== 'any'
      ? TIME_SHORT[timeSlot][lang]
      : '';

  const pickDate = (next: Date | undefined) => {
    onSelectDate(next);
    if (next) setMonth(new Date(next.getFullYear(), next.getMonth(), 1));
  };

  const shiftMonth = (delta: number) => {
    setMonth(prev => new Date(prev.getFullYear(), prev.getMonth() + delta, 1));
  };

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          className={`dopa-agent-qf-date-trigger${displayLabel ? ' has-val' : ''}`}
          aria-label={isJa ? '希望日時をカレンダーで選ぶ' : '희망 일시 달력으로 선택'}
        >
          <span className="dopa-agent-qf-date-val">
            {displayLabel || (isJa ? '日付を選ぶ' : '날짜 선택')}
          </span>
          <Calendar size={12} strokeWidth={2.2} className="dopa-agent-qf-date-ico" aria-hidden="true" />
        </button>
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content
          side="bottom"
          align="center"
          sideOffset={8}
          collisionPadding={12}
          className="dopa-agent-cal-popover"
          onOpenAutoFocus={e => e.preventDefault()}
        >
          <AnimatePresence>
            {open && (
              <motion.div
                className="dopa-agent-cal-card"
                initial={{ opacity: 0, y: -6, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -4, scale: 0.97 }}
                transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
              >
                <div className="dopa-agent-cal-quick">
                  {quickDates.map(q => {
                    const active = isSameDay(selectedDate, q.date);
                    return (
                      <button
                        key={q.label}
                        type="button"
                        className={`dopa-agent-cal-quick-btn${active ? ' is-on' : ''}`}
                        onClick={() => pickDate(q.date)}
                      >
                        <span>{q.label}</span>
                        <small className="num">
                          {q.date.getMonth() + 1}/{q.date.getDate()}
                        </small>
                      </button>
                    );
                  })}
                </div>

                <div className="dopa-agent-cal-month">
                  <div className="dopa-agent-cal-nav">
                    <button
                      type="button"
                      className="dopa-agent-cal-nav-btn"
                      aria-label={isJa ? '前の月' : '이전 달'}
                      onClick={() => shiftMonth(-1)}
                    >
                      <ChevronLeft size={15} strokeWidth={2.3} />
                    </button>
                    <span className="dopa-agent-cal-caption">{caption}</span>
                    <button
                      type="button"
                      className="dopa-agent-cal-nav-btn"
                      aria-label={isJa ? '次の月' : '다음 달'}
                      onClick={() => shiftMonth(1)}
                    >
                      <ChevronRight size={15} strokeWidth={2.3} />
                    </button>
                  </div>

                  <div className="dopa-agent-cal-weekdays" aria-hidden="true">
                    {weekdays.map((day, idx) => (
                      <span
                        key={day}
                        className={`dopa-agent-cal-weekday${idx === 0 ? ' is-sun' : ''}`}
                      >
                        {day}
                      </span>
                    ))}
                  </div>

                  <div className="dopa-agent-cal-days">
                    {cells.map(cell => {
                      const isOutside = cell.getMonth() !== month.getMonth();
                      const isDisabled = cell < today;
                      const isToday = isSameDay(today, cell);
                      const isSelected = isSameDay(selectedDate, cell);
                      return (
                        <button
                          key={cell.toISOString()}
                          type="button"
                          disabled={isDisabled}
                          aria-pressed={isSelected}
                          aria-label={formatBookingDate(cell, lang)}
                          className={[
                            'dopa-agent-cal-day',
                            isOutside ? 'is-outside' : '',
                            isToday ? 'is-today' : '',
                            isSelected ? 'is-selected' : '',
                          ]
                            .filter(Boolean)
                            .join(' ')}
                          onClick={() => pickDate(cell)}
                        >
                          {cell.getDate()}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="dopa-agent-cal-times">
                  <span className="dopa-agent-cal-times-lbl">
                    {isJa ? '希望時間帯' : '희망 시간대'}
                  </span>
                  <div className="dopa-agent-cal-times-grid">
                    {(['any', 'morning', 'afternoon', 'evening'] as BookingTimeSlot[]).map(slot => (
                      <button
                        key={slot}
                        type="button"
                        className={`dopa-agent-cal-time-btn${timeSlot === slot ? ' is-on' : ''}`}
                        onClick={() => onSelectTimeSlot(slot)}
                      >
                        {TIME_SHORT[slot][lang]}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="dopa-agent-cal-foot">
                  <button
                    type="button"
                    className="dopa-agent-cal-clear"
                    onClick={() => {
                      onSelectDate(undefined);
                      onSelectTimeSlot('any');
                    }}
                  >
                    {isJa ? 'クリア' : '초기화'}
                  </button>
                  <button
                    type="button"
                    className="dopa-agent-cal-done"
                    onClick={() => setOpen(false)}
                  >
                    {isJa ? '決定' : '선택 완료'}
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function AgentBilingualMessageBox({
  ko,
  ja,
  uiLang,
  japaneseSupport,
  purpose,
  allowLangSwitch = purpose === 'inquiry',
}: {
  ko: string;
  ja: string;
  uiLang: MessageLang;
  japaneseSupport: boolean;
  purpose: MessagePurpose;
  allowLangSwitch?: boolean;
}) {
  const switchId = useId();
  const isJaUi = uiLang === 'ja';
  const defaultSend = resolveDefaultSendLang(uiLang, japaneseSupport, purpose);
  const [sendLang, setSendLang] = useState<MessageLang>(defaultSend);
  const [showGloss, setShowGloss] = useState<boolean>(isJaUi && defaultSend === 'ko');
  const [copyStatus, setCopyStatus] = useState<'copied' | 'failed' | null>(null);

  useEffect(() => {
    const nextDefault = resolveDefaultSendLang(uiLang, japaneseSupport, purpose);
    setSendLang(nextDefault);
    setShowGloss(uiLang === 'ja' && nextDefault === 'ko');
  }, [uiLang, japaneseSupport, purpose]);

  const primaryText = sendLang === 'ja' ? ja : ko;
  const glossText = sendLang === 'ja' ? ko : ja;
  const glossLang: MessageLang = sendLang === 'ja' ? 'ko' : 'ja';

  const lines = useMemo(
    () => buildInterlinearLines(primaryText, glossText),
    [primaryText, glossText]
  );

  const statusText = (() => {
    if (purpose === 'order') {
      return isJaUi ? '受付提示用 · 韓国語' : '접수대 제시용 · 한국어';
    }
    if (japaneseSupport) {
      return sendLang === 'ja'
        ? isJaUi
          ? '日本語そのまま送信OK'
          : '일본어 그대로 전송 가능'
        : isJaUi
          ? '韓国語バージョン表示中'
          : '한국어 버전 선택됨';
    }
    return isJaUi ? '韓国語送信推奨 · 翻訳配慮文入り' : '한국어 권장 · 번역 배려 문구 포함';
  })();

  const copyLabel = (() => {
    if (copyStatus === 'copied') {
      return isJaUi ? 'コピーしました' : '복사 완료';
    }
    if (sendLang === 'ja') {
      return isJaUi ? '日本語メッセージをコピー' : '일본어 문구 복사';
    }
    return isJaUi
      ? showGloss
        ? '韓国語だけコピー'
        : '韓国語メッセージをコピー'
      : showGloss
        ? '한국어만 복사'
        : '한국어 문구 복사';
  })();

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(primaryText);
      setCopyStatus('copied');
      window.setTimeout(() => setCopyStatus(null), 1800);
    } catch {
      setCopyStatus('failed');
    }
  };

  return (
    <div className="dopa-agent-bmsg">
      <div className="dopa-agent-bmsg-bar">
        <span className="dopa-agent-bmsg-status">
          <span
            className={`dopa-agent-bmsg-dot${japaneseSupport && purpose === 'inquiry' ? ' is-jp' : ''}`}
            aria-hidden="true"
          />
          <span>{statusText}</span>
        </span>

        <div className="dopa-agent-bmsg-controls">
          {allowLangSwitch && (
            <div
              className="dopa-agent-bmsg-lang"
              role="group"
              aria-label={isJaUi ? '送信言語を選択' : '전송 언어 선택'}
            >
              <button
                type="button"
                className={sendLang === 'ja' ? 'is-on' : ''}
                aria-pressed={sendLang === 'ja'}
                onClick={() => {
                  setSendLang('ja');
                  setShowGloss(false);
                }}
              >
                {isJaUi ? '日本語' : '일본어'}
              </button>
              <span className="dopa-agent-bmsg-sep" aria-hidden="true">
                /
              </span>
              <button
                type="button"
                className={sendLang === 'ko' ? 'is-on' : ''}
                aria-pressed={sendLang === 'ko'}
                onClick={() => {
                  setSendLang('ko');
                  if (isJaUi) setShowGloss(true);
                }}
              >
                {isJaUi ? '韓国語' : '한국어'}
              </button>
            </div>
          )}

          {sendLang === 'ko' && (
            <div className="dopa-agent-bmsg-switch-wrap">
              <label htmlFor={switchId} className="dopa-agent-bmsg-switch-lbl">
                {isJaUi ? '日本語訳' : '일본어 뜻'}
              </label>
              <button
                id={switchId}
                type="button"
                role="switch"
                aria-checked={showGloss}
                className="dopa-agent-switch"
                onClick={() => setShowGloss(prev => !prev)}
              >
                <span className="dopa-agent-switch-thumb" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="dopa-agent-bmsg-body" lang={sendLang}>
        {showGloss ? (
          <div className="dopa-agent-bmsg-interlinear">
            {lines.map((line, i) =>
              line.spacer ? (
                <div key={i} className="dopa-agent-bmsg-spacer" aria-hidden="true" />
              ) : (
                <div key={i} className="dopa-agent-bmsg-line">
                  <p className="dopa-agent-bmsg-primary">{line.primary}</p>
                  {line.gloss && line.gloss !== line.primary && (
                    <p className="dopa-agent-bmsg-gloss" lang={glossLang}>
                      {line.gloss}
                    </p>
                  )}
                </div>
              )
            )}
          </div>
        ) : (
          <pre className="dopa-agent-bmsg-pre" lang={sendLang}>
            {primaryText}
          </pre>
        )}
      </div>

      <div className="dopa-agent-bmsg-foot">
        <button
          type="button"
          className="dopa-agent-bmsg-copy-btn"
          onClick={() => void handleCopy()}
        >
          {copyStatus === 'copied' ? (
            <Check size={14} strokeWidth={2.5} aria-hidden="true" />
          ) : (
            <Copy size={13} strokeWidth={2.2} aria-hidden="true" />
          )}
          <span>{copyLabel}</span>
        </button>

        {showGloss && sendLang === 'ko' && (
          <p className="dopa-agent-bmsg-hint">
            {isJaUi
              ? '日本語訳を除いて、韓国語本文のみコピーされます'
              : '일본어 뜻은 제외하고 한국어 본문만 깔끔하게 복사됩니다'}
          </p>
        )}
        {copyStatus === 'failed' && (
          <small role="status" className="dopa-agent-bmsg-hint">
            {isJaUi ? '上の文章を選択してコピーしてください' : '위 문장을 직접 선택해 복사해 주세요'}
          </small>
        )}
      </div>
    </div>
  );
}
