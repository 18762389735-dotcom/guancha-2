const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');

function browser() {
  const values = new Map();
  const window = {
    crypto: require('node:crypto').webcrypto,
    localStorage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
    FormData: global.FormData,
    setTimeout,
    clearTimeout,
    document: { hidden: false },
  };
  window.window = window;
  return window;
}

function load(window, filename) {
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), window, { filename });
}

test('need-card decoration does not intercept the editable Need control', () => {
  const styles = fs.readFileSync(path.join(root, '..', 'styles.css'), 'utf8');
  assert.match(styles, /\.need-card \.leaf-float\s*\{[^}]*pointer-events\s*:\s*none\s*;/s);
});

test('real-flow client uses only backend contracts for session, candidate, image, job and decision', async () => {
  const window = browser();
  load(window, path.join(root, 'api-client.js'));
  const calls = [];
  const client = window.GuanchaApi.createApiClient({
    clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f',
    transport: async item => {
      calls.push(item);
      return { ok: true, body: { id: 'server-id', image: { id: 'image-id' }, extraction_job: { id: 'job-id', status: 'queued' } } };
    },
  });
  const key = '4d482cc6-3546-4859-9fbf-01063e12d234';
  await client.createSelectionSession({ taste_text: 'floral' }, key);
  await client.createCandidate('session-id', { display_label: 'A', display_name: 'candidate' }, key);
  await client.uploadCandidateImage('candidate-id', new Blob(['image'], { type: 'image/png' }), key);
  await client.getJob('job-id');
  await client.getCurrentExtraction('candidate-id');
  await client.analyzeSelectionSession('session-id', key);
  await client.getCurrentDecision('session-id');
  assert.deepEqual(calls.map(call => call.path), [
    '/api/v1/selection-sessions',
    '/api/v1/selection-sessions/session-id/candidates',
    '/api/v1/candidates/candidate-id/images',
    '/api/v1/jobs/job-id',
    '/api/v1/candidates/candidate-id/current-extraction',
    '/api/v1/selection-sessions/session-id/analyze',
    '/api/v1/selection-sessions/session-id/current-decision',
  ]);
  assert.equal(calls[2].payload instanceof window.FormData, true);
  assert.equal(calls[0].headers['X-Client-Id'], 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f');
  assert.equal(calls[5].headers['Idempotency-Key'], key);
});

test('job poller ignores an obsolete job version instead of reporting a late result', async () => {
  const window = browser();
  window.GuanchaPublicConfig = { get: () => ({ pollInitialWindowMs: 1, pollInitialMs: 1, pollAfterInitialMs: 1, pollBackgroundMs: 1 }) };
  load(window, path.join(root, 'job-poller.js'));
  let updates = 0;
  window.GuanchaJobPoller.start({
    jobId: 'old-job', resourceId: 'candidate-id', versionId: 'old-job',
    fetchStatus: async () => ({ status: 'completed', extraction_version_id: 'old-version' }),
    getCurrentVersion: () => 'new-job',
    onUpdate: () => { updates += 1; },
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(updates, 0);
  assert.equal(window.GuanchaJobPoller.activeCount(), 0);
});

test('API client exposes backend and network failures as recoverable contract errors', async () => {
  const window = browser();
  load(window, path.join(root, 'api-client.js'));
  const forbidden = window.GuanchaApi.createApiClient({
    clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f',
    transport: async () => ({ ok: false, body: { error: { code: 'resource_not_owned', message: 'not yours' } } }),
  });
  await assert.rejects(() => forbidden.getJob('job-id'), error => error.code === 'resource_not_owned');
  const unconfigured = window.GuanchaApi.createApiClient({ clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f' });
  await assert.rejects(() => unconfigured.getJob('job-id'), error => error.code === 'api_not_configured');
});

test('real-flow client preserves server order for questions, rejudgement and post-purchase bridge calls', async () => {
  const window = browser();
  load(window, path.join(root, 'api-client.js'));
  const calls = [];
  const client = window.GuanchaApi.createApiClient({
    clientId: 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f',
    transport: async item => { calls.push(item); return { ok: true, body: [] }; },
  });
  const key = '4d482cc6-3546-4859-9fbf-01063e12d234';
  await client.getDecisionQuestions('decision-id');
  await client.generateDecisionQuestions('decision-id', key);
  await client.createMerchantReply('session-id', { decision_version_id: 'decision-id', followup_question_id: 'question-id', raw_text: 'merchant reply' }, key);
  await client.rejudgeMerchantReply('session-id', 'reply-id', key);
  await client.getDecisionDelta('delta-id');
  await client.analyzeBrewFeedback({ brew_session_id: 'local-brew-id' }, key);
  assert.deepEqual(calls.map(call => call.path), [
    '/api/v1/decision-versions/decision-id/questions',
    '/api/v1/decision-versions/decision-id/questions',
    '/api/v1/selection-sessions/session-id/merchant-replies',
    '/api/v1/selection-sessions/session-id/rejudge',
    '/api/v1/decision-deltas/delta-id',
    '/api/v1/brew-feedback/analyze',
  ]);
  assert.equal(calls[1].headers['Idempotency-Key'], key);
  assert.equal(calls[4].method, 'GET');
});

test('production frontend contains no provider key or evaluation fixture leakage', () => {
  const sources = [
    path.resolve(root, '..', 'app.js'),
    path.join(root, 'api-client.js'),
    path.join(root, 'adapters.js'),
    path.join(root, 'job-poller.js'),
  ].map(filename => fs.readFileSync(filename, 'utf8')).join('\n');
  assert.doesNotMatch(sources, /(?:MIMO|OPENAI)_API_KEY|VITE_MIMO_API_KEY/i);
  assert.doesNotMatch(sources, /(?:EVAL-|HOLDOUT-|PERSONA-|META-|golden|corrected_value|expected_bucket|expected_rank|blind-holdout|decision-eval)/i);
});

test('result view puts personal fit before evidence and keeps evidence provenance readable', () => {
  const source = fs.readFileSync(path.resolve(root, '..', 'app.js'), 'utf8');
  const fit = source.indexOf('<h3>为什么它更像 / 不像你会喜欢</h3>');
  const sensory = source.indexOf('<h3>这些专业信息可能意味着什么</h3>');
  const facts = source.indexOf('<h3>商品页目前能确认</h3>');
  assert.ok(fit >= 0 && sensory > fit && facts > sensory);
  assert.match(source, /\$\{escapeHtml\(item\.value\)\}<\/span><small>\$\{escapeHtml\(item\.basis\)\}<\/small>/);
});

test('completed empty questions unlock only their current decision and survive snapshot recovery', () => {
  const source = fs.readFileSync(path.resolve(root, '..', 'app.js'), 'utf8');
  assert.match(source, /snapshot\.question_generation_status === 'completed'\) state\.questionStatus = 'not-needed'/);
  assert.match(source, /state\.questionStatus === 'not-needed' && state\.questionDecisionVersionId === state\.decisionVersionId/);
  assert.match(source, /requiredQuestionIds\.size === 0\s*\? 'not-needed'/);
});

test('one merchant reply is bound to exactly one current question without fan-out', () => {
  const source = fs.readFileSync(path.resolve(root, '..', 'app.js'), 'utf8');
  const start = source.indexOf('async function submitMerchantReply(rawText)');
  const end = source.indexOf('async function refreshSelectionAnswer()', start);
  const implementation = source.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.equal((implementation.match(/apiClient\.createMerchantReply/g) || []).length, 1);
  assert.doesNotMatch(implementation, /extraQuestion|questions\.slice\(1\)/);
  assert.match(source, /const currentQuestion = merchantQuestions\(currentCandidate\(\)\)\.find/);
  assert.match(source, /对应：\$\{escapeHtml\(currentQuestion\.question\)\}/);
});

test('Need edits update the server before clearing stale decision and returning to candidates', () => {
  const source = fs.readFileSync(path.resolve(root, '..', 'app.js'), 'utf8');
  const start = source.indexOf('async function saveSelectionNeed(nextNeed)');
  const end = source.indexOf('function drinkGroup(', start);
  const implementation = source.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.match(implementation, /await GuanchaAdapters\.prepareNeedUpdate/);
  assert.ok(implementation.indexOf('await GuanchaAdapters.prepareNeedUpdate') < implementation.indexOf('Object.assign(state, transition)'));
  assert.match(implementation, /state\.screen = 'candidates'/);
});

test('active snapshot recovery uses server job status and preserves rejudge delta screen', () => {
  const source = fs.readFileSync(path.resolve(root, '..', 'app.js'), 'utf8');
  assert.match(source, /const recoveryScreen = GuanchaAdapters\.activeRecoveryScreen\(snapshot\)/);
  assert.match(source, /current_job_status/);
  assert.match(source, /state\.screen = recoveryScreen/);
  assert.doesNotMatch(source, /state\.screen === 'analysis' \|\| state\.screen === 'candidates'\) state\.screen = 'result'/);
});

test('result analytics is guarded by the screen candidate decision transition edge', () => {
  const source = fs.readFileSync(path.resolve(root, '..', 'app.js'), 'utf8');
  assert.match(source, /const edge = \[state\.screen, currentCandidate\(\)\?\.serverCandidateId \|\| currentCandidate\(\)\?\.id \|\| 'none', state\.decisionVersionId \|\| 'none'\]\.join/);
  assert.match(source, /if \(edge === lastResultAnalyticsEdge\) return false/);
  assert.match(source, /if \(action === 'slide-next'\) \{ slide\(1\); return trackResultView\(\); \}/);
});
