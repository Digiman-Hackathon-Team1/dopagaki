import { Questions, type Send } from './Questions';
import { areas, menus } from '@/data/prices.json';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Schemas } from '@repo/api-client';
import { SvgIcon } from '@/components/SvgIcon';

type Lang = Schemas['ChatRequest']['lang'];
const exclusions = { needle: '바늘 시술 기피', downtime: '회복 기간 초과', area: '다른 지역', japanese: '일본어 미지원', budget: '예산 초과' };

const japaneseExclusions = { needle: '針を使う施術', downtime: 'ダウンタイム超過', area: 'エリア外', japanese: '日本語非対応', budget: '予算超過' };

const priceLabels = {
  first_visit: '첫 방문', event: '이벤트가', day_limited: '요일 한정',
  app_benefit: '앱 혜택', anesthesia: '마취비 확인', extra_fee: '추가 비용'
};

const japaneseTags = {
  first_visit: '初回限定', event: 'キャンペーン', day_limited: '曜日限定',
  app_benefit: 'アプリ特典', anesthesia: '麻酔料要確認', extra_fee: '追加料金'
};

function Card({ card, lang, showSearch }: { showSearch: boolean; lang: Lang; card: Schemas['RecommendationCard'] }) {
  const ja = lang === 'ja';
  const n = (value: number) => value.toLocaleString(lang);
  const won = ja ? 'ウォン' : '원';
  const yen = ja ? '円' : '엔';
  return <article className="agent-card"><small>{showSearch && (ja ? '検索 ' : '검색 ') + card.searchId.replace(/^s/, '') + ' · '}{ja ? card.stationJa : card.stationKo} · {areas.find(area => area.id === card.area)?.[ja ? 'nameJa' : 'nameKo'] ?? card.area}{card.japaneseSupport && (ja ? ' · 日本語対応' : ' · 일본어 지원')}</small>
    <h3>{ja ? card.clinicNameJa : card.clinicNameKo} · {ja ? card.menuNameJa : card.menuNameKo}</h3><strong>{n(card.totalKrw)}{won}</strong> · {n(card.totalJpy)}{yen}
    <p>{ja ? '日本の平均' : '일본 평균'} {n(card.jpAverageJpy)}{ja ? '円より' : '엔 대비'} {n(Math.abs(card.savingsJpy))}{yen} ({Math.abs(card.savingsPct)}%) {card.savingsJpy < 0 ? (ja ? '割高' : '높음') : (ja ? 'お得' : '절약')}</p>
    {card.priceTags.length > 0 && <p className="agent-tags">{card.priceTags.map(tag => <span key={tag}>{ja ? japaneseTags[tag] : priceLabels[tag]}</span>)}</p>}
    {card.reason && <p>{card.reason}</p>}
    <details><summary>{ja ? '料金条件と比較の根拠' : '가격 조건과 비교 근거'}</summary>
      <p>{ja ? card.clinicNameKo : card.clinicNameJa} · {ja ? card.menuNameKo : card.menuNameJa} · {ja ? card.stationKo : card.stationJa}</p>
      <p>{ja ? '表示価格' : '표시가'} {n(card.baseKrw)}{won} · {ja ? '追加の付加価値税' : '추가 부가세'} {n(card.vatKrw)}{won}</p>
      <p>{ja ? '日本' : '일본'} {card.jpClinicCount}{ja ? '院' : '곳'} · {n(card.jpMinJpy)}~{n(card.jpMaxJpy)}{yen} · {ja ? '為替' : '환율'} {card.fxDateLabel} {ja ? '時点、100円につき' : '기준 100엔당'} {n(card.krwPer100Jpy)}{won}</p>
      {card.distanceMeters !== null && <p>{ja ? '直線距離' : '직선거리'} {n(card.distanceMeters)}m {card.walkMinutes !== null && (ja ? '· 徒歩約' : '· 도보 약 ') + card.walkMinutes + (ja ? '分' : '분')}</p>}
      <p>{ja ? card.priceConditionJa : card.priceConditionKo} {ja ? card.priceNoteJa : card.priceNoteKo}</p>
      {/^https?:\/\//i.test(card.sourceUrl) && <a href={card.sourceUrl} target="_blank" rel="noreferrer">{ja ? '公式料金を確認' : '공식 가격 확인'} <SvgIcon name="external" /></a>}
    </details>
  </article>;
}

export function Reply({ reply, send, lang }: { lang: Lang; reply: Schemas['ChatResponse']; send: Send }) {
  const searchIds = new Set([
    ...reply.cards.flatMap(card => card.kind === 'recommendation' ? [card.searchId] : []),
    ...reply.exclusions.map(item => item.searchId)
  ]);
  return <div className="agent-reply">
    {reply.conditionSummary && <p className="agent-summary">{reply.conditionSummary}</p>}
    <div className="agent-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
      a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
      img: ({ alt }) => <>{alt}</>
    }}>{reply.answer}</Markdown></div>
    {reply.cards.some(card => card.kind === 'recommendation') && <h3>{lang === 'ja' ? 'おすすめのクリニック' : '추천 병원'}</h3>}
    {reply.cards.map(card => card.kind === 'recommendation' && <Card showSearch={searchIds.size > 1} lang={lang} key={card.searchId + ':' + card.cardId} card={card} />)}
    {reply.exclusions.length > 0 && <details><summary>{lang === 'ja' ? '候補から除外したクリニック' : '추천에서 제외된 병원'}</summary>{reply.exclusions.map(e =>
      <p key={[e.searchId,e.menuId,e.reason].join(':')}>{lang === 'ja' ? '検索' : '검색'} {e.searchId.replace(/^s/, '')} · {menus.find(menu => menu.id === e.menuId)?.[lang === 'ja' ? 'shortJa' : 'shortKo'] ?? e.menuId} · {lang === 'ja' ? japaneseExclusions[e.reason] : exclusions[e.reason]} · {e.clinicCount}{lang === 'ja' ? '院' : '곳'}</p>)}</details>}
    {reply.questions.length > 0 && <Questions lang={lang} items={reply.questions} send={send} />}
    {reply.cautions.length > 0 && <h3>{lang === 'ja' ? '確認しておきたいこと' : '확인할 사항'}</h3>}
    {reply.cautions.map(text => <p key={text}><SvgIcon name="info" /> {text}</p>)}
    <small>{reply.disclaimer}</small>
    {reply.suggestions.length > 0 && <section><h3>{lang === 'ja' ? '続けて質問する' : '이어서 물어보기'}</h3>{reply.suggestions.map(s =>
      <button key={s} onClick={() => send(s)}>{s} <SvgIcon name="chevronRight" /></button>)}</section>}
  </div>;
}
