const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function browser() {
  const values = new Map();
  const window = {
    crypto: require('node:crypto').webcrypto,
    localStorage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    structuredClone,
    FormData: global.FormData,
    setTimeout,
    document: { hidden: false },
  };
  window.window = window;
  return { window, values };
}
function load(context, filename) {
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}
const root = path.resolve(__dirname, '..');

test('API client sends client and idempotency headers for creation requests', async () => {
  const { window } = browser();
  load(window, path.join(root, 'api-client.js'));
  let request;
  const client = window.GuanchaApi.createApiClient({ clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f', transport: item => { request = item; return Promise.resolve({ ok: true, body: { id: 'ok' } }); } });
  await client.createSelectionSession({ taste_text: 'floral' }, '4d482cc6-3546-4859-9fbf-01063e12d234', [{ source_brew_session_id: 'brew-1', confidence: 'low' }]);
  assert.equal(request.headers['X-Client-Id'], 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f');
  assert.equal(request.headers['Idempotency-Key'], '4d482cc6-3546-4859-9fbf-01063e12d234');
  assert.equal(request.path, '/api/v1/selection-sessions');
  assert.match(request.payload, /recent_preference_evidence/);
});

test('API client exposes the session decision contract without client-side scoring', async () => {
  const { window } = browser();
  load(window, path.join(root, 'api-client.js'));
  let request;
  const client = window.GuanchaApi.createApiClient({ clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f', transport: item => { request = item; return Promise.resolve({ ok: true, body: { id: 'job' } }); } });
  await client.analyzeSelectionSession('session-1', '4d482cc6-3546-4859-9fbf-01063e12d234');
  assert.equal(request.path, '/api/v1/selection-sessions/session-1/analyze');
  assert.equal(request.headers['Idempotency-Key'], '4d482cc6-3546-4859-9fbf-01063e12d234');
});

test('API client exposes generated questions without exposing value scores', async () => {
  const { window } = browser();
  load(window, path.join(root, 'api-client.js'));
  let request;
  const client = window.GuanchaApi.createApiClient({ clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f', transport: item => { request = item; return Promise.resolve({ ok: true, body: [] }); } });
  await client.generateDecisionQuestions('decision-1', '4d482cc6-3546-4859-9fbf-01063e12d234');
  assert.equal(request.path, '/api/v1/decision-versions/decision-1/questions');
  assert.equal(request.headers['Idempotency-Key'], '4d482cc6-3546-4859-9fbf-01063e12d234');
  await client.getDecisionQuestions('decision-1');
  assert.equal(request.path, '/api/v1/decision-versions/decision-1/questions');
  assert.equal(request.method, 'GET');
});

test('API client exposes merchant reply and asynchronous rejudgement contracts', async () => {
  const { window } = browser();
  load(window, path.join(root, 'api-client.js'));
  let request;
  const client = window.GuanchaApi.createApiClient({ clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f', transport: item => { request = item; return Promise.resolve({ ok: true, body: { id: 'reply' } }); } });
  const key = '4d482cc6-3546-4859-9fbf-01063e12d234';
  await client.createMerchantReply('session-1', { decision_version_id: 'v1', followup_question_id: 'q1', raw_text: 'light roast' }, key);
  assert.equal(request.path, '/api/v1/selection-sessions/session-1/merchant-replies');
  assert.equal(request.headers['Idempotency-Key'], key);
  await client.rejudgeMerchantReply('session-1', key);
  assert.equal(request.path, '/api/v1/selection-sessions/session-1/rejudge');
  assert.equal(request.payload, '{}');
  await client.getDecisionDelta('delta-1');
  assert.equal(request.path, '/api/v1/decision-deltas/delta-1');
});

test('API client submits brew feedback through the idempotent server bridge', async () => {
  const { window } = browser();
  load(window, path.join(root, 'api-client.js'));
  let request;
  const client = window.GuanchaApi.createApiClient({ clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f', transport: item => { request = item; return Promise.resolve({ ok: true, body: { attribution: 'uncertain' } }); } });
  const key = '4d482cc6-3546-4859-9fbf-01063e12d234';
  await client.analyzeBrewFeedback({ brew_session_id: 'brew-1', client_feedback_id: key }, key);
  assert.equal(request.path, '/api/v1/brew-feedback/analyze');
  assert.equal(request.headers['Idempotency-Key'], key);
  assert.match(request.payload, /brew_session_id/);
});

test('warehouse storage persists added MVP tea records', () => {
  const { window } = browser();
  load(window, path.join(root, 'stores.js'));
  window.GuanchaStores.localPostPurchase.save({ warehouse: [{ id: 'tea-1', product_name: '铁观音', extraction_version_id: '11111111-1111-4111-8111-111111111111' }] });
  const loaded = window.GuanchaStores.localPostPurchase.load({ warehouse: [] });
  assert.equal(loaded.warehouse[0].product_name, '铁观音');
  assert.equal(loaded.warehouse[0].extraction_version_id, '11111111-1111-4111-8111-111111111111');
});

test('preference evidence store safely deduplicates and bounds local feedback', () => {
  const { window } = browser();
  load(window, path.join(root, 'stores.js'));
  window.GuanchaStores.preferenceEvidence.save({ items: [
    { id: '11111111-1111-4111-8111-111111111111', target_type: 'roast', target_value: 'heavy-roast', polarity: 'negative', issue_source: 'tea', source_brew_session_id: 'brew-1', confidence: 'low', created_at: new Date().toISOString() },
    { id: '22222222-2222-4222-8222-222222222222', target_type: 'roast', target_value: 'heavy-roast', polarity: 'negative', issue_source: 'tea', source_brew_session_id: 'brew-1', confidence: 'low', created_at: new Date().toISOString() },
    { id: '33333333-3333-4333-8333-333333333333', target_type: 'roast', target_value: 'heavy-roast', polarity: 'negative', issue_source: 'tea', source_brew_session_id: 'brew-2', confidence: 'high', created_at: new Date().toISOString() },
  ] });
  const loaded = window.GuanchaStores.preferenceEvidence.load({ items: [] });
  assert.equal(loaded.items.length, 1);
  assert.equal(loaded.items[0].source_brew_session_id, 'brew-1');
});

test('job poller exposes a completed and a failed terminal state', async () => {
  const { window } = browser();
  window.GuanchaPublicConfig = { get: () => ({ pollInitialWindowMs: 1, pollInitialMs: 1, pollAfterInitialMs: 1, pollBackgroundMs: 1 }) };
  load(window, path.join(root, 'job-poller.js'));
  for (const status of ['completed', 'failed']) {
    const received = await new Promise(resolve => window.GuanchaJobPoller.start({
      jobId: `${status}-job`, resourceId: `${status}-candidate`, versionId: 'v1',
      fetchStatus: async () => ({ status }), getCurrentVersion: () => 'v1', onUpdate: resolve,
    }));
    assert.equal(received.status, status);
  }
});

test('job poller keeps a transport failure separate from a server Job failure', async () => {
  const { window } = browser();
  window.GuanchaPublicConfig = { get: () => ({ pollInitialWindowMs: 1, pollInitialMs: 1, pollAfterInitialMs: 1, pollBackgroundMs: 1 }) };
  load(window, path.join(root, 'job-poller.js'));
  let calls = 0;
  let transportErrors = 0;
  const received = await new Promise(resolve => window.GuanchaJobPoller.start({
    jobId: 'network-job', resourceId: 'network-candidate', versionId: 'v1',
    fetchStatus: async () => {
      calls += 1;
      if (calls === 1) throw Object.assign(new Error('offline'), { code: 'network_unavailable' });
      return { status: 'completed' };
    },
    getCurrentVersion: () => 'v1',
    onTransportError: () => { transportErrors += 1; },
    onUpdate: resolve,
  }));
  assert.equal(transportErrors, 1);
  assert.equal(received.status, 'completed');
});

test('job poller can cancel every active business poller for logout and account switches', () => {
  const { window } = browser();
  window.GuanchaPublicConfig = { get: () => ({ pollInitialWindowMs: 1000, pollInitialMs: 1000, pollAfterInitialMs: 1000, pollBackgroundMs: 1000 }) };
  load(window, path.join(root, 'job-poller.js'));
  window.GuanchaJobPoller.start({ jobId: 'job-a', resourceId: 'resource-a', versionId: 'v1', fetchStatus: async () => ({ status: 'processing' }), getCurrentVersion: () => 'v1', onUpdate: () => {} });
  assert.equal(window.GuanchaJobPoller.activeCount(), 1);
  window.GuanchaJobPoller.cancelAll();
  assert.equal(window.GuanchaJobPoller.activeCount(), 0);
});
