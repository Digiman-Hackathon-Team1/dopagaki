import type { Schemas } from '@repo/api-client';
import { copy } from './copy';

export function PriceTableLink({ lang }: { lang: Schemas['ChatRequest']['lang'] }) {
  return <a className="agent-price-table-link" href={'/?lang=' + lang}>{copy[lang].priceTable} ↗</a>;
}
