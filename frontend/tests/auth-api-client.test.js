const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'api-client.js'), 'utf8');
const clientId = 'd3ac0eb0-6436-4d48-a3cc-6f0d9f171a0f';

function client(options = {}) {
  const window = { crypto: require('node:crypto').webcrypto, setTimeout, clearTimeout };
  window.window = window;
  vm.runInNewContext(source, window, { filename: 'api-client.js' });
  return window.GuanchaApi.createApiClient({ clientId, ...options });
}

test('required authenticated Selection transport sends bearer without X-Client-Id', async () => {
  let request;
  const api = client({ authRequired: true, getAccessToken: async () => 'fake-access-token', transport: item => { request = item; return Promise.resolve({ ok: true, body: {} }); } });
  await api.getSelectionSession('session-a');
  assert.equal(request.headers.Authorization, 'Bearer fake-access-token');
  assert.equal(request.headers['X-Client-Id'], undefined);
});

test('required auth without a token never starts an anonymous Selection request', async () => {
  let calls = 0;
  const api = client({ authRequired: true, getAccessToken: async () => null, transport: () => { calls += 1; return Promise.resolve({ ok: true, body: {} }); } });
  await assert.rejects(api.getSelectionSession('session-a'), error => error.code === 'authentication_required');
  assert.equal(calls, 0);
});

test('a failing token supplier never downgrades to X-Client-Id', async () => {
  let calls = 0;
  const api = client({ authRequired: false, getAccessToken: async () => { throw new Error('offline'); }, transport: () => { calls += 1; return Promise.resolve({ ok: true, body: {} }); } });
  await assert.rejects(api.getSelectionSession('session-a'), error => error.code === 'authentication_required');
  assert.equal(calls, 0);
});

test('optional auth keeps anonymous Selection and Brew Feedback contracts when no token exists', async () => {
  let request;
  const api = client({ authRequired: false, getAccessToken: async () => null, transport: item => { request = item; return Promise.resolve({ ok: true, body: {} }); } });
  await api.getSelectionSession('session-a');
  assert.equal(request.headers['X-Client-Id'], clientId);
  assert.equal(request.headers.Authorization, undefined);
  await api.analyzeBrewFeedback({ brew_session_id: 'brew-a' }, '4d482cc6-3546-4859-9fbf-01063e12d234');
  assert.equal(request.headers['X-Client-Id'], clientId);
  assert.equal(request.headers.Authorization, undefined);
});

test('/me always requires a bearer while events do not receive one automatically', async () => {
  let request;
  const api = client({ authRequired: false, getAccessToken: async () => 'fake-access-token', transport: item => { request = item; return Promise.resolve({ ok: true, body: {} }); } });
  await api.getMe();
  assert.equal(request.headers.Authorization, 'Bearer fake-access-token');
  assert.equal(request.headers['X-Client-Id'], undefined);
  await api._request('POST', '/api/v1/events', '{}', { headers: { 'Content-Type': 'application/json' } });
  assert.equal(request.headers.Authorization, undefined);
  assert.equal(request.headers['X-Client-Id'], clientId);
});
