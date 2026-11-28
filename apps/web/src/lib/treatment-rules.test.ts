import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { PriceDataset } from '../types.ts';
import { CONCERN_MENUS, DOWNTIME_MAX_DAYS, NEEDLE_MENUS, treatmentMinutes } from './treatment-rules.ts';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dataset: PriceDataset = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../data/prices.json'), 'utf8')
);

test('treatment rules: every downtime, needle and concern entry refers to a real treatment', () => {
  const ids = new Set(dataset.menus.map((m) => m.id));
  for (const id of Object.keys(DOWNTIME_MAX_DAYS)) assert.ok(ids.has(id), id);
  for (const id of NEEDLE_MENUS) assert.ok(ids.has(id), id);
  for (const menuIds of Object.values(CONCERN_MENUS)) {
    assert.ok(menuIds.length > 0);
    for (const id of menuIds) assert.ok(ids.has(id), id);
  }
  assert.equal(Object.keys(DOWNTIME_MAX_DAYS).length, dataset.menus.length);
});

test('treatment rules: every treatment has a treatment time from its own note', () => {
  for (const menu of dataset.menus) {
    const min = treatmentMinutes(menu);
    assert.ok(min !== null && min > 0 && min <= 120, `${menu.id}: ${menu.leadTimeNoteJa}`);
    assert.ok(menu.leadTimeNoteKo.includes(String(min)), `${menu.id} JA/KO minutes differ`);
  }
  assert.equal(treatmentMinutes({ leadTimeNoteJa: '施術約40分・当日帰国可' }), 40);
});
