import type { Lang, PlaceItem } from '@/types.ts';
import {
  buildCommunityReviewLinks,
  buildExternalReviewLinks,
  type CommunityReviewLink
} from '@/data/brand-reviews.ts';

export type ReviewAccessMode = 'no_login' | 'partial_login' | 'login_required';

export interface InstantReviewLink {
  id: 'google_reviews' | 'note_jp' | 'naver_place' | 'naver_blog' | 'naver_cafe';
  labelKo: string;
  labelJa: string;
  noteKo: string;
  noteJa: string;
  badgeKo: string;
  badgeJa: string;
  url: string;
}

export interface EnhancedCommunityReviewLink extends CommunityReviewLink {
  accessMode: ReviewAccessMode;
}

/**
 * Zero-login direct review sources ordered for instant reading even in iOS Safari Private Mode.
 * Lives outside brand-reviews.ts so backend catalog snapshots stay untouched.
 */
export function buildInstantReviewLinks(place: PlaceItem, lang: Lang): InstantReviewLink[] {
  const ext = buildExternalReviewLinks(place, lang);
  const byId = new Map(ext.map((l) => [l.id, l]));
  const jaBrandName = place.brandJa.replace(/\s*\([^)]*\)/, '').trim();

  if (lang === 'ja') {
    return [
      {
        id: 'google_reviews',
        labelKo: '구글 지도 별점·실후기',
        labelJa: 'Googleマップ 日本語口コミ',
        noteKo: '로그인 없이 별점과 번역 후기 바로 보기',
        noteJa: '星評価と日本語訳つき実体験をそのまま閲覧',
        badgeKo: '무로그인',
        badgeJa: '登録不要',
        url: byId.get('google_reviews')!.url
      },
      {
        id: 'note_jp',
        labelKo: 'note 일본인 방한 후기',
        labelJa: 'note 日本人渡韓レポ',
        noteKo: '로그인 없이 일본인 여행객의 솔직 블로그 조회',
        noteJa: '日本人旅行者の本音ブログ・経過レポを検索',
        badgeKo: '무로그인',
        badgeJa: '登録不要',
        url: `https://note.com/search?q=${encodeURIComponent(`${jaBrandName} 韓国`)}&context=note`
      },
      {
        id: 'naver_place',
        labelKo: '네이버 지도 방문자 리뷰',
        labelJa: 'NAVERマップ 現地レビュー',
        noteKo: '로그인 없이 영수증 인증 방문자 리뷰 확인',
        noteJa: '韓国ローカル訪問者の最新写真・評価を確認',
        badgeKo: '무로그인',
        badgeJa: '登録不要',
        url: byId.get('naver_place')!.url
      },
      {
        id: 'naver_blog',
        labelKo: '네이버 블로그 실후기',
        labelJa: 'NAVERブログ 写真レポ',
        noteKo: '로그인 없이 시술 전후 사진·대기시간 확인',
        noteJa: '施術写真や待ち時間の詳しい体験記をチェック',
        badgeKo: '무로그인',
        badgeJa: '登録不要',
        url: byId.get('naver_blog')!.url
      }
    ];
  }

  return [
    {
      id: 'naver_blog',
      labelKo: '네이버 블로그 실후기',
      labelJa: 'NAVERブログ 写真レポ',
      noteKo: '내돈내산 사진·대기시간·솔직 후기 바로 보기',
      noteJa: '施術写真や待ち時間の詳しい体験記をチェック',
      badgeKo: '무로그인',
      badgeJa: '登録不要',
      url: byId.get('naver_blog')!.url
    },
    {
      id: 'naver_place',
      labelKo: '네이버 지도 방문자 리뷰',
      labelJa: 'NAVERマップ 現地レビュー',
      noteKo: '영수증 인증 방문자 평점·최근 리뷰 바로 확인',
      noteJa: '韓国ローカル訪問者の最新写真・評価を確認',
      badgeKo: '무로그인',
      badgeJa: '登録不要',
      url: byId.get('naver_place')!.url
    },
    {
      id: 'google_reviews',
      labelKo: '구글 지도 별점·실후기',
      labelJa: 'Googleマップ 日本語口コミ',
      noteKo: '국내외 방문자 평점과 최근 솔직 후기 확인',
      noteJa: '星評価と日本語訳つき実体験をそのまま閲覧',
      badgeKo: '무로그인',
      badgeJa: '登録不要',
      url: byId.get('google_reviews')!.url
    },
    {
      id: 'naver_cafe',
      labelKo: '네이버 카페 실제 후기',
      labelJa: 'NAVERカフェ本音口コミ',
      noteKo: '커뮤니티 실방문 후기·비추 키워드 검색',
      noteJa: '韓国コミュニティの本音口コミを検索',
      badgeKo: '바로 검색',
      badgeJa: '登録不要',
      url: byId.get('naver_cafe')!.url
    }
  ];
}

/**
 * Enhances community review links for the web UI:
 * - Points JA GangnamUnni links to `/jp/hospitals` instead of Korean web UI
 * - Labels login requirements honestly (`no_login` / `partial_login` / `login_required`)
 * - Orders zero-login sources first in Japanese mode
 */
export function buildEnhancedCommunityReviewLinks(
  place: PlaceItem,
  lang: Lang
): EnhancedCommunityReviewLink[] {
  const jaBrandName = place.brandJa.replace(/\s*\([^)]*\)/, '').trim();
  const base = buildCommunityReviewLinks(place, lang);

  const enhanced: EnhancedCommunityReviewLink[] = base.map((item) => {
    if (item.id === 'gangnamunni') {
      return {
        ...item,
        labelJa: 'カンナムオンニ 日本語版',
        noteKo: '평점·후기수 확인 가능 (본문 열람 시 앱 로그인)',
        noteJa: '評価・件数は閲覧可（詳しい本文は要ログイン）',
        url:
          lang === 'ja'
            ? `https://www.gangnamunni.com/jp/hospitals?q=${encodeURIComponent(jaBrandName)}`
            : item.url,
        accessMode: 'partial_login'
      };
    }
    if (item.id === 'tribeau') {
      return {
        ...item,
        accessMode: 'no_login'
      };
    }
    if (item.id === 'x_realtime') {
      return {
        ...item,
        labelJa: 'X(Twitter) 本音ポスト検索',
        noteJa: 'Yahoo!リアルタイム検索でXログイン不要のまま確認',
        accessMode: 'no_login'
      };
    }
    if (item.id === 'threads') {
      return {
        ...item,
        labelKo: 'Threads(스레드) 실시간 후기',
        labelJa: 'Threads 最新口コミ検索',
        noteKo: '방문 후기·비추 글 확인 (비로그인·시크릿 모드는 제한 가능)',
        noteJa: '現地利用者の率直な投稿（未ログイン時は閲覧制限あり）',
        requiresLogin: true,
        accessMode: 'login_required'
      };
    }
    return {
      ...item,
      labelKo: 'X(트위터) 직접 검색',
      labelJa: 'X(Twitter) 直接検索',
      accessMode: 'login_required'
    };
  });

  if (lang === 'ja') {
    const rank: Record<ReviewAccessMode, number> = {
      no_login: 0,
      partial_login: 1,
      login_required: 2
    };
    return [...enhanced].sort((a, b) => rank[a.accessMode] - rank[b.accessMode]);
  }

  return enhanced;
}
