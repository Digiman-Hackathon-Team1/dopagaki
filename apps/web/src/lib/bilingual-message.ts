export type MessageLang = 'ko' | 'ja';
export type MessagePurpose = 'inquiry' | 'order';

export interface BilingualLine {
  primary: string;
  gloss: string;
  spacer: boolean;
}

/**
 * Decides the safest default outbound language for a clinic:
 * - Pre-visit inquiry in JA UI to a Japanese-speaking clinic -> send directly in Japanese ('ja').
 * - Pre-visit inquiry to a Korean-only clinic (or KO UI) -> send clean Korean ('ko').
 * - Walk-in reception memo -> always Korean ('ko') so Seoul desk staff read it immediately.
 */
export function resolveDefaultSendLang(
  uiLang: MessageLang,
  japaneseSupport: boolean,
  purpose: MessagePurpose
): MessageLang {
  if (purpose === 'inquiry' && uiLang === 'ja' && japaneseSupport) {
    return 'ja';
  }
  return 'ko';
}

/**
 * Pairs primary outbound text with its line-aligned translation gloss so travelers
 * can verify every single Korean sentence before copying only the clean outbound text.
 */
export function buildInterlinearLines(primaryText: string, glossText: string): BilingualLine[] {
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
