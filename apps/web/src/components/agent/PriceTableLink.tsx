import type { Schemas } from '@repo/api-client';
import { copy } from './copy';

/**
 * 시세표 안내. onClick이 있으면 같은 앱의 시세표 탭으로 넘어가고(대화는 남는다),
 * 새 탭으로 여는 클릭(Ctrl·Cmd·Shift·가운데 버튼)은 링크 그대로 둔다.
 */
export function PriceTableLink({ lang, onClick }: { lang: Schemas['ChatRequest']['lang']; onClick?: () => void }) {
  return <a className="agent-price-table-link" href={'/?lang=' + lang} onClick={onClick && (event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onClick();
  })}>{copy[lang].priceTable}{!onClick && ' ↗'}</a>;
}
