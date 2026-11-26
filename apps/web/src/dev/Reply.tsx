import { useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Schemas } from '@repo/api-client';
import { SvgIcon } from '@/components/SvgIcon';

type Send = (message: string) => void;
const exclusions = { needle: '바늘 시술 기피', downtime: '회복 기간 초과', area: '다른 지역', japanese: '일본어 미지원', budget: '예산 초과' };

function Question({ item, send }: { item: Schemas['Question']; send: Send }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [direct, setDirect] = useState(false);
  const [custom, setCustom] = useState('');
  const message = [...selected, direct ? custom.trim() : ''].filter(Boolean).join(', ');
  return <fieldset><legend>{item.question}</legend>
    {item.options.map(option => <button key={option} aria-pressed={item.multiSelect ? selected.includes(option) : undefined}
      onClick={() => item.multiSelect ? setSelected(prev => prev.includes(option) ? prev.filter(s => s !== option) : [...prev, option]) : send(option)}>{option}</button>)}
    <button aria-pressed={direct} onClick={() => setDirect(!direct)}>직접 입력</button>
    {direct && <input aria-label={item.question} placeholder="답변을 입력하세요" maxLength={2000} value={custom} onChange={e => setCustom(e.target.value)} />}
    {(item.multiSelect || direct) && <button disabled={!message || [...message].length > 2000} onClick={() => send(message)}>답변 보내기</button>}
  </fieldset>;
}

const priceLabels = {
  first_visit: '첫 방문', event: '이벤트가', day_limited: '요일 한정',
  app_benefit: '앱 혜택', anesthesia: '마취비 확인', extra_fee: '추가 비용'
};

function Card({ card }: { card: Schemas['RecommendationCard'] }) {
  const n = (value: number) => value.toLocaleString('ko-KR');
  return <article className="agent-card"><small>{card.stationKo} · {card.area}{card.japaneseSupport && ' · 일본어 지원'}</small>
    <h3>{card.clinicNameKo} · {card.menuNameKo}</h3><strong>{n(card.totalKrw)}원</strong> · {n(card.totalJpy)}엔
    <p>일본 평균 {n(card.jpAverageJpy)}엔 대비 {n(Math.abs(card.savingsJpy))}엔 ({Math.abs(card.savingsPct)}%) {card.savingsJpy < 0 ? '높음' : '절약'}</p>
    {card.priceTags.length > 0 && <p className="agent-tags">{card.priceTags.map(tag => <span key={tag}>{priceLabels[tag]}</span>)}</p>}
    {card.reason && <p>{card.reason}</p>}
    <details><summary>가격 조건과 비교 근거</summary>
      <p>{card.clinicNameJa} · {card.menuNameJa} · {card.stationJa}</p>
      <p>표시가 {n(card.baseKrw)}원 · 추가 부가세 {n(card.vatKrw)}원</p>
      <p>일본 {card.jpClinicCount}곳 · {n(card.jpMinJpy)}~{n(card.jpMaxJpy)}엔 · 환율 {card.fxDateLabel} 기준 100엔당 {n(card.krwPer100Jpy)}원</p>
      {card.distanceMeters !== null && <p>직선거리 {n(card.distanceMeters)}m {card.walkMinutes !== null && '· 도보 약 ' + card.walkMinutes + '분'}</p>}
      <p>{card.priceConditionKo} {card.priceNoteKo}</p>
      {/^https?:\/\//i.test(card.sourceUrl) && <a href={card.sourceUrl} target="_blank" rel="noreferrer">공식 가격 확인 <SvgIcon name="external" /></a>}
    </details>
  </article>;
}

export function Reply({ reply, send }: { reply: Schemas['ChatResponse']; send: Send }) {
  return <div className="agent-reply">
    {reply.conditionSummary && <p className="agent-summary">{reply.conditionSummary}</p>}
    <div className="agent-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml>{reply.answer}</Markdown></div>
    {reply.cards.map(card => card.kind === 'recommendation' && <Card key={card.searchId + ':' + card.cardId} card={card} />)}
    {reply.exclusions.length > 0 && <details><summary>추천에서 제외된 병원</summary>{reply.exclusions.map(e =>
      <p key={[e.searchId,e.menuId,e.reason].join(':')}>{e.searchId} · {e.menuId} · {exclusions[e.reason]} · {e.clinicCount}곳</p>)}</details>}
    {reply.questions.map(item => <Question key={item.question} item={item} send={send} />)}
    {reply.cautions.map(text => <p key={text}><SvgIcon name="info" /> {text}</p>)}
    <small>{reply.disclaimer}</small>
    {reply.suggestions.length > 0 && <section><h3>이어서 물어보기</h3>{reply.suggestions.map(s =>
      <button key={s} onClick={() => send(s)}>{s} <SvgIcon name="chevronRight" /></button>)}</section>}
  </div>;
}
