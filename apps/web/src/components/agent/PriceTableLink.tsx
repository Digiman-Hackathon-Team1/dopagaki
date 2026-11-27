import type { Schemas } from '@repo/api-client';
import { copy } from './copy';

/** 시세표 안내. onClick이 있으면 페이지를 새로 열지 않고 같은 앱의 시세표로 넘어간다. */
export function PriceTableLink({ lang, onClick }: { lang: Schemas['ChatRequest']['lang']; onClick?: () => void }) {
  return <a className="agent-price-table-link" href={'/?lang=' + lang} onClick={onClick && (event => {
    event.preventDefault();
    onClick();
  })}>{copy[lang].priceTable} ↗</a>;
}
