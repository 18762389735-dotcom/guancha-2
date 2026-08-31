const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const syncSource = fs.readFileSync(path.join(root, 'post-purchase-sync.js'), 'utf8');
const apiSource = fs.readFileSync(path.join(root, 'api-client.js'), 'utf8');
const storesSource = fs.readFileSync(path.join(root, 'stores.js'), 'utf8');

const teaId = '11111111-1111-4111-8111-111111111111';
const journalId = '22222222-2222-4222-8222-222222222222';

function loadSync() {
  const window = { structuredClone };
  window.window = window;
  vm.runInNewContext(syncSource, window, { filename: 'post-purchase-sync.js' });
  return window.GuanchaPostPurchaseSync;
}

function loadApi(options = {}) {
  const window = { crypto: require('node:crypto').webcrypto, setTimeout, clearTimeout };
  window.window = window;
  vm.runInNewContext(apiSource, window, { filename: 'api-client.js' });
  return window.GuanchaApi.createApiClient({
    clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f',
    getAccessToken: async () => 'fake-access-token',
    ...options,
  });
}

function loadStores() {
  const values = new Map();
  const window = {
    localStorage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    structuredClone,
  };
  window.window = window;
  vm.runInNewContext(storesSource, window, { filename: 'stores.js' });
  return window.GuanchaStores;
}

function serverTea(name = '云端乌龙') {
  return {
    id: teaId, name, tea_category: '乌龙茶', tea_subtype: null, origin: null,
    roast_or_style: null, aroma: '兰花', status: 'drinking', source_type: 'manual',
    selection_session_id: null, candidate_id: null, extraction_version_id: null,
    decision_version_id: null, facts: ['待补充'], risks: [], risk_flags: [],
    joined_at: '2026-08-31T00:00:00Z', revision: 1,
    created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
  };
}

function serverJournal() {
  return {
    id: journalId, tea_id: teaId, brewed_on: '2026-08-31',
    infusions: [{ number: 1, suggested: 10, actual: 11 }],
    plan: { ware: '盖碗', water: '110 ml', grams: '5 g', temp: '95℃' },
    feedback: { taste: '喜欢', strength: '刚好', tags: ['清爽'], aroma: [], score: 4, advanced: {} },
    suggestion: '保持本次参数', revision: 1,
    created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
  };
}

test('authenticated post-purchase API methods use bearer-only transport', async () => {
  const requests = [];
  const api = loadApi({ transport: request => { requests.push(request); return Promise.resolve({ ok: true, body: [] }); } });
  await api.getMyWarehouse();
  await api.putMyWarehouseTea(teaId, { name: '茶', status: 'drinking', source_type: 'manual', facts: [], risks: [], risk_flags: [] }, 0);
  await api.getMyBrewJournal();
  await api.putMyBrewJournalEntry(journalId, { tea_id: teaId, brewed_on: '2026-08-31', infusions: [], plan: {}, feedback: {}, suggestion: null }, 0);
  assert.deepEqual(requests.map(item => [item.method, item.path]), [
    ['GET', '/api/v1/me/warehouse'],
    ['PUT', `/api/v1/me/warehouse/${teaId}`],
    ['GET', '/api/v1/me/brew-journal'],
    ['PUT', `/api/v1/me/brew-journal/${journalId}`],
  ]);
  for (const request of requests) {
    assert.equal(request.headers.Authorization, 'Bearer fake-access-token');
    assert.equal(request.headers['X-Client-Id'], undefined);
  }
});

test('journal payload keeps only server plan fields', () => {
  const sync = loadSync();
  const payload = sync.toJournalPayload({
    id: journalId,
    teaId,
    date: '2026-08-31',
    infusions: [],
    plan: { ware: '盖碗', water: '110 ml', grams: '5 g', temp: '95℃', seconds: 10, unexpected: 'x' },
    feedback: {},
  });
  assert.deepEqual(JSON.parse(JSON.stringify(payload.plan)), { ware: '盖碗', water: '110 ml', grams: '5 g', temp: '95℃' });
  assert.deepEqual(Object.keys(payload.plan).sort(), ['grams', 'temp', 'ware', 'water']);
});

test('server-empty warehouse and journal replace old local cache without import', async () => {
  const sync = loadSync();
  const state = { warehouse: [{ id: 'tea-1', name: '旧本地茶' }], journalRecords: [{ id: 'record-1', teaId: 'tea-1', date: '2026-08-01' }], selectedTeaId: 'tea-1' };
  let saves = 0;
  const result = await sync.hydrate({ api: { getMyWarehouse: async () => [], getMyBrewJournal: async () => [] }, state, saveLocal: () => { saves += 1; } });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { warehouseLoaded: true, journalLoaded: true });
  assert.deepEqual(state.warehouse, []);
  assert.deepEqual(state.journalRecords, []);
  assert.equal(state.selectedTeaId, null);
  assert.equal(saves, 1);
});

test('server resources hydrate into the existing presentation shape and UUIDs survive cache sanitization', async () => {
  const sync = loadSync();
  const stores = loadStores();
  const state = { warehouse: [], journalRecords: [], selectedTeaId: null };
  await sync.hydrate({ api: { getMyWarehouse: async () => [serverTea()], getMyBrewJournal: async () => [serverJournal()] }, state, saveLocal: () => stores.localPostPurchase.save(state) });
  assert.equal(state.warehouse[0].id, teaId);
  assert.equal(state.warehouse[0].records, 1);
  assert.equal(state.journalRecords[0].id, journalId);
  const restored = stores.localPostPurchase.load({ warehouse: [], journalRecords: [] });
  assert.equal(restored.warehouse[0].id, teaId);
  assert.equal(restored.journalRecords[0].id, journalId);
  assert.equal(restored.journalRecords[0].teaId, teaId);
});

test('successful writes replace local state with canonical server revisions', async () => {
  const sync = loadSync();
  const state = { warehouse: [], journalRecords: [] };
  let saves = 0;
  const tea = sync.normalizeWarehouseTea(serverTea());
  const record = sync.normalizeJournalEntry(serverJournal());
  const canonicalTea = { ...serverTea('服务端名称'), revision: 2 };
  const canonicalJournal = { ...serverJournal(), revision: 2, suggestion: '服务端建议' };
  const api = {
    putMyWarehouseTea: async () => canonicalTea,
    putMyBrewJournalEntry: async () => canonicalJournal,
  };
  const savedTea = await sync.persistWarehouseTea({ api, state, tea, expectedRevision: 1, saveLocal: () => { saves += 1; } });
  const savedJournal = await sync.persistJournalEntry({ api, state, record, expectedRevision: 1, saveLocal: () => { saves += 1; } });
  assert.equal(savedTea.revision, 2);
  assert.equal(savedJournal.revision, 2);
  assert.equal(state.warehouse[0].name, '服务端名称');
  assert.equal(state.journalRecords[0].suggestion, '服务端建议');
  assert.equal(saves, 2);
});

test('revision conflict refetches authoritative collections and never silently overwrites', async () => {
  const sync = loadSync();
  const state = { warehouse: [sync.normalizeWarehouseTea(serverTea())], journalRecords: [] };
  let fetches = 0;
  let conflicts = 0;
  const error = Object.assign(new Error('conflict'), { code: 'warehouse_revision_conflict' });
  const result = await sync.persistWarehouseTea({
    api: {
      putMyWarehouseTea: async () => { throw error; },
      getMyWarehouse: async () => { fetches += 1; return [{ ...serverTea('其他设备名称'), revision: 3 }]; },
      getMyBrewJournal: async () => [],
    },
    state, tea: state.warehouse[0], expectedRevision: 1, saveLocal: () => {}, onConflict: () => { conflicts += 1; },
  });
  assert.equal(result, null);
  assert.equal(fetches, 1);
  assert.equal(conflicts, 1);
  assert.equal(state.warehouse[0].name, '其他设备名称');
  assert.equal(state.warehouse[0].revision, 3);
});

test('cloud hydration failure reports a visible sync error and does not preserve stale state as authoritative', async () => {
  const sync = loadSync();
  const state = { warehouse: [{ id: 'tea-1' }], journalRecords: [{ id: 'record-1' }] };
  const codes = [];
  const result = await sync.hydrate({ api: { getMyWarehouse: async () => { throw Object.assign(new Error('offline'), { code: 'network_unavailable' }); }, getMyBrewJournal: async () => [] }, state, onError: code => codes.push(code) });
  assert.equal(result.warehouseLoaded, false);
  assert.deepEqual(state.warehouse, [{ id: 'tea-1' }]);
  assert.deepEqual(codes, ['network_unavailable']);
});
