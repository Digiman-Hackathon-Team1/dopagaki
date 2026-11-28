import { useId, useMemo, useState, type ReactNode } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import { motion } from 'motion/react';
import { ArrowUpRight, Bookmark, ChevronDown, MapPin, Sparkles } from 'lucide-react';
import type { Schemas } from '@repo/api-client';
import {
  AgentBilingualMessageBox,
  AgentDateTimePicker,
  applyInquiryQuickFill,
  formatBookingDate,
  type BookingTimeSlot,
} from './AgentBookingControls';
import './dopa-agent-ui.css';

type Lang = Schemas['ChatRequest']['lang'];

export function SaveClinicButton({
  saved,
  clinicName,
  lang,
  onToggle,
}: {
  saved: boolean;
  clinicName: string;
  lang: Lang;
  onToggle: () => void;
}) {
  const ja = lang === 'ja';
  const label = saved
    ? ja
      ? `${clinicName}の保存を解除`
      : `${clinicName} 저장 해제`
    : ja
      ? `${clinicName}を保存`
      : `${clinicName} 저장`;
  return (
    <motion.button
      type="button"
      className={`dopa-agent-save-btn${saved ? ' is-saved' : ''}`}
      whileTap={{ scale: 0.86 }}
      transition={{ duration: 0.12 }}
      aria-pressed={saved}
      aria-label={label}
      title={saved ? (ja ? '保存済み' : '저장됨') : ja ? '保存' : '저장'}
      onClick={e => {
        e.stopPropagation();
        onToggle();
      }}
    >
      <Bookmark
        size={13}
        strokeWidth={2.2}
        fill={saved ? 'currentColor' : 'none'}
        aria-hidden="true"
      />
    </motion.button>
  );
}

export function ExternalLink({
  url,
  children,
  className,
}: {
  url: string;
  children: ReactNode;
  className?: string;
}) {
  if (!/^https?:\/\//i.test(url)) return null;
  return (
    <a href={url} target="_blank" rel="noreferrer" className={className}>
      <span>{children}</span>
      <ArrowUpRight size={13} strokeWidth={2.2} aria-hidden="true" />
    </a>
  );
}

export function Channels({
  channels,
  central,
  lang,
}: {
  channels: Schemas['BookingChannel'][];
  central: boolean;
  lang: Lang;
}) {
  const ja = lang === 'ja';
  if (!channels.length) return null;
  return (
    <div className="dopa-agent-channels">
      <div className="dopa-agent-channel-list">
        {channels.map((channel, i) => (
          <ExternalLink
            key={channel.kind + ':' + i}
            url={ja ? channel.urlJa : channel.urlKo}
            className={`dopa-agent-channel-pill is-${channel.kind}`}
          >
            {ja ? channel.labelJa : channel.labelKo}
          </ExternalLink>
        ))}
      </div>
      {central && (
        <small className="dopa-agent-muted-note">
          {ja
            ? 'ブランド共通窓口を含みます。予約時に支店名をお伝えください。'
            : '브랜드 통합 창구가 포함되어 있어요. 예약 시 지점명을 함께 알려주세요.'}
        </small>
      )}
    </div>
  );
}

export function LocationCard({ card, lang }: { card: Schemas['LocationCard']; lang: Lang }) {
  const ja = lang === 'ja';
  return (
    <span
      className="dopa-ctx-chip is-location"
      title={card.query ? `${card.query} (${card.lat}, ${card.lng})` : `${card.lat}, ${card.lng}`}
    >
      <MapPin size={12} strokeWidth={2.3} aria-hidden="true" />
      <strong>{ja ? card.nameJa : card.nameKo}</strong>
      <span className="dopa-ctx-sub">
        {card.locationType === 'area'
          ? ja
            ? 'エリア基準'
            : '지역 기준'
          : ja
            ? '駅基準'
            : '역 기준'}
      </span>
    </span>
  );
}

export function JpMatchCard({ card, lang }: { card: Schemas['JpMatchCard']; lang: Lang }) {
  const ja = lang === 'ja';
  const clinic = card.jpClinic;
  const menuName = ja ? card.menuNameJa : card.menuNameKo;
  const reason = ja ? card.reasonJa : card.reasonKo;
  const unitNote = ja ? card.unitNoteJa : card.unitNoteKo;

  if (!clinic) {
    return (
      <span className="dopa-ctx-chip is-match" title={`${reason} ${unitNote}`.trim()}>
        <Sparkles size={12} strokeWidth={2.3} aria-hidden="true" />
        <span>
          {card.query} → <strong>{menuName}</strong>
        </span>
      </span>
    );
  }

  return (
    <details className="dopa-ctx-details">
      <summary className="dopa-ctx-chip is-match is-interactive">
        <Sparkles size={12} strokeWidth={2.3} aria-hidden="true" />
        <span>
          {card.query} → <strong>{menuName}</strong>
        </span>
        <ChevronDown size={12} strokeWidth={2.3} className="dopa-agent-chev" aria-hidden="true" />
      </summary>
      <div className="dopa-ctx-popover">
        <p className="dopa-agent-card-desc">{reason}</p>
        {unitNote && <p className="dopa-agent-muted-note">{unitNote}</p>}
        <div className="dopa-agent-comparison">
          <div className="dopa-agent-comp-row">
            <strong>{ja ? clinic.nameJa : clinic.nameKo}</strong>
            <span className="num">¥{clinic.priceJpy.toLocaleString(lang)}</span>
          </div>
          <p className="dopa-agent-muted-note">{ja ? clinic.planJa : clinic.planKo}</p>
          {(ja ? clinic.productNoteJa : clinic.productNoteKo) && (
            <p className="dopa-agent-muted-note">
              {ja ? clinic.productNoteJa : clinic.productNoteKo}
            </p>
          )}
          <ExternalLink url={clinic.sourceUrl} className="dopa-agent-inline-link">
            {ja ? '日本の公式料金' : '일본 공식 가격'}
          </ExternalLink>
        </div>
      </div>
    </details>
  );
}

export function BookingCard({
  card,
  lang,
  saved = false,
  onToggleSave,
}: {
  card: Schemas['BookingCard'];
  lang: Lang;
  saved?: boolean;
  onToggleSave?: () => void;
}) {
  const ja = lang === 'ja';
  const tabLineId = useId();
  const initialPeople = useMemo(() => {
    const match = card.inquiryKo.match(/인원:\s*([1-5])명/);
    return match ? Number(match[1]) : 1;
  }, [card.inquiryKo]);

  const [tab, setTab] = useState<'inquiry' | 'order'>('inquiry');
  const [guestName, setGuestName] = useState('');
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(undefined);
  const [timeSlot, setTimeSlot] = useState<BookingTimeSlot>('any');
  const [people, setPeople] = useState<number>(initialPeople);

  const filledInquiry = useMemo(
    () =>
      applyInquiryQuickFill(card.inquiryKo, card.inquiryJa, {
        guestName,
        dateKo: selectedDate ? formatBookingDate(selectedDate, 'ko') : '',
        dateJa: selectedDate ? formatBookingDate(selectedDate, 'ja') : '',
        timeSlot,
        people,
      }),
    [card.inquiryKo, card.inquiryJa, guestName, selectedDate, timeSlot, people]
  );

  const validChannels = card.channels.filter(channel =>
    /^https?:\/\//i.test(ja ? channel.urlJa : channel.urlKo)
  );
  const hasOfficialSite = /^https?:\/\//i.test(card.officialSiteUrl);

  return (
    <article className="agent-card agent-booking dopa-agent-booking">
      <div className="dopa-agent-book-head">
        <div className="dopa-agent-book-title-row">
          <h3 className="dopa-agent-book-title">{ja ? card.clinicNameJa : card.clinicNameKo}</h3>
          <div className="dopa-agent-card-badges">
            <span className={`dopa-agent-tag-rect${card.japaneseSupport ? ' is-jp' : ' is-ko'}`}>
              {card.japaneseSupport
                ? ja
                  ? '日本語OK'
                  : '일본어 OK'
                : ja
                  ? '韓国語対応'
                  : '한국어 전용'}
            </span>
            {onToggleSave && (
              <SaveClinicButton
                saved={saved}
                clinicName={ja ? card.clinicNameJa : card.clinicNameKo}
                lang={lang}
                onToggle={onToggleSave}
              />
            )}
          </div>
        </div>

        <div className="dopa-agent-book-subrow">
          <span className="dopa-agent-book-caption">
            {ja ? '手数料なし · 公式直接予約・決済' : '수수료 0원 · 공식 직접 예약·결제'}
          </span>
          {card.menuIds.length > 0 && (
            <span className="dopa-agent-book-ledger num">
              <strong>¥{card.orderTotalJpy.toLocaleString(lang)}</strong>
              <small>₩{card.orderTotalKrw.toLocaleString(lang)}</small>
            </span>
          )}
        </div>

        {card.menuIds.length > 0 && (
          <small className="dopa-agent-book-fx num">
            {ja
              ? `付加価値税込目安 · 為替 ${card.fxDateLabel}（¥100 = ₩${card.krwPer100Jpy.toLocaleString(lang)}）`
              : `부가세 포함 예상 합계 · 환율 ${card.fxDateLabel} (¥100 = ₩${card.krwPer100Jpy.toLocaleString(lang)})`}
          </small>
        )}
      </div>

      {validChannels.length > 0 ? (
        <div className="dopa-agent-book-channels">
          <div className="dopa-agent-channel-grid">
            {validChannels.map((channel, i) => (
              <a
                key={channel.kind + ':' + i}
                href={ja ? channel.urlJa : channel.urlKo}
                target="_blank"
                rel="noreferrer"
                className={`dopa-agent-channel-tile is-${channel.kind}`}
              >
                <span className="dopa-agent-ch-lbl">
                  {ja ? channel.labelJa : channel.labelKo}
                </span>
                <ArrowUpRight size={13} strokeWidth={2.2} className="dopa-agent-ch-ico" aria-hidden="true" />
              </a>
            ))}
          </div>
          {card.centralChannel && (
            <small className="dopa-agent-muted-note">
              {ja
                ? 'ブランド共通窓口を含みます。予約時に支店名をお伝えください。'
                : '브랜드 통합 창구가 포함되어 있어요. 예약 시 지점명을 함께 알려주세요.'}
            </small>
          )}
        </div>
      ) : (
        <div className="dopa-agent-book-channels">
          <small className="dopa-agent-muted-note">
            {ja
              ? '登録された予約窓口がありません。公式サイトでご確認ください。'
              : '등록된 예약 채널이 없습니다. 공식 사이트에서 확인해 주세요.'}
          </small>
          {hasOfficialSite && (
            <a
              href={card.officialSiteUrl}
              target="_blank"
              rel="noreferrer"
              className="dopa-agent-inline-link"
            >
              <span>{ja ? '公式サイトへ' : '공식 사이트 바로가기'}</span>
              <ArrowUpRight size={13} strokeWidth={2.2} aria-hidden="true" />
            </a>
          )}
        </div>
      )}

      <Tabs.Root
        value={tab}
        onValueChange={v => setTab(v as 'inquiry' | 'order')}
        className="dopa-agent-ed-tabs"
      >
        <Tabs.List className="dopa-agent-ed-tablist">
          <Tabs.Trigger value="inquiry" className="dopa-agent-ed-tab">
            <span>{ja ? '訪問前 · 予約メッセージ' : '방문 전 · 예약 문의'}</span>
            {tab === 'inquiry' && (
              <motion.span
                layoutId={`bk-line-${tabLineId}`}
                className="dopa-agent-ed-tab-bar"
                transition={{ type: 'spring', stiffness: 520, damping: 38 }}
              />
            )}
          </Tabs.Trigger>
          <Tabs.Trigger value="order" className="dopa-agent-ed-tab">
            <span>{ja ? '当日 · 受付メモ' : '당일 · 접수 메모'}</span>
            {tab === 'order' && (
              <motion.span
                layoutId={`bk-line-${tabLineId}`}
                className="dopa-agent-ed-tab-bar"
                transition={{ type: 'spring', stiffness: 520, damping: 38 }}
              />
            )}
          </Tabs.Trigger>
        </Tabs.List>

        <Tabs.Content value="inquiry" className="dopa-agent-ed-tabpanel">
          <div className="dopa-agent-qf-strip">
            <label className="dopa-agent-qf-cell">
              <span>{ja ? 'お名前（ローマ字）' : '여권 영문명'}</span>
              <input
                type="text"
                value={guestName}
                maxLength={40}
                placeholder="YUKI TANAKA"
                onChange={e => setGuestName(e.target.value)}
              />
            </label>
            <div className="dopa-agent-qf-cell">
              <span>{ja ? '希望日時' : '희망 일시'}</span>
              <AgentDateTimePicker
                lang={lang}
                selectedDate={selectedDate}
                timeSlot={timeSlot}
                onSelectDate={setSelectedDate}
                onSelectTimeSlot={setTimeSlot}
              />
            </div>
            <label className="dopa-agent-qf-cell">
              <span>{ja ? '人数' : '인원'}</span>
              <select value={people} onChange={e => setPeople(Number(e.target.value))}>
                {[1, 2, 3, 4, 5].map(cnt => (
                  <option key={cnt} value={cnt}>
                    {ja ? `${cnt}名` : `${cnt}명`}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <AgentBilingualMessageBox
            ko={filledInquiry.ko}
            ja={filledInquiry.ja}
            uiLang={lang}
            japaneseSupport={card.japaneseSupport}
            purpose="inquiry"
          />
        </Tabs.Content>

        <Tabs.Content value="order" className="dopa-agent-ed-tabpanel">
          <AgentBilingualMessageBox
            ko={card.orderSheetKo}
            ja={card.orderSheetJa}
            uiLang={lang}
            japaneseSupport={card.japaneseSupport}
            purpose="order"
            allowLangSwitch={false}
          />
        </Tabs.Content>
      </Tabs.Root>
    </article>
  );
}
