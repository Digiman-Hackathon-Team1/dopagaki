/**
 * web 데이터를 에이전트용 JSON 스냅샷으로 내보낸다(ADR 0001).
 *
 * 실행: `pnpm --filter @repo/api export:web-data` (Node 22.18 이상에서 타입 제거로 바로 실행)
 *
 * 만드는 파일
 * - src/api/domain/data/web-snapshot.json: 에이전트가 읽는 병원·가격·환율 데이터.
 *   prices.json에 web 코드에만 있는 값(회복 기간, 바늘 여부, AI 평점, 고민별 시술표, 예약 채널,
 *   가격표 증빙)을 덧붙인다. 예약 채널과 가격표 증빙은 web 화면이 쓰는 함수(getClinicChannels,
 *   getOfficialMenuProof)의 결과를 그대로 넣는다.
 * - tests/fixtures/calc-expected.json: web calc.ts로 계산한 기대값. calc.py 대조 테스트(설계 13장)에 쓴다.
 * - tests/fixtures/inquiry-expected.json: web buildInquiryMessages로 만든 예약 문의 문장. inquiry.py 대조 테스트에 쓴다.
 * - tests/fixtures/order-sheet-expected.json: web buildOrderSheetMessages로 만든 접수 메모. inquiry.py 대조 테스트에 쓴다.
 *
 * web 파일은 읽기만 한다. web 데이터나 환율이 바뀌면 이 스크립트를 다시 실행한다.
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AreaId, GeoPoint, NaverFxSnapshot, PriceDataset, SortId } from '../../web/src/types.ts';
import { buildRankedRows } from '../../web/src/lib/calc.ts';
import { getClinicAiRating } from '../../web/src/lib/ai-rating.ts';
import { CONCERN_MENUS, DOWNTIME_MAX_DAYS, NEEDLE_MENUS } from '../../web/src/lib/treatment-rules.ts';
import { getClinicChannels } from '../../web/src/data/clinic-channels.ts';
import { getOfficialMenuProof } from '../../web/src/data/official-proofs.ts';
import {
  buildInquiryMessages,
  buildOrderSheetMessages,
  computeClinicBundle,
  ORDER_GUARD_OPTIONS,
  type InquiryFormInput,
  type OrderGuardOption
} from '../../web/src/lib/clinic-helpers.ts';
import type { PlaceItem } from '../../web/src/types.ts';

const apiDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const webData = path.resolve(apiDir, '../web/src/data');

const dataset: PriceDataset = JSON.parse(fs.readFileSync(path.join(webData, 'prices.json'), 'utf8'));
const fx: NaverFxSnapshot = JSON.parse(fs.readFileSync(path.join(webData, 'naver-fx.json'), 'utf8'));

// web App.tsx와 같은 환율: 네이버 환율 스냅샷, 없으면 데이터 기본값.
const krwPer100Jpy = fx.krwPer100Jpy || dataset.meta.defaultKrwPer100Jpy;

for (const menu of dataset.menus) {
  if (!(menu.id in DOWNTIME_MAX_DAYS)) {
    throw new Error(`DOWNTIME_MAX_DAYS에 ${menu.id}가 없습니다`);
  }
}
const menuIds = new Set(dataset.menus.map((menu) => menu.id));
for (const [concern, ids] of Object.entries(CONCERN_MENUS)) {
  for (const id of ids) {
    if (!menuIds.has(id)) throw new Error(`CONCERN_MENUS.${concern}의 ${id}가 prices.json에 없습니다`);
  }
}

// 스냅샷 값을 만든 원본(레포 루트 기준 경로). 이 중 하나라도 바뀌면 tests/test_catalog.py가
// 다시 내보내라고 실패한다. 환율(naver-fx.json)은 pnpm build마다 바뀌므로 넣지 않는다.
// 줄바꿈을 LF로 맞춘 뒤 해시해, Windows에서 CRLF로 받아도 같은 값이 나오게 한다.
const SOURCE_FILES = [
  'apps/web/src/data/prices.json',
  'apps/web/src/lib/calc.ts',
  'apps/web/src/lib/treatment-rules.ts',
  'apps/web/src/lib/ai-rating.ts',
  'apps/web/src/data/brand-reviews.ts',
  'apps/web/src/data/branch-reviews.ts',
  'apps/web/src/data/clinic-channels.ts',
  'apps/web/src/data/official-proofs.ts',
  'apps/web/src/lib/clinic-helpers.ts',
  'apps/api/scripts/export-web-snapshot.mts',
  // official-proofs.ts가 읽는 스크린샷 숨김 목록(PR #22에서 추가)
  'apps/web/src/data/proof-hidden.ts'
];
const repoRoot = path.resolve(apiDir, '../..');
const sources = Object.fromEntries(
  SOURCE_FILES.map((file) => {
    const text = fs.readFileSync(path.join(repoRoot, file), 'utf8').replace(/\r\n/g, '\n');
    return [file, createHash('sha256').update(text, 'utf8').digest('hex')];
  })
);

// 예약 채널(web ClinicBookingSheet와 같은 목록). 언어별로 고르는 링크(인스타그램, 웹 예약)만 다르고
// 채널 종류와 순서는 같으므로, 한 채널에 일본어·한국어 링크를 함께 담는다.
function channels(place: PlaceItem) {
  const ja = getClinicChannels(place.id, 'ja');
  const ko = getClinicChannels(place.id, 'ko');
  if (ja.items.map((c) => c.kind).join() !== ko.items.map((c) => c.kind).join()) {
    throw new Error(`${place.id}의 예약 채널 종류가 언어마다 다릅니다`);
  }
  return {
    channels: ja.items.map((c, i) => ({
      kind: c.kind,
      labelJa: c.labelJa,
      labelKo: c.labelKo,
      urlJa: c.url,
      urlKo: ko.items[i].url
    })),
    centralChannel: ja.isCentralChannel
  };
}

// 가격표 증빙(web 병원 화면과 같은 링크). 스크린샷이 가격과 맞는 경우에만 hasScreenshot이 true다.
function proofs(place: PlaceItem) {
  return Object.fromEntries(
    Object.keys(place.prices).map((menuId) => {
      const ja = getOfficialMenuProof(place, menuId, 'ja');
      const ko = getOfficialMenuProof(place, menuId, 'ko');
      if (ja.capturedDate !== ko.capturedDate || Boolean(ja.imageFile) !== Boolean(ko.imageFile)) {
        throw new Error(`${place.id}::${menuId}의 가격표 증빙이 언어마다 다릅니다`);
      }
      return [
        menuId,
        {
          urlJa: ja.priceUrl,
          urlKo: ko.priceUrl,
          domainJa: ja.domainLabel,
          domainKo: ko.domainLabel,
          capturedDate: ko.capturedDate,
          hasScreenshot: ko.imageFile !== null,
          matchedText: ko.imageFile !== null ? ko.matchedText : null
        }
      ];
    })
  );
}

const snapshot = {
  sources,
  meta: dataset.meta,
  fx: {
    krwPer100Jpy,
    dateLabel: fx.dateLabel,
    bankKo: fx.bankKo,
    bankJa: fx.bankJa,
    sourceUrl: fx.sourceUrl
  },
  areas: dataset.areas,
  // 고민 분류 → 시술 ID 목록(가장 직접적인 순). 설계 문서 9장의 고민별 시술표.
  concerns: Object.entries(CONCERN_MENUS).map(([id, menuIds]) => ({ id, menuIds })),
  menus: dataset.menus.map((menu) => ({
    ...menu,
    downtimeMaxDays: DOWNTIME_MAX_DAYS[menu.id],
    usesNeedle: NEEDLE_MENUS.has(menu.id)
  })),
  places: dataset.places.map((place) => {
    const rating = getClinicAiRating(place, dataset.places);
    return {
      ...place,
      aiRating: rating.overall,
      // 평점 근거: branch 지점 후기, brand 같은 브랜드 다른 지점 후기를 참고. limited는 확인된 후기가 적음.
      ratingScope: rating.reviewScope,
      ratingTier: rating.verificationTier,
      ...channels(place),
      proofs: proofs(place)
    };
  })
};

// 대조용 기준 위치: 홍대 지역 중심(서울 안), 도쿄역(서울 밖이라 web은 서울 시청 기준으로 계산).
const ORIGINS: Record<string, GeoPoint | null> = {
  none: null,
  hongdae: { lat: 37.5563, lng: 126.9228 },
  tokyo: { lat: 35.6812, lng: 139.7671 }
};
const SORTS: SortId[] = ['savings', 'distance', 'rating'];

// 금액 대조: 시술마다 순위표 전체 행.
const rows = dataset.menus.map((menu) => ({
  menuId: menu.id,
  origin: 'hongdae',
  rows: buildRankedRows(dataset, menu.id, 'all', 'savings', krwPer100Jpy, true, {
    origin: ORIGINS.hongdae
  }).map((row) => ({
    placeId: row.place.id,
    baseKrw: row.baseKrw,
    vatKrw: row.vatKrw,
    totalKrw: row.totalKrw,
    krTotalJpy: row.krTotalJpy,
    jpBenchmarkJpy: row.jpBenchmarkJpy,
    savingsJpy: row.savingsJpy,
    savingsPct: row.savingsPct,
    distanceMeters: row.distanceMeters,
    walkMinutes: row.walkMinutes,
    aiRating: row.aiRating
  }))
}));

// 순서 대조: 정렬 기준 × 기준 위치 × 일본어 필터 × 지역.
const orders = [];
for (const menuId of ['rejuran-2cc', 'shrink-300', 'aquapeel']) {
  for (const sort of SORTS) {
    for (const [originName, origin] of Object.entries(ORIGINS)) {
      for (const japaneseOnly of [false, true]) {
        for (const area of ['all', 'gangnam'] as AreaId[]) {
          const ranked = buildRankedRows(dataset, menuId, area, sort, krwPer100Jpy, true, {
            origin,
            japaneseOnly
          });
          orders.push({
            menuId,
            sort,
            origin: originName,
            japaneseOnly,
            area,
            placeIds: ranked.map((row) => row.place.id)
          });
        }
      }
    }
  }
}

// 이름 검색 대조: web 검색창(searchQuery)과 같은 결과인지 한 단어 검색으로 확인한다.
for (const menuId of ['rejuran-2cc', 'shrink-300']) {
  for (const searchQuery of ['밴스', 'バンス', '홍대입구', '강남', 'GANGNAM', '없는병원']) {
    const ranked = buildRankedRows(dataset, menuId, 'all', 'savings', krwPer100Jpy, true, {
      searchQuery
    });
    orders.push({
      menuId,
      sort: 'savings',
      origin: 'none',
      japaneseOnly: false,
      area: 'all',
      searchQuery,
      placeIds: ranked.map((row) => row.place.id)
    });
  }
}

const expected = { krwPer100Jpy, origins: ORIGINS, rows, orders };

// 접수 메모 대조: 부가세 별도·포함 병원, 시술 수(없음·하나·둘·병원에 가격 없음), 부탁 조합.
// 에이전트는 항상 부가세 포함·1회 기준이다(설계 4.7).
const vatExcludedPlace = dataset.places.find((p) => p.vatExcluded && 'shrink-300' in p.prices && 'rejuran-2cc' in p.prices);
const vatIncludedPlace = dataset.places.find((p) => !p.vatExcluded && 'shrink-300' in p.prices);
const noPotenzaPlace = dataset.places.find((p) => !('potenza' in p.prices));
if (!vatExcludedPlace || !vatIncludedPlace || !noPotenzaPlace) throw new Error('접수 메모 대조용 병원이 없습니다');
const GUARD_SETS: Array<Array<OrderGuardOption['id']>> = [
  ['no-upsell', 'total-first'],
  [],
  ORDER_GUARD_OPTIONS.map((g) => g.id).reverse(),
  ['one-hour']
];
const orderCases = [];
for (const place of [vatExcludedPlace, vatIncludedPlace, noPotenzaPlace]) {
  for (const menuIds of [[], ['shrink-300'], ['shrink-300', 'rejuran-2cc'], ['potenza']]) {
    for (const guards of GUARD_SETS) {
      const bundle = computeClinicBundle(place, dataset.menus, menuIds, 1, true, krwPer100Jpy, dataset.meta.vatRate);
      orderCases.push({
        placeId: place.id,
        menuIds,
        guards,
        krwPer100Jpy,
        vatRate: dataset.meta.vatRate,
        totalKrw: bundle.totalKrw,
        totalJpy: bundle.krTotalJpy,
        ...buildOrderSheetMessages(place, bundle, guards, true)
      });
    }
  }
}

// 예약 문의 문장 대조: 일본어 가능·불가 병원, 시술 수(없음·하나·둘·병원에 가격 없음), 입력 조합.
const japanesePlace = dataset.places.find((p) => p.japaneseSupport && 'shrink-300' in p.prices);
const koreanOnlyPlace = dataset.places.find((p) => !p.japaneseSupport && 'shrink-300' in p.prices);
const missingMenuPlace = dataset.places.find((p) => !('potenza' in p.prices));
if (!japanesePlace || !koreanOnlyPlace || !missingMenuPlace) throw new Error('문의 문장 대조용 병원이 없습니다');
const FORMS: InquiryFormInput[] = [
  { date: '', timeSlot: 'any', people: 1, firstVisit: true, guestName: '' },
  { date: '11/28', timeSlot: 'morning', people: 2, firstVisit: false, guestName: '' },
  { date: '12/3', timeSlot: 'afternoon', people: 9, firstVisit: true, guestName: '  ' },
  { date: '', timeSlot: 'evening', people: 0, firstVisit: true, guestName: 'YUKI TANAKA' }
];
const inquiryCases = [];
for (const place of [japanesePlace, koreanOnlyPlace, missingMenuPlace]) {
  for (const menuIds of [[], ['shrink-300'], ['rejuran-2cc', 'shrink-300'], ['potenza']]) {
    for (const form of FORMS) {
      const bundle = computeClinicBundle(
        place,
        dataset.menus,
        menuIds,
        1,
        true,
        krwPer100Jpy,
        dataset.meta.vatRate
      );
      inquiryCases.push({ placeId: place.id, menuIds, form, ...buildInquiryMessages(place, bundle, form) });
    }
  }
}

function write(relative: string, data: unknown, pretty = true) {
  const file = path.join(apiDir, relative);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${pretty ? JSON.stringify(data, null, 2) : JSON.stringify(data)}\n`);
  console.log(`wrote ${relative}`);
}

write('src/api/domain/data/web-snapshot.json', snapshot);
write('tests/fixtures/calc-expected.json', expected, false);
write('tests/fixtures/inquiry-expected.json', inquiryCases);
write('tests/fixtures/order-sheet-expected.json', orderCases);
