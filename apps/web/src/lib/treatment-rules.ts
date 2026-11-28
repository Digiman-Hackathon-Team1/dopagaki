import type { MenuItem } from '../types.ts';

/**
 * 시술마다 정해진 값: 회복 기간, 바늘 여부, 고민별 시술표, 시술 시간.
 * api 에이전트 데이터 스냅샷의 원본이다(apps/api/scripts/export-web-snapshot.mts).
 * 값을 바꾸면 `pnpm --filter @repo/api export:web-data`로 스냅샷을 다시 만든다.
 */

/** 고민 분류 6개. api `ConcernId`와 같다. */
export type ConcernId = 'pores' | 'glow' | 'lift' | 'dry' | 'jaw' | 'skinbase';

/** Worst-case visible downtime in days, read from the TREATMENT_DOWNTIME copy (clinic-helpers.ts). */
export const DOWNTIME_MAX_DAYS: Record<string, number> = {
  'rejuran-2cc': 3,
  'shrink-300': 0,
  potenza: 4,
  'jaw-botox': 0,
  'juvelook-2cc': 2,
  're2o-5cc': 3,
  'inmode-fx': 5,
  lalapeel: 0,
  aquapeel: 0
};

/** Treatments that use a needle or microneedles. */
export const NEEDLE_MENUS = new Set(['rejuran-2cc', 'potenza', 'jaw-botox', 'juvelook-2cc', 're2o-5cc']);

/** Concern → treatments, most direct first. */
export const CONCERN_MENUS: Record<ConcernId, string[]> = {
  pores: ['potenza', 'aquapeel', 'juvelook-2cc'],
  glow: ['lalapeel', 'aquapeel', 'rejuran-2cc'],
  lift: ['shrink-300', 'inmode-fx'],
  dry: ['rejuran-2cc', 'juvelook-2cc', 're2o-5cc'],
  jaw: ['jaw-botox'],
  skinbase: ['re2o-5cc', 'juvelook-2cc', 'rejuran-2cc']
};

/** Treatment time in minutes, read from the menu's own note ("施術約40分…"); null if the note has none. */
export function treatmentMinutes(menu: Pick<MenuItem, 'leadTimeNoteJa'>): number | null {
  const m = /約\s*(\d+)\s*分/.exec(menu.leadTimeNoteJa ?? '');
  return m ? Number(m[1]) : null;
}

export function formatMinutes(minutes: number, isJa: boolean): string {
  return isJa ? `約${minutes}分` : `약 ${minutes}분`;
}
