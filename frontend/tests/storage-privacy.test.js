const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadStore(initial = {}) {
  const values = new Map(Object.entries(initial));
  const window = {
    localStorage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    structuredClone,
  };
  window.window = window;
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '..', 'stores.js'), 'utf8'), window);
  return { window, values };
}

test('legacy merchant reply text is removed from returned and backing selection state on load', () => {
  const key = 'guancha.selection-bridge.v1';
  const question = '11111111-1111-4111-8111-111111111111';
  const legacy = JSON.stringify({ unknown_top: 'secret', merchantReplies: { [question]: { id: '22222222-2222-4222-8222-222222222222', status: 'submitted', raw_text: 'private chat', summary: 'private summary', candidate_id: '33333333-3333-4333-8333-333333333333', nested: { token: 'bad' } }, bad: [{ raw_text: 'array secret' }] }, reply: 'draft' });
  const { window, values } = loadStore({ [key]: legacy });
  const loaded = window.GuanchaStores.selectionBridge.load({ merchantReplies: {}, reply: '' });
  assert.deepEqual(JSON.parse(JSON.stringify(loaded.merchantReplies[question])), { id: '22222222-2222-4222-8222-222222222222', status: 'submitted', candidate_id: '33333333-3333-4333-8333-333333333333' });
  assert.equal(loaded.merchantReplies.bad, undefined);
  assert.equal(loaded.unknown_top, undefined);
  assert.equal(loaded.reply, '');
  const backing = values.get(key);
  assert.doesNotMatch(backing, /private chat|private summary|raw_text|summary|draft/);
});

test('future selection saves use a second merchant reply allowlist and reset clears it', () => {
  const { window, values } = loadStore();
  const store = window.GuanchaStores.selectionBridge;
  store.save({ merchantReplies: { '11111111-1111-4111-8111-111111111111': { id: '22222222-2222-4222-8222-222222222222', parse_status: 'answered', raw_text: 'secret', arbitrary: 'no' } }, reply: 'secret draft' });
  assert.doesNotMatch(values.get(store.key), /secret|raw_text|arbitrary/);
  window.GuanchaStores.clearAll();
  assert.equal(values.has(store.key), false);
});

test('account-boundary clear also removes the browser-global onboarding marker', () => {
  const { window, values } = loadStore({ guancha_onboarding_status: 'completed' });
  window.GuanchaStores.clearAll();
  assert.equal(values.has('guancha_onboarding_status'), false);
});

test('corrupt selection JSON is removed immediately and falls back safely', () => {
  const key = 'guancha.selection-bridge.v1';
  const { window, values } = loadStore({ [key]: '{broken' });
  const fallback = { candidates: [], merchantReplies: {}, reply: '' };
  assert.deepEqual(JSON.parse(JSON.stringify(window.GuanchaStores.selectionBridge.load(fallback))), fallback);
  assert.equal(values.has(key), false);
});

test('schema v3 serializes only recovery anchors from hostile nested selection trees', () => {
  const key = 'guancha.selection-bridge.v1';
  const questionId = '11111111-1111-4111-8111-111111111111';
  const candidateId = '33333333-3333-4333-8333-333333333333';
  const { window, values } = loadStore();
  window.GuanchaStores.selectionBridge.save({
    sessionId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    candidates: [{ id: 'local-candidate-123-0', serverCandidateId: candidateId, letter: 'A', extractionStatus: 'completed', name: 'secret', fields: { raw_text: 'merchant' }, extraction: { evidence: [{ text: 'reply' }] }, decision: { reasons: ['private'] }, riskFlags: ['private'], images: [{ id: 'local-image-123-abcd', serverImageId: '44444444-4444-4444-8444-444444444444', status: 'completed', previewUrl: 'data:image/png;base64,SECRET', file: { name: 'private.png', type: 'image/png' } }] }],
    followupQuestions: [{ reply: { text: 'secret' } }], selectionAnswer: { summary: 'secret' }, lastDecisionDelta: { added_facts: ['secret'] }, jobIds: { arbitrary: 'secret' }, need: { taste: 'user free text' },
    merchantReplyIds: { [questionId]: '22222222-2222-4222-8222-222222222222' },
    merchantReplies: { [questionId]: { id: '22222222-2222-4222-8222-222222222222', candidate_id: candidateId, status: 'submitted', raw_text: 'secret' } },
    unexpected: [{ summary: 'secret' }],
  });
  const persisted = JSON.parse(values.get(key));
  assert.equal(persisted.schemaVersion, 3);
  assert.equal(persisted.candidates.length, 1);
  assert.equal(persisted.candidates[0].serverCandidateId, candidateId);
  assert.equal(persisted.merchantReplyIds[questionId], '22222222-2222-4222-8222-222222222222');
  assert.doesNotMatch(values.get(key), /secret|raw_text|previewUrl|data:image|followupQuestions|selectionAnswer|lastDecisionDelta|jobIds|need|unexpected/);
});

test('schema v3 rejects arrays invalid UUIDs open statuses and excessive anchors then rewrites backing', () => {
  const key = 'guancha.selection-bridge.v1';
  const candidate = index => ({ serverCandidateId: `${String(index).padStart(8, '0')}-1111-4111-8111-111111111111`, letter: String.fromCharCode(65 + index), extractionStatus: index ? 'made-up' : 'queued', images: Array.from({ length: 3 }, (_, image) => ({ serverImageId: `${String(image + 20).padStart(8, '0')}-2222-4222-8222-222222222222`, status: 'queued' })) });
  const raw = JSON.stringify({ schemaVersion: 2, sessionId: 'not-a-uuid', candidates: Array.from({ length: 7 }, (_, index) => candidate(index)), merchantReplies: [], decisionVersionId: true, decisionStatus: 'private' });
  const { window, values } = loadStore({ [key]: raw });
  const loaded = window.GuanchaStores.selectionBridge.load({ candidates: [], merchantReplies: {} });
  assert.equal(loaded.schemaVersion, 3);
  assert.equal(loaded.sessionId, null);
  assert.equal(loaded.candidates.length, 5);
  assert.equal(loaded.candidates[0].images.length, 2);
  assert.equal(loaded.candidates[1].extractionStatus, undefined);
  assert.equal(loaded.decisionStatus, undefined);
  assert.equal(JSON.parse(values.get(key)).schemaVersion, 3);
});

test('ui session is a closed anchor store and cannot retain reply text', () => {
  const { window, values } = loadStore();
  window.GuanchaStores.uiSession.save({ screen: 'result', activeCandidateId: '33333333-3333-4333-8333-333333333333', o1: { tea: ['绿茶', 'private reply'] }, o2: { flavors: ['兰花', 'merchant raw text'], sweetness: 75 }, overlay: { raw_text: 'secret' }, brew: { impression: 'secret' } });
  const persisted = values.get(window.GuanchaStores.uiSession.key);
  assert.match(persisted, /33333333-3333-4333-8333-333333333333/);
  assert.match(persisted, /绿茶|兰花/);
  assert.doesNotMatch(persisted, /private|merchant|raw_text|secret|overlay|brew/);
});

test('ui session removes corrupt scalar and array backing before fallback', () => {
  const key = 'guancha.ui-session.v1';
  for (const raw of ['{broken', '0', '[]']) {
    const { window, values } = loadStore({ [key]: raw });
    assert.deepEqual(JSON.parse(JSON.stringify(window.GuanchaStores.uiSession.load({ screen: 'home' }))), { screen: 'home' });
    assert.equal(values.has(key), false);
  }
});

test('legacy is cleared even when a new selection bridge already exists', () => {
  const selectionKey = 'guancha.selection-bridge.v1';
  const legacyKey = 'guancha-prototype-v2';
  const { window, values } = loadStore({
    [selectionKey]: JSON.stringify({ schemaVersion: 3, sessionId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', candidates: [] }),
    [legacyKey]: JSON.stringify({ merchantReply: { raw_text: 'legacy secret' }, candidates: [{ fields: { summary: 'secret' } }] }),
  });
  window.GuanchaStores.migrateLegacy();
  assert.equal(values.has(legacyKey), false);
  assert.equal(JSON.parse(values.get(selectionKey)).sessionId, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
  assert.doesNotMatch([...values.values()].join(''), /legacy secret|summary/);
});

test('legacy projections populate missing safe stores then clear the raw key', () => {
  const legacyKey = 'guancha-prototype-v2';
  const { window, values } = loadStore({ [legacyKey]: JSON.stringify({
    sessionId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    candidates: [{ serverCandidateId: '33333333-3333-4333-8333-333333333333', letter: 'A', fields: { raw_text: 'secret' } }],
    warehouse: [{ id: 'tea-1', name: 'My tea', status: 'drinking', merchantReply: { text: 'secret' } }],
  }) });
  window.GuanchaStores.migrateLegacy();
  assert.equal(values.has(legacyKey), false);
  assert.match(values.get(window.GuanchaStores.selectionBridge.key), /aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/);
  assert.match(values.get(window.GuanchaStores.localPostPurchase.key), /My tea/);
  assert.doesNotMatch([...values.values()].join(''), /raw_text|"merchantReply":|secret/);
});

test('preference evidence save and load share a strict projection and rewrite backing', () => {
  const key = 'guancha.preference-evidence.v1';
  const valid = { id: '11111111-1111-4111-8111-111111111111', target_type: 'roast', target_value: 'heavy-roast', polarity: 'negative', confidence: 'low', issue_source: 'tea', source_brew_session_id: 'record-123', created_at: new Date().toISOString(), raw_text: 'merchant secret', nested: { summary: 'secret' } };
  const raw = JSON.stringify({ schemaVersion: 1, items: [valid, { ...valid, id: 'bad', source_brew_session_id: 'record-124' }], raw_text: 'top secret' });
  const { window, values } = loadStore({ [key]: raw });
  const loaded = window.GuanchaStores.preferenceEvidence.load({ items: [] });
  assert.equal(loaded.items.length, 1);
  assert.equal(loaded.items[0].target_value, 'heavy-roast');
  assert.doesNotMatch(values.get(key), /raw_text|nested|summary|secret/);
  window.GuanchaStores.preferenceEvidence.save({ items: [valid], merchant: { reply: 'secret' } });
  assert.doesNotMatch(values.get(key), /merchant|reply|secret/);
});

test('preference evidence removes corrupt and array backing values', () => {
  const key = 'guancha.preference-evidence.v1';
  for (const raw of ['{broken', '[]']) {
    const { window, values } = loadStore({ [key]: raw });
    assert.deepEqual(JSON.parse(JSON.stringify(window.GuanchaStores.preferenceEvidence.load({ items: [] }))), { items: [] });
    assert.equal(values.has(key), false);
  }
});

test('post-purchase store restores bounded warehouse journal and semantic history only', () => {
  const { window, values } = loadStore();
  const key = window.GuanchaStores.localPostPurchase.key;
  window.GuanchaStores.localPostPurchase.save({
    warehouse: [{ id: 'tea-1', name: 'User tea', type: '乌龙茶', status: 'drinking', records: 1, facts: ['待补充', 'merchant raw reply'], risks: ['origin_claim_conflict', 'merchant-raw-reply', 'light-roast'], risk_flags: ['season_claim_conflict', 'merchant-raw-reply', 'light-roast'], merchantReply: { raw_text: 'secret' } }],
    journalRecords: [{ id: 'record-123', date: '2026-08-13', teaId: 'tea-1', infusions: [{ number: 1, suggested: 10, actual: 11, reply: 'secret' }], plan: { ware: '盖碗', water: '110 ml', unknown: 'secret' }, feedback: { taste: '喜欢', impression: 'my note', merchant: { summary: 'secret' } }, feedbackAnalysis: { text: 'secret' } }],
    history: [{ date: '08.13', recommended_candidate_id: '33333333-3333-4333-8333-333333333333', recommended_candidate_label: 'A', selected_candidate_id: '44444444-4444-4444-8444-444444444444', selected_candidate_label: 'B', purpose: 'private Need', selected_candidate_name: 'private name', selectionAnswer: { summary: 'secret' } }],
    merchantReplies: [{ raw_text: 'secret' }],
  });
  const loaded = window.GuanchaStores.localPostPurchase.load({ warehouse: [], journalRecords: [], history: [] });
  assert.equal(loaded.warehouse[0].name, 'User tea');
  assert.deepEqual(JSON.parse(JSON.stringify(loaded.warehouse[0].facts)), ['待补充']);
  assert.deepEqual(JSON.parse(JSON.stringify(loaded.warehouse[0].risks)), ['origin_claim_conflict']);
  assert.deepEqual(JSON.parse(JSON.stringify(loaded.warehouse[0].risk_flags)), ['season_claim_conflict']);
  assert.equal(loaded.journalRecords[0].feedback.impression, 'my note');
  assert.deepEqual(JSON.parse(JSON.stringify(loaded.history[0])), { date: '08.13', recommended_candidate_id: '33333333-3333-4333-8333-333333333333', selected_candidate_id: '44444444-4444-4444-8444-444444444444', recommended_candidate_label: 'A', selected_candidate_label: 'B' });
  assert.doesNotMatch(values.get(key), /merchant|raw_text|selectionAnswer|private Need|private name|secret|feedbackAnalysis|unknown/);
});
