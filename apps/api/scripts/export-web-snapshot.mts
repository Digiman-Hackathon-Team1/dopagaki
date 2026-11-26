/**
 * web 데이터를 에이전트용 JSON 스냅샷으로 내보낸다(ADR 0001).
 *
 * 실행: `pnpm --filter @repo/api export:web-data` (Node 22.18 이상에서 타입 제거로 바로 실행)
 *
 * 만드는 파일
 * - src/api/domain/data/web-snapshot.json: 에이전트가 읽는 병원·가격·환율 데이터.
 *   prices.json에 web 코드에만 있는 값(회복 기간, 바늘 여부, AI 평점)을 덧붙인다.
 * - tests/fixtures/calc-expected.json: web calc.ts로 계산한 기대값. calc.py 대조 테스트(설계 13장)에 쓴다.
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
import { DOWNTIME_MAX_DAYS, NEEDLE_MENUS } from '../../web/src/lib/concierge.ts';

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

// 스냅샷 값을 만든 원본(레포 루트 기준 경로). 이 중 하나라도 바뀌면 tests/test_catalog.py가
// 다시 내보내라고 실패한다. 환율(naver-fx.json)은 pnpm build마다 바뀌므로 넣지 않는다.
// 줄바꿈을 LF로 맞춘 뒤 해시해, Windows에서 CRLF로 받아도 같은 값이 나오게 한다.
const SOURCE_FILES = [
  'apps/web/src/data/prices.json',
  'apps/web/src/lib/calc.ts',
  'apps/web/src/lib/concierge.ts',
  'apps/web/src/lib/ai-rating.ts',
  'apps/web/src/data/brand-reviews.ts',
  'apps/web/src/data/branch-reviews.ts',
  'apps/api/scripts/export-web-snapshot.mts'
];
const repoRoot = path.resolve(apiDir, '../..');
const sources = Object.fromEntries(
  SOURCE_FILES.map((file) => {
    const text = fs.readFileSync(path.join(repoRoot, file), 'utf8').replace(/\r\n/g, '\n');
    return [file, createHash('sha256').update(text, 'utf8').digest('hex')];
  })
);

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
  menus: dataset.menus.map((menu) => ({
    ...menu,
    downtimeMaxDays: DOWNTIME_MAX_DAYS[menu.id],
    usesNeedle: NEEDLE_MENUS.has(menu.id)
  })),
  places: dataset.places.map((place) => ({
    ...place,
    aiRating: getClinicAiRating(place, dataset.places).overall
  }))
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

const expected = { krwPer100Jpy, origins: ORIGINS, rows, orders };

function write(relative: string, data: unknown, pretty = true) {
  const file = path.join(apiDir, relative);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${pretty ? JSON.stringify(data, null, 2) : JSON.stringify(data)}\n`);
  console.log(`wrote ${relative}`);
}

write('src/api/domain/data/web-snapshot.json', snapshot);
write('tests/fixtures/calc-expected.json', expected, false);
