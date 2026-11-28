import { useMemo, useState } from 'react';
import { motion } from 'motion/react';
import { ChevronDown, Plus, Sparkles, Star } from 'lucide-react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Schemas } from '@repo/api-client';
import { areas, menus } from '@/data/prices.json';
import { isNewClinic } from '@/data/brand-reviews.ts';
import { SvgIcon } from '@/components/SvgIcon';
import { BlurFade } from '@/components/ui/blur-fade.tsx';
import { NumFlow, YenFlow } from '@/components/ui/number-flow.tsx';
import {
  Disclosure,
  DisclosureContent,
  DisclosureTrigger,
} from '@/components/motion-primitives/disclosure.tsx';
import { Questions, type Send } from './Questions';
import {
  BookingCard,
  Channels,
  ExternalLink,
  JpMatchCard,
  LocationCard,
  SaveClinicButton,
} from './Cards';
import { PriceTableLink } from './PriceTableLink';
import { copy } from './copy';
import './agent.css';
import './dopa-agent-ui.css';

type Lang = Schemas['ChatRequest']['lang'];
const INITIAL_VISIBLE_CLINICS = 2;

const exclusions = {
  needle: '바늘 시술 기피',
  downtime: '회복 기간 초과',
  area: '다른 지역',
  japanese: '일본어 미지원',
  budget: '예산 초과',
};

const japaneseExclusions = {
  needle: '針を使う施術',
  downtime: '回復期間超過',
  area: 'エリア外',
  japanese: '日本語非対応',
  budget: '予算超過',
};

const priceLabels = {
  first_visit: '첫 방문',
  event: '이벤트가',
  day_limited: '요일 한정',
  app_benefit: '앱 혜택',
  anesthesia: '마취비 확인',
  extra_fee: '추가 비용',
};

const japaneseTags = {
  first_visit: '初回限定',
  event: 'キャンペーン',
  day_limited: '曜日限定',
  app_benefit: 'アプリ特典',
  anesthesia: '麻酔料要確認',
  extra_fee: '追加料金',
};

const AGENT_MD_COMPONENTS: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  img: ({ alt }) => <>{alt}</>,
  table: ({ children }) => (
    <div className="dopa-agent-table-wrap">
      <table className="dopa-agent-cmp-table">{children}</table>
    </div>
  ),
};

function isTableRowLine(trimmed: string): boolean {
  return trimmed.startsWith('|') && trimmed.includes('|', 1);
}

/**
 * LLM 목록(`• `/`・`)과 GFM 마크다운 표 앞뒤 빈 줄을 정돈해 비교표·요약이 깨지지 않게 그린다.
 */
export function normalizeAgentMarkdown(raw: string): string {
  const lines = raw.replace(/\r\n/g, '\n').split('\n');
  const out: string[] = [];
  let prevKind: 'empty' | 'bullet' | 'table' | 'text' = 'empty';

  for (let i = 0; i < lines.length; i += 1) {
    const trimmed = lines[i].trim();
    if (!trimmed) {
      const nextNonEmpty = lines.slice(i + 1).find(l => l.trim().length > 0)?.trim() ?? '';
      if (prevKind === 'table' && isTableRowLine(nextNonEmpty)) {
        continue;
      }
      if (out.length > 0 && out[out.length - 1] !== '') {
        out.push('');
      }
      prevKind = 'empty';
      continue;
    }

    if (isTableRowLine(trimmed)) {
      if (prevKind !== 'empty' && prevKind !== 'table' && out.length > 0 && out[out.length - 1] !== '') {
        out.push('');
      }
      out.push(trimmed);
      prevKind = 'table';
      continue;
    }

    const bulletMatch = trimmed.match(/^(?:[•・▪◦]|[-*])\s+(.+)$/);
    if (bulletMatch) {
      if (prevKind === 'table' && out.length > 0 && out[out.length - 1] !== '') {
        out.push('');
      }
      let body = bulletMatch[1];
      if (!body.startsWith('**')) {
        body = body.replace(/^([^:：\n]{2,18})([:：]\s*)/, '**$1**$2');
      }
      out.push(`- ${body}`);
      prevKind = 'bullet';
    } else {
      if ((prevKind === 'bullet' || prevKind === 'table') && out.length > 0 && out[out.length - 1] !== '') {
        out.push('');
      }
      out.push(trimmed);
      prevKind = 'text';
    }
  }
  return out.join('\n');
}

/** 서버 카드의 실제 순위(1부터). 구버전 응답처럼 숫자가 비었을 때만 표시 순서를 쓴다. */
function resolveCardRank(rank: number | undefined, fallback: number): number {
  return typeof rank === 'number' && Number.isInteger(rank) && rank >= 1 ? rank : fallback;
}

function Card({
  card,
  rank,
  lang,
  replyLang,
  showSearch,
  send,
  saved = false,
  onToggleSave,
  onOpenClinic,
  lightning = false,
}: {
  showSearch: boolean;
  rank: number;
  lang: Lang;
  replyLang: Lang;
  card: Schemas['RecommendationCard'];
  send: Send;
  saved?: boolean;
  onToggleSave?: () => void;
  onOpenClinic?: (placeId: string, menuIds: string[]) => void;
  lightning?: boolean;
}) {
  const [showProof, setShowProof] = useState(false);
  const ja = lang === 'ja';
  const replyJa = replyLang === 'ja';
  const n = (value: number) => value.toLocaleString(lang);
  const won = ja ? 'ウォン' : '원';
  const yen = ja ? '円' : '엔';
  const areaName =
    areas.find(area => area.id === card.area)?.[ja ? 'nameJa' : 'nameKo']?.split(' (')[0] ??
    card.area;
  const stationLabel = ja ? card.stationJa : card.stationKo;
  const hasWalkInStation = ja ? stationLabel.includes('徒歩') : stationLabel.includes('도보');
  const isRank1 = rank === 1;
  const newClinic = isNewClinic(card.placeId);

  return (
    <article
      className={`agent-card dopa-agent-card${isRank1 ? ' is-top-rec' : ''}${
        lightning && isRank1 ? ' is-rank1-lightning' : ''
      }`}
    >
      <div className="dopa-agent-card-inner">
        <div className="dopa-agent-card-head">
          <div className="dopa-agent-card-top">
            <div className="dopa-agent-title-row">
              {isRank1 ? (
                <span className="dopa-agent-rank1-pill">
                  <span className="dopa-agent-rank1-spark" aria-hidden="true">
                    ✦
                  </span>
                  <span>{ja ? '最安 1位' : '최저가 1위'}</span>
                </span>
              ) : (
                <span className="dopa-agent-rank-index num">
                  {showSearch
                    ? `${ja ? '検索' : '검색'}${card.searchId.replace(/^s/, '')}-${rank}`
                    : String(rank).padStart(2, '0')}
                </span>
              )}
              <h3 className="dopa-agent-card-title">
                {ja ? card.clinicNameJa : card.clinicNameKo}
              </h3>
            </div>
            <div className="dopa-agent-card-badges">
              {newClinic ? (
                <span className="dopa-agent-new-inline">NEW</span>
              ) : (
                <span className="dopa-agent-rating-inline num">
                  <Star size={11} strokeWidth={2.4} fill="currentColor" aria-hidden="true" />
                  <span>{card.rating.toFixed(1)}</span>
                </span>
              )}
              {card.japaneseSupport && (
                <span className="dopa-agent-jp-inline">{ja ? '日本語' : '일본어'}</span>
              )}
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

          <p className="dopa-agent-card-loc">
            <strong>{ja ? card.menuNameJa : card.menuNameKo}</strong> · {stationLabel}
            {card.walkMinutes !== null && !hasWalkInStation
              ? ja
                ? ` · 徒歩${card.walkMinutes}分`
                : ` · 도보 ${card.walkMinutes}분`
              : !hasWalkInStation
                ? ` · ${areaName}`
                : ''}
          </p>
        </div>

        <div className="dopa-agent-price-hero">
          <div className="dopa-agent-price-nums">
            <strong className="num">
              <YenFlow value={card.totalJpy} />
            </strong>
            <span className="num">
              {n(card.totalKrw)}
              {won}
            </span>
          </div>
          <div className="dopa-agent-price-right">
            {card.savingsJpy !== 0 && (
              <>
                <span className="dopa-agent-save-pill num">
                  <NumFlow
                    value={Math.abs(card.savingsPct)}
                    prefix={card.savingsJpy > 0 ? '-' : '+'}
                    suffix="%"
                  />
                </span>
                <span className="dopa-agent-savings-inline num">
                  {card.savingsJpy > 0
                    ? ja
                      ? `¥${n(card.savingsJpy)}得`
                      : `¥${n(card.savingsJpy)} 절약`
                    : ja
                      ? `¥${n(Math.abs(card.savingsJpy))}高`
                      : `¥${n(Math.abs(card.savingsJpy))} 높음`}
                </span>
              </>
            )}
          </div>
        </div>

        {(card.reason || card.priceTags.length > 0) && (
          <div className="dopa-agent-submeta-row">
            {card.priceTags.slice(0, 2).map(tag => (
              <span key={tag} className="dopa-agent-micro-tag">
                {ja ? japaneseTags[tag] : priceLabels[tag]}
              </span>
            ))}
            {card.reason && <span className="dopa-agent-reason-inline">{card.reason}</span>}
          </div>
        )}

        <Disclosure open={showProof} onOpenChange={setShowProof}>
          <div className="dopa-agent-card-actions">
            <motion.button
              type="button"
              whileTap={{ scale: 0.975 }}
              className="dopa-agent-action-btn is-primary"
              onClick={() =>
                send(
                  replyJa
                    ? `${card.clinicNameJa}の${card.menuNameJa}の予約方法を教えてください。`
                    : `${card.clinicNameKo}에서 ${card.menuNameKo} 예약 방법을 알려 주세요.`
                )
              }
            >
              <span>{ja ? '予約・メモ' : '예약·메모'}</span>
            </motion.button>
            {onOpenClinic && (
              <motion.button
                type="button"
                whileTap={{ scale: 0.975 }}
                className="dopa-agent-action-btn"
                onClick={() => onOpenClinic(card.placeId, [card.menuId])}
              >
                <span>{ja ? '詳細' : '상세'}</span>
              </motion.button>
            )}
            <DisclosureTrigger>
              <button
                type="button"
                className={`dopa-agent-action-btn is-ghost${showProof ? ' is-open' : ''}`}
              >
                <span>{ja ? '公式表' : '공식표'}</span>
                <ChevronDown size={13} strokeWidth={2.2} className="dopa-agent-chev" aria-hidden="true" />
              </button>
            </DisclosureTrigger>
          </div>

          <DisclosureContent>
            <div className="dopa-agent-details-body">
              <Channels channels={card.bookingChannels} central={card.centralChannel} lang={lang} />
              <ExternalLink
                url={ja ? card.proof.urlJa : card.proof.urlKo}
                className="dopa-agent-inline-link"
              >
                {ja ? '公式料金表' : '공식 가격표'} · {ja ? card.proof.domainJa : card.proof.domainKo}
              </ExternalLink>
              <p className="dopa-agent-muted-note num">
                {ja ? '表示' : '표시'} {n(card.baseKrw)}
                {won} + {ja ? '税' : '부가세'} {n(card.vatKrw)}
                {won} · {ja ? '確認' : '확인'} {card.proof.capturedDate}
                {card.proof.verified
                  ? ja
                    ? '（画像確認済）'
                    : ' (스크린샷 확인)'
                  : ''}
              </p>
              <p className="dopa-agent-muted-note num">
                {ja ? '日本平均' : '일본 평균'} ¥{n(card.jpAverageJpy)} ({card.jpClinicCount}
                {ja ? '院' : '곳'} ¥{n(card.jpMinJpy)}–¥{n(card.jpMaxJpy)}) · {card.fxDateLabel} 100
                {yen}={n(card.krwPer100Jpy)}
                {won}
              </p>
              {card.jpClinic && (
                <div className="dopa-agent-comparison">
                  <div className="dopa-agent-comp-row">
                    <strong>{ja ? card.jpClinic.nameJa : card.jpClinic.nameKo}</strong>
                    <span className="num">¥{n(card.jpClinic.priceJpy)}</span>
                  </div>
                  <p className="dopa-agent-muted-note">
                    {ja ? card.jpClinic.planJa : card.jpClinic.planKo}
                  </p>
                </div>
              )}
              {(ja ? card.priceConditionJa : card.priceConditionKo) && (
                <p className="dopa-agent-muted-note">
                  {ja ? card.priceConditionJa : card.priceConditionKo}
                </p>
              )}
              {(ja ? card.priceNoteJa : card.priceNoteKo) && (
                <p className="dopa-agent-muted-note">{ja ? card.priceNoteJa : card.priceNoteKo}</p>
              )}
              {!newClinic && (
                <p className="dopa-agent-muted-note">
                  {ja ? 'AI評価' : 'AI 평점'} {card.rating.toFixed(1)}/5 ·{' '}
                  {card.ratingScope === 'branch'
                    ? ja
                      ? 'この支店の口コミ'
                      : '이 지점 후기'
                    : ja
                      ? '同ブランド参考'
                      : '브랜드 참고'}
                </p>
              )}
            </div>
          </DisclosureContent>
        </Disclosure>
      </div>
    </article>
  );
}

/** 따로 받을 때와의 비교 문장. 시술마다 최저가 병원이 한 곳이면 그곳이 가장 싼 묶음이다. */
function separateText(
  card: Schemas['BundleCard'],
  ja: boolean,
  n: (value: number) => string,
  won: string,
  yen: string
) {
  const extra = card.extraVsSeparateKrw;
  const extraJpy = card.extraVsSeparateJpy;
  if (card.separateClinicCount === 1) {
    const best = card.separatePicks[0];
    const name = best ? (ja ? best.clinicNameJa : best.clinicNameKo) : '';
    if (extra === 0)
      return ja
        ? '施術ごとの最安値がすべてこの院です。いちばん安い組み合わせです。'
        : '시술마다 최저가가 모두 이 병원이에요. 가장 싼 묶음이에요.';
    return ja
      ? `いちばん安い組み合わせは${name}（合計 ${n(card.separateTotalKrw)}${won}）で、この院は ${n(extra)}${won}（${n(extraJpy)}${yen}）高くなります。`
      : `가장 싼 묶음은 ${name}(합계 ${n(card.separateTotalKrw)}${won})이고, 이 병원은 ${n(extra)}${won}(${n(extraJpy)}${yen}) 더 들어요.`;
  }
  const where = card.separatePicks
    .map(p => `${ja ? p.menuNameJa : p.menuNameKo}: ${ja ? p.clinicNameJa : p.clinicNameKo}`)
    .join(ja ? '、' : ', ');
  const base = ja
    ? `施術ごとに一番安い院へ別々に行くと合計 ${n(card.separateTotalKrw)}${won}（${card.separateClinicCount}院: ${where}）。`
    : `시술마다 가장 싼 병원에 따로 가면 합계 ${n(card.separateTotalKrw)}${won}(병원 ${card.separateClinicCount}곳: ${where}). `;
  if (extra > 0)
    return (
      base +
      (ja
        ? `この院1か所なら ${n(extra)}${won}（${n(extraJpy)}${yen}）多くかかります。`
        : `이 병원 한 곳이면 ${n(extra)}${won}(${n(extraJpy)}${yen}) 더 들어요.`)
    );
  if (extra === 0)
    return (
      base + (ja ? 'この院1か所でも合計は同じです。' : '이 병원 한 곳에서 받아도 합계가 같아요.')
    );
  return (
    base +
    (ja
      ? `この院1か所のほうが ${n(-extra)}${won} 安くなります。`
      : `이 병원 한 곳이 ${n(-extra)}${won} 더 싸요.`)
  );
}

function BundleCard({
  card,
  rank,
  lang,
  replyLang,
  send,
  saved = false,
  onToggleSave,
  onOpenClinic,
  lightning = false,
}: {
  lang: Lang;
  replyLang: Lang;
  rank: number;
  card: Schemas['BundleCard'];
  send: Send;
  saved?: boolean;
  onToggleSave?: () => void;
  onOpenClinic?: (placeId: string, menuIds: string[]) => void;
  lightning?: boolean;
}) {
  const [showProof, setShowProof] = useState(false);
  const ja = lang === 'ja';
  const replyJa = replyLang === 'ja';
  const n = (value: number) => value.toLocaleString(lang);
  const won = ja ? 'ウォン' : '원';
  const yen = ja ? '円' : '엔';
  const replyNames = card.items.map(i => (replyJa ? i.menuNameJa : i.menuNameKo));
  const menuIds = card.items.map(i => i.menuId);
  const tags = [...new Set(card.items.flatMap(i => i.priceTags))];
  const areaName =
    areas.find(area => area.id === card.area)?.[ja ? 'nameJa' : 'nameKo']?.split(' (')[0] ??
    card.area;
  const stationLabel = ja ? card.stationJa : card.stationKo;
  const hasWalkInStation = ja ? stationLabel.includes('徒歩') : stationLabel.includes('도보');
  const isRank1 = rank === 1;
  const newClinic = isNewClinic(card.placeId);

  return (
    <article
      className={`agent-card agent-bundle dopa-agent-card${isRank1 ? ' is-top-bundle' : ''}${
        lightning && isRank1 ? ' is-rank1-lightning' : ''
      }`}
    >
      <div className="dopa-agent-card-inner">
        <div className="dopa-agent-card-head">
          <div className="dopa-agent-card-top">
            <div className="dopa-agent-title-row">
              {isRank1 ? (
                <span className="dopa-agent-rank1-pill">
                  <span className="dopa-agent-rank1-spark" aria-hidden="true">
                    ✦
                  </span>
                  <span>{ja ? '1院最安' : '묶음 1위'}</span>
                </span>
              ) : (
                <span className="dopa-agent-rank-index num">{String(rank).padStart(2, '0')}</span>
              )}
              <h3 className="dopa-agent-card-title">
                {ja ? card.clinicNameJa : card.clinicNameKo}
              </h3>
            </div>
            <div className="dopa-agent-card-badges">
              {newClinic ? (
                <span className="dopa-agent-new-inline">NEW</span>
              ) : (
                <span className="dopa-agent-rating-inline num">
                  <Star size={11} strokeWidth={2.4} fill="currentColor" aria-hidden="true" />
                  <span>{card.rating.toFixed(1)}</span>
                </span>
              )}
              {card.japaneseSupport && (
                <span className="dopa-agent-jp-inline">{ja ? '日本語' : '일본어'}</span>
              )}
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

          <p className="dopa-agent-card-loc">
            {stationLabel}
            {!stationLabel.includes(areaName) && !hasWalkInStation ? ` · ${areaName}` : ''}
            {card.walkMinutes !== null &&
              !hasWalkInStation &&
              (ja ? ` · 徒歩${card.walkMinutes}分` : ` · 도보 ${card.walkMinutes}분`)}
          </p>
        </div>

        <div className="dopa-agent-price-hero">
          <div className="dopa-agent-price-nums">
            <strong className="num">
              <YenFlow value={card.totalJpy} />
            </strong>
            <span className="num">
              {n(card.totalKrw)}
              {won}
            </span>
          </div>
          <div className="dopa-agent-price-right">
            {card.savingsJpy !== 0 && (
              <>
                <span className="dopa-agent-save-pill num">
                  <NumFlow
                    value={Math.abs(card.savingsPct)}
                    prefix={card.savingsJpy > 0 ? '-' : '+'}
                    suffix="%"
                  />
                </span>
                <span className="dopa-agent-savings-inline num">
                  {card.savingsJpy > 0
                    ? ja
                      ? `¥${n(card.savingsJpy)}得`
                      : `¥${n(card.savingsJpy)} 절약`
                    : ja
                      ? `¥${n(Math.abs(card.savingsJpy))}高`
                      : `¥${n(Math.abs(card.savingsJpy))} 높음`}
                </span>
              </>
            )}
          </div>
        </div>

        <div className="dopa-agent-bundle-receipt">
          {card.items.map(item => (
            <div key={item.menuId} className="dopa-agent-bundle-item-row">
              <span className="dopa-agent-bundle-item-name">
                {ja ? item.menuNameJa : item.menuNameKo}
              </span>
              <span className="dopa-agent-bundle-item-price num">
                ¥{n(item.totalJpy)}
                <small>₩{n(item.totalKrw)}</small>
              </span>
            </div>
          ))}
        </div>

        {(card.reason || tags.length > 0) && (
          <div className="dopa-agent-submeta-row">
            {tags.slice(0, 2).map(tag => (
              <span key={tag} className="dopa-agent-micro-tag">
                {ja ? japaneseTags[tag] : priceLabels[tag]}
              </span>
            ))}
            {card.reason && <span className="dopa-agent-reason-inline">{card.reason}</span>}
          </div>
        )}

        <Disclosure open={showProof} onOpenChange={setShowProof}>
          <div className="dopa-agent-card-actions">
            <motion.button
              type="button"
              whileTap={{ scale: 0.975 }}
              className="dopa-agent-action-btn is-primary"
              onClick={() =>
                send(
                  replyJa
                    ? `${card.clinicNameJa}で${replyNames.join('と')}をまとめて受ける予約方法を教えてください。`
                    : `${card.clinicNameKo}에서 ${replyNames.join(' + ')} 묶음으로 받는 예약 방법을 알려 주세요.`
                )
              }
            >
              <span>{ja ? '予約・メモ' : '묶음 예약·메모'}</span>
            </motion.button>
            {onOpenClinic && (
              <motion.button
                type="button"
                whileTap={{ scale: 0.975 }}
                className="dopa-agent-action-btn"
                onClick={() => onOpenClinic(card.placeId, menuIds)}
              >
                <span>{ja ? '詳細' : '상세'}</span>
              </motion.button>
            )}
            <DisclosureTrigger>
              <button
                type="button"
                className={`dopa-agent-action-btn is-ghost${showProof ? ' is-open' : ''}`}
              >
                <span>{ja ? '比較・公式表' : '비교·공식표'}</span>
                <ChevronDown size={13} strokeWidth={2.2} className="dopa-agent-chev" aria-hidden="true" />
              </button>
            </DisclosureTrigger>
          </div>

          <DisclosureContent>
            <div className="dopa-agent-details-body">
              <p className="dopa-agent-compare-note">
                <Sparkles size={12} strokeWidth={2.3} aria-hidden="true" />
                <span>{separateText(card, ja, n, won, yen)}</span>
              </p>
              <Channels channels={card.bookingChannels} central={card.centralChannel} lang={lang} />
              <div className="dopa-agent-proof-links">
                {card.items.map(item => (
                  <ExternalLink
                    key={item.menuId}
                    url={ja ? item.proof.urlJa : item.proof.urlKo}
                    className="dopa-agent-inline-link"
                  >
                    {ja ? item.menuNameJa : item.menuNameKo} ·{' '}
                    {ja ? item.proof.domainJa : item.proof.domainKo}
                  </ExternalLink>
                ))}
              </div>
              <small className="dopa-agent-muted-note num">
                {ja
                  ? `日本平均合計 ¥${n(card.jpTotalJpy)} · 為替 ${card.fxDateLabel}（100${yen}=${n(card.krwPer100Jpy)}${won}）`
                  : `일본 평균 ¥${n(card.jpTotalJpy)} · 환율 ${card.fxDateLabel} (100${yen}=${n(card.krwPer100Jpy)}${won})`}
              </small>
            </div>
          </DisclosureContent>
        </Disclosure>
      </div>
    </article>
  );
}

type PlanReason = Schemas['PlanDeferred']['reasons'][number];
const laterReasons: PlanReason[] = ['downtime', 'budget', 'one_clinic'];

/** 일정에서 뺀 이유. 여행자는 이번 여행에서 빠짐, 거주자는 회복·예산 때문에 나중으로 미룸. */
function planReason(reason: PlanReason, days: number, resident: boolean, ja: boolean) {
  switch (reason) {
    case 'downtime':
      if (resident)
        return ja
          ? `回復最大${days}日 → 予定が終わってから（${days}日予定のない時に）`
          : `회복 최대 ${days}일 → 약속 끝난 뒤 (${days}일 동안 일정 없을 때)`;
      return ja
        ? `回復最大${days}日 → 帰国までに落ち着かない`
        : `회복 최대 ${days}일 → 귀국 전에 안 가라앉아요`;
    case 'budget':
      return resident
        ? ja
          ? '今月の予算オーバー → 来月のおすすめ候補'
          : '이번 달 예산 초과 → 다음 달 추천 후보'
        : ja
          ? '今回の予算オーバー'
          : '이번 여행 예산 초과';
    case 'needle':
      return ja ? '針を使う施術' : '바늘을 쓰는 시술';
    case 'far':
      return ja ? '6km以内に院がない' : '6km 안에 병원이 없어요';
    case 'no_clinic':
      return ja ? '条件に合う院がない' : '조건에 맞는 병원이 없어요';
    case 'one_clinic':
      return ja ? '同じ院でまとめて受けられない' : '한 병원에서 같이 받을 수 없어요';
  }
}

function TripPlanCard({
  card,
  lang,
  replyLang,
  send,
  savedSet,
  onToggleSaveClinic,
  onOpenClinic,
}: {
  lang: Lang;
  replyLang: Lang;
  card: Schemas['TripPlanCard'];
  send: Send;
  savedSet: Set<string>;
  onToggleSaveClinic?: (placeId: string, menuIds?: string[]) => void;
  onOpenClinic?: (placeId: string, menuIds: string[]) => void;
}) {
  const ja = lang === 'ja';
  const replyJa = replyLang === 'ja';
  const n = (value: number) => value.toLocaleString(lang);
  const won = ja ? 'ウォン' : '원';
  const yen = ja ? '円' : '엔';
  const resident = card.visitor === 'resident';
  const later = card.deferred.filter(
    d => !resident || d.reasons.every(r => laterReasons.includes(r))
  );
  const excluded = resident ? card.deferred.filter(d => !later.includes(d)) : [];
  const title = resident
    ? card.daysUntilEvent === null
      ? ja
        ? '予定なし'
        : '약속 없음'
      : card.daysUntilEvent === 0
        ? ja
          ? '今日予定あり'
          : '오늘 약속'
        : ja
          ? `次の予定まで${card.daysUntilEvent}日`
          : `다음 약속까지 ${card.daysUntilEvent}일`
    : ja
      ? `${card.tripDays}日間（${card.tripDays}日目が帰国日）`
      : `${card.tripDays}일 일정 (${card.tripDays}일째 귀국)`;
  const when = (visit: Schemas['PlanVisit']) => {
    if (visit.day !== null) {
      const ret =
        visit.day === card.tripDays
          ? ja
            ? '・帰国日（フライト前に）'
            : ' · 귀국일 (비행기 전에)'
          : '';
      return (ja ? `${visit.day}日目` : `${visit.day}일째`) + ret;
    }
    if (card.daysUntilEvent === null || visit.downtimeMaxDays === 0)
      return ja ? '今・予定の前ならいつでも' : '지금 · 약속 전 언제든';
    return ja
      ? `今・予定の${visit.downtimeMaxDays}日前までに`
      : `지금 · 약속 ${visit.downtimeMaxDays}일 전까지`;
  };
  const deferredItem = (d: Schemas['PlanDeferred']) => (
    <li key={d.menuId}>
      <span>{ja ? d.menuNameJa : d.menuNameKo}</span>{' '}
      <span>{d.reasons.map(r => planReason(r, d.downtimeMaxDays, resident, ja)).join(' · ')}</span>
      {d.cheapest && (
        <small>
          {ja ? '最安' : '최저가'} {ja ? d.cheapest.clinicNameJa : d.cheapest.clinicNameKo}{' '}
          {n(d.cheapest.totalKrw)}
          {won} · {n(d.cheapest.totalJpy)}
          {yen}
        </small>
      )}
    </li>
  );
  const alt = card.alternative;
  return (
    <article className="agent-card agent-plan dopa-agent-card">
      <div className="dopa-agent-card-inner">
        <div className="dopa-agent-card-head">
          <small className="dopa-agent-card-loc">
            {ja ? '日程プラン' : '일정 플랜'}
            {card.oneClinic &&
              card.visits.some(v => v.items.length > 1) &&
              (ja ? ' · 1院でまとめて' : ' · 한 병원에서 같이')}
            {card.radiusMeters !== null &&
              (ja
                ? ` · 基準地点から${card.radiusMeters / 1000}km以内`
                : ` · 기준 위치 ${card.radiusMeters / 1000}km 안`)}
          </small>
          <h3 className="dopa-agent-card-title">{title}</h3>
        </div>
        {card.visits.length > 0 && (
          <>
            <h4>{resident ? (ja ? '今回受ける施術' : '지금 받을 것') : ja ? '日ごとのプラン' : '날짜별 일정'}</h4>
            <ol className="agent-plan-visits">
              {card.visits.map(visit => {
                const replyNames = visit.items.map(i => (replyJa ? i.menuNameJa : i.menuNameKo));
                const menuIds = visit.items.map(i => i.menuId);
                return (
                  <li key={visit.cardId}>
                    <div className="dopa-agent-plan-visit-head">
                      <small>{when(visit)}</small>
                      {onToggleSaveClinic && (
                        <SaveClinicButton
                          saved={savedSet.has(visit.placeId)}
                          clinicName={ja ? visit.clinicNameJa : visit.clinicNameKo}
                          lang={lang}
                          onToggle={() => onToggleSaveClinic(visit.placeId, menuIds)}
                        />
                      )}
                    </div>
                    <p>
                      <b>{ja ? visit.clinicNameJa : visit.clinicNameKo}</b> ·{' '}
                      {ja ? visit.stationJa : visit.stationKo}
                      {visit.japaneseSupport && (ja ? ' · 日本語対応' : ' · 일본어 지원')}
                      {visit.distanceMeters !== null && ` · ${n(visit.distanceMeters)}m`}
                    </p>
                    <ul className="agent-bundle-items">
                      {visit.items.map(item => (
                        <li key={item.menuId}>
                          <span>{ja ? item.menuNameJa : item.menuNameKo}</span>{' '}
                          <span>
                            {n(item.totalKrw)}
                            {won} · {n(item.totalJpy)}
                            {yen}
                          </span>
                          <span>
                            {item.downtimeMaxDays > 0
                              ? ja
                                ? `回復最大${item.downtimeMaxDays}日`
                                : `회복 최대 ${item.downtimeMaxDays}일`
                              : ja
                                ? '回復期間なし'
                                : '회복 기간 없음'}
                          </span>
                          {item.priceTags.length > 0 && (
                            <span className="agent-tags">
                              {item.priceTags.map(tag => (
                                <span key={tag}>{ja ? japaneseTags[tag] : priceLabels[tag]}</span>
                              ))}
                            </span>
                          )}
                          <ExternalLink
                            url={ja ? item.proof.urlJa : item.proof.urlKo}
                            className="dopa-agent-inline-link"
                          >
                            {ja ? '公式料金表' : '공식 가격표'}
                          </ExternalLink>
                        </li>
                      ))}
                    </ul>
                    {visit.items.length > 1 && (
                      <p>
                        {ja ? 'この院の合計' : '이 병원 합계'} {n(visit.totalKrw)}
                        {won} · {n(visit.totalJpy)}
                        {yen}
                      </p>
                    )}
                    {(ja ? visit.priceConditionJa : visit.priceConditionKo) && (
                      <small className="dopa-agent-muted-note">
                        {ja ? '料金条件' : '가격 조건'}:{' '}
                        {ja ? visit.priceConditionJa : visit.priceConditionKo}
                      </small>
                    )}
                    <Channels
                      channels={visit.bookingChannels}
                      central={visit.centralChannel}
                      lang={lang}
                    />
                    <div className="dopa-agent-card-actions">
                      <button
                        type="button"
                        className="dopa-agent-action-btn is-primary"
                        onClick={() =>
                          send(
                            replyJa
                              ? `${visit.clinicNameJa}で${replyNames.join('と')}を受ける予約方法を教えてください。`
                              : `${visit.clinicNameKo}에서 ${replyNames.join(' + ')} 받는 예약 방법을 알려 주세요.`
                          )
                        }
                      >
                        {ja ? '予約・メモ' : '예약·메모'}
                      </button>
                      {onOpenClinic && (
                        <button
                          type="button"
                          className="dopa-agent-action-btn"
                          onClick={() => onOpenClinic(visit.placeId, menuIds)}
                        >
                          {ja ? '詳細' : '상세'}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
            <p className="num">
              {ja ? 'プラン合計' : '일정 합계'}{' '}
              <strong>
                {n(card.totalKrw)}
                {won}
              </strong>{' '}
              · {n(card.totalJpy)}
              {yen}
            </p>
            <p className="dopa-agent-muted-note num">
              {ja ? '日本の平均合計' : '일본 평균 합계'} {n(card.jpTotalJpy)}
              {ja ? '円より' : '엔 대비'} {n(Math.abs(card.savingsJpy))}
              {yen} ({Math.abs(card.savingsPct)}%){' '}
              {card.savingsJpy < 0 ? (ja ? '割高' : '높음') : ja ? 'お得' : '절약'}
            </p>
            {card.budget && (
              <p className="dopa-agent-muted-note num">
                {ja ? '予算の残り' : '남은 예산'}{' '}
                {card.budget.currency === 'JPY'
                  ? `${n(card.budget.left)}${yen}`
                  : `${n(card.budget.left)}${won}`}{' '}
                /{' '}
                {card.budget.currency === 'JPY'
                  ? `${n(card.budget.amount)}${yen}`
                  : `${n(card.budget.amount)}${won}`}
              </p>
            )}
            {alt && (
              <p className="dopa-agent-compare-note">
                {alt.oneClinic
                  ? ja
                    ? `1院（${alt.clinicNameJa}）でまとめて受けると合計 ${n(alt.totalKrw)}${won}`
                    : `한 병원(${alt.clinicNameKo})에서 같이 받으면 합계 ${n(alt.totalKrw)}${won}`
                  : ja
                    ? `施術ごとに一番安い院へ別々に行くと合計 ${n(alt.totalKrw)}${won}（${alt.clinicCount}院）`
                    : `시술마다 가장 싼 병원에 따로 가면 합계 ${n(alt.totalKrw)}${won}(병원 ${alt.clinicCount}곳)`}
                {alt.extraKrw > 0
                  ? ja
                    ? `で、${n(alt.extraKrw)}${won}高くなります。`
                    : `로, ${n(alt.extraKrw)}${won} 더 들어요.`
                  : alt.extraKrw < 0
                    ? ja
                      ? `で、${n(-alt.extraKrw)}${won}安くなります。`
                      : `로, ${n(-alt.extraKrw)}${won} 더 싸요.`
                    : ja
                      ? 'で、合計は同じです。'
                      : '로, 합계가 같아요.'}
              </p>
            )}
          </>
        )}
        {card.visits.length === 0 && (
          <p>
            {ja
              ? 'この条件では今回受けられる施術がありません。'
              : '이 조건으로는 이번에 받을 수 있는 시술이 없어요.'}
          </p>
        )}
        {later.length > 0 && (
          <>
            <h4>
              {resident
                ? ja
                  ? 'あとで受ける施術'
                  : '나중에 받을 것'
                : ja
                  ? '今回の日程に入れなかった施術'
                  : '이번 일정에서 뺀 시술'}
            </h4>
            <ul className="agent-plan-deferred">{later.map(deferredItem)}</ul>
          </>
        )}
        {excluded.length > 0 && (
          <>
            <h4>{ja ? '候補から外した施術' : '후보에서 뺀 시술'}</h4>
            <ul className="agent-plan-deferred">{excluded.map(deferredItem)}</ul>
          </>
        )}
        <small className="dopa-agent-muted-note num">
          {ja
            ? '営業時間・曜日限定価格はデータにありません。予約時に院へご確認ください。'
            : '영업시간·요일 한정가는 데이터에 없어요. 예약할 때 병원에 확인해 주세요.'}{' '}
          · {ja ? '為替' : '환율'} {card.fxDateLabel}{' '}
          {ja ? '時点、100円につき' : '기준 100엔당'} {n(card.krwPer100Jpy)}
          {won}
        </small>
      </div>
    </article>
  );
}

/** 답변 본문(마크다운). 스트리밍 중 이어 붙인 글도 같은 모양으로 그린다. */
export function AnswerText({ text }: { text: string }) {
  return (
    <div className="agent-markdown">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml components={AGENT_MD_COMPONENTS}>
        {normalizeAgentMarkdown(text)}
      </Markdown>
    </div>
  );
}

export function Reply({
  reply,
  send,
  lang,
  savedIds,
  onToggleSaveClinic,
  onOpenClinic,
  onPriceTable,
}: {
  lang: Lang;
  reply: Schemas['ChatResponse'];
  send: Send;
  savedIds?: string[];
  onToggleSaveClinic?: (placeId: string, menuIds?: string[]) => void;
  onOpenClinic?: (placeId: string, menuIds: string[]) => void;
  onPriceTable?: () => void;
}) {
  const [showAllClinics, setShowAllClinics] = useState(false);
  const ja = lang === 'ja';
  const replyLang: Lang = reply.lang ?? lang;
  const savedSet = useMemo(() => new Set(savedIds ?? []), [savedIds]);

  const searchIds = new Set([
    ...reply.cards.flatMap(card => (card.kind === 'recommendation' ? [card.searchId] : [])),
    ...reply.exclusions.map(item => item.searchId),
  ]);
  const showSearch = searchIds.size > 1;

  const contextCards = reply.cards.filter(
    (c): c is Schemas['LocationCard'] | Schemas['JpMatchCard'] =>
      c.kind === 'location' || c.kind === 'jp_match'
  );
  const clinicCards = reply.cards.filter(
    (c): c is Schemas['BundleCard'] | Schemas['RecommendationCard'] =>
      c.kind === 'bundle' || c.kind === 'recommendation'
  );
  const planCards = reply.cards.filter(
    (c): c is Schemas['TripPlanCard'] => c.kind === 'trip_plan'
  );
  const bookingCards = reply.cards.filter(
    (c): c is Schemas['BookingCard'] => c.kind === 'booking'
  );

  const visibleClinics =
    showAllClinics || showSearch ? clinicCards : clinicCards.slice(0, INITIAL_VISIBLE_CLINICS);
  const hiddenCount = showSearch ? 0 : Math.max(0, clinicCards.length - INITIAL_VISIBLE_CLINICS);
  const nextHidden = hiddenCount > 0 ? clinicCards[INITIAL_VISIBLE_CLINICS] : null;
  const nextHiddenPreview = nextHidden
    ? `${ja ? nextHidden.clinicNameJa : nextHidden.clinicNameKo} ¥${nextHidden.totalJpy.toLocaleString(lang)}`
    : '';

  let bundleRank = 0;
  let recRank = 0;
  const searchRankMap = new Map<string, number>();

  return (
    <div className="agent-reply">
      {reply.conditionSummary && <p className="agent-summary">{reply.conditionSummary}</p>}
      <AnswerText text={reply.answer} />
      {reply.fallback && (
        <aside className="agent-fallback">
          <p>{reply.fallback === 'daily_limit' ? copy[replyLang].dailyLimit : copy[replyLang].fallbackError}</p>
          <PriceTableLink lang={replyLang} onClick={onPriceTable} />
        </aside>
      )}

      {contextCards.length > 0 && (
        <div className="dopa-ctx-strip">
          {contextCards.map((card, index) =>
            card.kind === 'location' ? (
              <LocationCard key={'location:' + index} card={card} lang={lang} />
            ) : (
              <JpMatchCard key={'jp:' + index} card={card} lang={lang} />
            )
          )}
        </div>
      )}

      {visibleClinics.length > 0 && (
        <div className="dopa-agent-cards-stack">
          {visibleClinics.map((card, idx) => {
            const isHeroFirst = idx === 0 && !showSearch;
            if (card.kind === 'bundle') {
              bundleRank += 1;
              const displayRank = resolveCardRank(card.rank, bundleRank);
              return (
                <BlurFade
                  key={card.searchId + ':' + card.cardId}
                  delay={Math.min(idx, 3) * 0.04}
                  duration={0.26}
                  offset={8}
                  direction="up"
                >
                  <BundleCard
                    card={card}
                    rank={displayRank}
                    lang={lang}
                    replyLang={replyLang}
                    send={send}
                    saved={savedSet.has(card.placeId)}
                    onToggleSave={
                      onToggleSaveClinic
                        ? () =>
                            onToggleSaveClinic(
                              card.placeId,
                              card.items.map(it => it.menuId)
                            )
                        : undefined
                    }
                    onOpenClinic={onOpenClinic}
                    lightning={isHeroFirst && displayRank === 1}
                  />
                </BlurFade>
              );
            }
            recRank += 1;
            const localRank = (searchRankMap.get(card.searchId) ?? 0) + 1;
            searchRankMap.set(card.searchId, localRank);
            const displayRank = resolveCardRank(card.rank, showSearch ? localRank : recRank);
            return (
              <BlurFade
                key={card.searchId + ':' + card.cardId}
                delay={Math.min(idx, 3) * 0.04}
                duration={0.26}
                offset={8}
                direction="up"
              >
                <Card
                  showSearch={showSearch}
                  rank={displayRank}
                  lang={lang}
                  replyLang={replyLang}
                  card={card}
                  send={send}
                  saved={savedSet.has(card.placeId)}
                  onToggleSave={
                    onToggleSaveClinic
                      ? () => onToggleSaveClinic(card.placeId, [card.menuId])
                      : undefined
                  }
                  onOpenClinic={onOpenClinic}
                  lightning={isHeroFirst && displayRank === 1}
                />
              </BlurFade>
            );
          })}
          {hiddenCount > 0 && (
            <button
              type="button"
              className="dopa-agent-more-btn"
              onClick={() => setShowAllClinics(v => !v)}
            >
              {showAllClinics ? (
                <>
                  <span>{ja ? '上位2院だけ見る' : '상위 2곳만 보기'}</span>
                  <ChevronDown
                    size={13}
                    strokeWidth={2.3}
                    className="dopa-agent-chev is-flipped"
                    aria-hidden="true"
                  />
                </>
              ) : (
                <>
                  <Plus size={13} strokeWidth={2.4} aria-hidden="true" />
                  <span>{ja ? `あと${hiddenCount}院を見る` : `${hiddenCount}곳 더 보기`}</span>
                  {nextHiddenPreview && (
                    <span className="dopa-agent-more-preview num">{nextHiddenPreview}</span>
                  )}
                </>
              )}
            </button>
          )}
        </div>
      )}

      {planCards.length > 0 && (
        <div className="dopa-agent-cards-stack">
          {planCards.map((card, index) => (
            <TripPlanCard
              key={'plan:' + index}
              card={card}
              lang={lang}
              replyLang={replyLang}
              send={send}
              savedSet={savedSet}
              onToggleSaveClinic={onToggleSaveClinic}
              onOpenClinic={onOpenClinic}
            />
          ))}
        </div>
      )}

      {bookingCards.length > 0 && (
        <div className="dopa-agent-cards-stack">
          {bookingCards.map((card, index) => (
            <BookingCard
              key={'booking:' + index}
              card={card}
              lang={lang}
              saved={savedSet.has(card.placeId)}
              onToggleSave={
                onToggleSaveClinic ? () => onToggleSaveClinic(card.placeId) : undefined
              }
            />
          ))}
        </div>
      )}

      {reply.exclusions.length > 0 && (
        <details>
          <summary>{ja ? '候補から除外したクリニック' : '추천에서 제외된 병원'}</summary>
          {reply.exclusions.map(e => (
            <p key={[e.searchId, e.menuId, e.reason].join(':')}>
              {ja ? '検索' : '검색'} {e.searchId.replace(/^s/, '')} ·{' '}
              {menus.find(menu => menu.id === e.menuId)?.[ja ? 'shortJa' : 'shortKo'] ?? e.menuId} ·{' '}
              {ja ? japaneseExclusions[e.reason] : exclusions[e.reason]} · {e.clinicCount}
              {ja ? '院' : '곳'}
            </p>
          ))}
        </details>
      )}
      {reply.questions.length > 0 && <Questions lang={lang} items={reply.questions} send={send} />}
      {reply.cautions.length > 0 && <h3>{ja ? '確認しておきたいこと' : '확인할 사항'}</h3>}
      {reply.cautions.map(text => (
        <p key={text}>
          <SvgIcon name="info" /> {text}
        </p>
      ))}
      <small>{reply.disclaimer}</small>
      {reply.suggestions.length > 0 && (
        <section>
          <h3>{ja ? '続けて質問する' : '이어서 물어보기'}</h3>
          {reply.suggestions.map(s => (
            <button key={s} onClick={() => send(s)}>
              {s} <SvgIcon name="chevronRight" />
            </button>
          ))}
        </section>
      )}
    </div>
  );
}
