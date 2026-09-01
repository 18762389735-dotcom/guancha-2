const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.resolve(__dirname, '..', '..', 'app.js'), 'utf8');

function loadMerchantForm(context) {
  const start = appSource.indexOf('function replyNeedsClarification');
  const end = appSource.indexOf('async function prepareImageFiles', start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(
    `${appSource.slice(start, end)}; globalThis.appendMerchantReplyForm = appendMerchantReplyForm; globalThis.renderOverlay = renderOverlay;`,
    context,
    { filename: 'app.js' },
  );
}

function formContext(state) {
  const sheet = {
    form: null,
    querySelector: () => null,
    append(form) { this.form = form; },
  };
  const context = {
    state,
    currentCandidate: () => state.candidates[state.activeCandidate] || null,
    escapeHtml: value => String(value).replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character])),
    wordmark: () => '',
    icon: () => '',
    app: { querySelector: () => sheet },
    document: { createElement: () => ({ dataset: {}, className: '', innerHTML: '' }) },
    saveState: () => {},
    render: () => {},
  };
  return { context, sheet };
}

test('candidate with no questions gets no saved-reply claim or textarea', () => {
  const state = {
    activeCandidate: 0,
    questionStatus: 'completed',
    candidates: [
      { letter: 'A', serverCandidateId: 'server-a' },
      { letter: 'B', serverCandidateId: 'server-b' },
    ],
    followupQuestions: [{ id: 'question-b', candidate_id: 'server-b', question_text: '问题 B', field_key: 'season' }],
    merchantReplyIds: {},
    merchantReplies: {},
    overlay: 'ask',
    questionDecisionVersionId: null,
    decisionVersionId: null,
  };
  const { context, sheet } = formContext(state);
  loadMerchantForm(context);
  context.appendMerchantReplyForm();

  assert.equal(sheet.form, null);
  const overlay = context.renderOverlay();
  assert.equal((overlay.match(/候选 A 当前没有需要继续向商家确认的信息/g) || []).length, 1);
  assert.doesNotMatch(overlay, /商家回复已保存/);
});

function loadWarehouseFlow(context) {
  const saveStart = appSource.indexOf('function saveState');
  const saveEnd = appSource.indexOf('function apiNeed', saveStart);
  const evidenceStart = appSource.indexOf('function evidenceByField');
  const evidenceEnd = appSource.indexOf('function evidenceForDisplay', evidenceStart);
  const flowStart = appSource.indexOf('function serverResourceId');
  const flowEnd = appSource.indexOf('async function addManualWarehouseTea', flowStart);
  assert.ok(saveStart >= 0 && saveEnd > saveStart);
  assert.ok(evidenceStart >= 0 && evidenceEnd > evidenceStart);
  assert.ok(flowStart >= 0 && flowEnd > flowStart);
  vm.runInNewContext(
    `${appSource.slice(saveStart, saveEnd)}${appSource.slice(evidenceStart, evidenceEnd)}${appSource.slice(flowStart, flowEnd)}; globalThis.confirmWarehouseFromSelection = confirmWarehouseFromSelection;`,
    context,
    { filename: 'app.js' },
  );
}

function warehouseState() {
  return {
    screen: 'ownership',
    openDrink: '',
    activeCandidate: 0,
    activeCandidateId: null,
    o1: {},
    o2: { flavors: [] },
    ownershipChoice: 'bought',
    activeSelectionFlow: true,
    preferenceFlow: null,
    sessionId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    candidates: [{
      id: 'local-candidate-a',
      letter: 'A',
      name: '白水观音',
      type: '乌龙茶 · 清香型',
      serverCandidateId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      extractionVersionId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      extraction: { evidence_items: [] },
      riskFlags: [],
    }],
    reply: '',
    need: { taste: '', purpose: '', budget: '' },
    decisionVersionId: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    decisionJobId: null,
    selectionAnswer: null,
    followupQuestions: [],
    questionStatus: 'idle',
    questionDecisionVersionId: null,
    merchantReplyIds: {},
    merchantReplies: {},
    rejudgeJobId: null,
    lastDecisionDelta: null,
    deltaStatus: 'idle',
    jobIds: {},
    warehouse: [{
      id: 'tea-demo', name: '春日乌龙', type: '乌龙茶', aroma: '清香型', status: 'drinking',
      source: '选茶结果', records: 0, lastBrew: '还未泡过', facts: [], risks: [],
    }],
    journalRecords: [],
    history: [],
    selectedTeaId: null,
  };
}

function warehouseContext(state, { authenticated = false } = {}) {
  const persisted = { value: null };
  const toSnapshot = value => JSON.parse(JSON.stringify(value));
  const messages = [];
  const context = {
    state,
    currentCandidate: () => state.candidates[state.activeCandidate] || null,
    authenticatedPostPurchaseSyncAvailable: () => authenticated,
    apiClient: {},
    GuanchaApi: { createIdempotencyKey: () => 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee' },
    GuanchaPostPurchaseSync: authenticated ? {
      persistWarehouseTea: async ({ tea }) => {
        const canonical = { ...tea, id: 'ffffffff-ffff-4fff-8fff-ffffffffffff' };
        const index = state.warehouse.findIndex(item => item.id === canonical.id);
        if (index >= 0) state.warehouse.splice(index, 1, canonical);
        else state.warehouse.unshift(canonical);
        return canonical;
      },
    } : undefined,
    GuanchaStores: {
      uiSession: { save: () => {} },
      selectionBridge: { save: () => {} },
      localPostPurchase: { save: value => { persisted.value = toSnapshot(value); } },
    },
    syncActiveCandidate: () => {},
    productAnalytics: { track: () => {} },
    addSelectionHistory: () => {},
    completeSelectionFlow: () => { state.activeSelectionFlow = false; },
    showToast: message => messages.push(message),
    setScreen: screen => { state.screen = screen; },
  };
  return { context, persisted, messages };
}

test('local Selection handoff adds the selected candidate and survives warehouse/journal reload', async () => {
  const state = warehouseState();
  const { context, persisted } = warehouseContext(state);
  loadWarehouseFlow(context);

  await context.confirmWarehouseFromSelection();

  const added = state.warehouse.find(item => item.name === '白水观音');
  assert.ok(added);
  assert.equal(added.name, '白水观音');
  assert.equal(added.product_name, '白水观音');
  assert.equal(added.source_type, 'selection');
  assert.equal(state.selectedTeaId, added.id);
  assert.equal(state.warehouse.filter(item => item.name === '白水观音').length, 1);
  assert.equal(state.warehouse.some(item => item.name === '春日乌龙' && item.id === added.id), false);
  assert.equal(persisted.value.warehouse.some(item => item.name === '白水观音'), true);

  state.journalRecords.push({ id: 'record-local-1', teaId: added.id, date: '2026-08-04' });
  context.saveState();
  const reloaded = JSON.parse(JSON.stringify(persisted.value));
  assert.equal(reloaded.warehouse.some(item => item.name === '白水观音'), true);
  assert.equal(reloaded.journalRecords[0].teaId, added.id);
  assert.equal(reloaded.journalRecords[0].teaId, reloaded.warehouse.find(item => item.name === '白水观音').id);
});

test('authenticated Selection handoff keeps server persistence authoritative without a duplicate local insert', async () => {
  const state = warehouseState();
  const { context } = warehouseContext(state, { authenticated: true });
  loadWarehouseFlow(context);

  await context.confirmWarehouseFromSelection();

  assert.equal(state.warehouse.filter(item => item.name === '白水观音').length, 1);
  assert.equal(state.warehouse[0].id, 'ffffffff-ffff-4fff-8fff-ffffffffffff');
});

test('missing Selection candidate never writes the demo tea fallback', async () => {
  const state = warehouseState();
  state.candidates = [];
  const { context, messages } = warehouseContext(state);
  loadWarehouseFlow(context);

  await context.confirmWarehouseFromSelection();

  assert.equal(state.warehouse.some(item => item.name === '春日乌龙' && item.id !== 'tea-demo'), false);
  assert.deepEqual(messages, ['当前没有可加入茶仓的候选茶']);
});

function fitLabel(sensoryMatch, overallOrder) {
  const start = appSource.indexOf('function fitLabel');
  const end = appSource.indexOf('async function maybeStartSessionDecision', start);
  const context = { GuanchaAdapters: { sensoryNeedMatch: () => sensoryMatch } };
  vm.runInNewContext(`${appSource.slice(start, end)}; globalThis.fitLabel = fitLabel;`, context, { filename: 'app.js' });
  return context.fitLabel({ decision: { overall_order: overallOrder } }, { sensory_interpretations: [] });
}

test('fit label keeps overall ranking visible without claiming taste certainty', () => {
  assert.equal(fitLabel(0, 1), '综合条件下当前更优先');
  assert.equal(fitLabel(0, 2), '口味方向仍待体验确认');
  assert.equal(fitLabel(1, 1), '当前更接近你的方向');
});
