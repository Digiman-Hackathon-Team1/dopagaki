import type { Lang } from '@/types';

const storageKey = 'seoul_payback_lang';

export function readSiteLanguage(query: string | null): Lang {
  if (query === 'ko' || query === 'ja') return query;
  try {
    return localStorage.getItem(storageKey) === 'ko' ? 'ko' : 'ja';
  } catch {
    return 'ja';
  }
}

export function saveSiteLanguage(lang: Lang) {
  try {
    localStorage.setItem(storageKey, lang);
  } catch {
    // The selection still applies to the current page.
  }
}
