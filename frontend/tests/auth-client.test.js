const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'auth-client.js'), 'utf8');
const appSource = fs.readFileSync(path.resolve(__dirname, '..', '..', 'app.js'), 'utf8');
const userA = '11111111-1111-4111-8111-111111111111';
const userB = '22222222-2222-4222-8222-222222222222';

function load() {
  const values = new Map();
  const window = { localStorage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } };
  window.window = window;
  vm.runInNewContext(source, window, { filename: 'auth-client.js' });
  return { auth: window.GuanchaAuth, values };
}
function configured() { return { required: true, configured: true, provider: 'cloudbase', envId: 'env-test', region: 'ap-shanghai', publishableKey: 'public-key' }; }
function tokenPayload(token = 'fake-access-token', sub = userA) { return { access_token: token, expires_in: 3600, sub }; }
function transportFor({ restored = false, failures = {} } = {}) {
  let refreshCount = 0;
  const calls = [];
  const transport = async request => {
    calls.push(request);
    if (request.path === '/api/v1/auth/refresh') {
      refreshCount += 1;
      if (restored || refreshCount > 1) return { ok: true, body: tokenPayload('restored-access-token') };
      return { ok: false, status: 401, body: { error: { code: 'session_expired' } } };
    }
    if (failures[request.path]) return { ok: false, status: failures[request.path].status || 400, body: { error: { code: failures[request.path].code } } };
    if (request.path === '/api/v1/auth/sign-in') return { ok: true, body: tokenPayload() };
    if (request.path === '/api/v1/auth/register/start') return { ok: true, body: { verification_id: 'verification-1', expires_in: 600 } };
    if (request.path === '/api/v1/auth/register/complete') return { ok: true, body: tokenPayload('registered-access-token') };
    if (request.path === '/api/v1/auth/sign-out') return { ok: true, body: { status: 'signed_out' } };
    throw new Error(`unexpected path ${request.path}`);
  };
  return { transport, calls };
}

test('registration first step requests only email before verification', () => {
  const registerStart = appSource.indexOf("if (state?.authView === 'register')");
  const verifyStart = appSource.indexOf("if (state?.authView === 'verify')", registerStart);
  const registerView = appSource.slice(registerStart, verifyStart);
  assert.match(registerView, /name="email"/);
  assert.match(registerView, /获取验证码/);
  assert.doesNotMatch(registerView, /name="password"|name="confirm-password"/);

  const submitStart = appSource.indexOf("if (form.dataset.action === 'auth-signup')");
  const verifySubmitStart = appSource.indexOf("if (form.dataset.action === 'auth-verify')", submitStart);
  const signupSubmit = appSource.slice(submitStart, verifySubmitStart);
  assert.match(signupSubmit, /startSignUp\(email\)/);
  assert.doesNotMatch(signupSubmit, /data\.get\('password'\)|data\.get\('confirm-password'\)/);
});

test('BFF login, registration verification, transient token and no frontend token persistence', async () => {
  const { auth, values } = load(); const fake = transportFor();
  const client = auth.createAuthClient(configured(), { transport: fake.transport });
  assert.equal((await client.initialize()).status, 'unauthenticated');
  await client.signIn('tea@example.com', 'Password1');
  assert.equal(client.getState().status, 'authenticated');
  assert.equal(await client.getAccessToken(), 'fake-access-token');
  assert.equal(JSON.stringify(client.getState()).includes('fake-access-token'), false);
  await client.startSignUp('new@example.com');
  await client.verifySignUp('123456', 'Password1');
  assert.equal(client.getState().email, 'new@example.com');
  assert.equal([...values.values()].join(''), '');
  const signupStart = fake.calls.find(item => item.path.endsWith('/register/start'));
  assert.deepEqual(JSON.parse(JSON.stringify(signupStart.payload)), { email: 'new@example.com' });
  const complete = fake.calls.find(item => item.path.endsWith('/register/complete'));
  assert.deepEqual(JSON.parse(JSON.stringify(complete.payload)), { email: 'new@example.com', verification_id: 'verification-1', verification_code: '123456', password: 'Password1' });
});

test('reload restores through BFF refresh and refresh token is never returned to frontend state', async () => {
  const { auth } = load(); const fake = transportFor({ restored: true });
  const client = auth.createAuthClient(configured(), { transport: fake.transport });
  assert.equal((await client.initialize()).status, 'authenticated');
  assert.equal(await client.getAccessToken(), 'restored-access-token');
  assert.equal(JSON.stringify(client.getState()).includes('refresh'), false);
  assert.equal(fake.calls.filter(item => item.path.endsWith('/refresh')).length, 1);
});

test('BFF errors map to safe codes and malformed success fails closed', async () => {
  const { auth } = load();
  const invalid = transportFor({ failures: { '/api/v1/auth/sign-in': { code: 'invalid_credentials' } } });
  const client = auth.createAuthClient(configured(), { transport: invalid.transport });
  await client.initialize();
  await assert.rejects(client.signIn('tea@example.com', 'Password1'), error => error.code === 'invalid_credentials' && !error.message.includes('Password1'));
  const malformed = auth.createAuthClient(configured(), { transport: async () => ({ ok: true, body: { access_token: 'fake-access-token' } }) });
  assert.deepEqual(JSON.parse(JSON.stringify(await malformed.initialize())), { status: 'error', email: null, errorCode: 'auth_provider_unavailable' });
});

test('signout calls the BFF and immediately clears the in-memory session', async () => {
  const { auth } = load(); const fake = transportFor();
  const client = auth.createAuthClient(configured(), { transport: fake.transport });
  await client.initialize(); await client.signIn('tea@example.com', 'Password1');
  await client.signOut();
  assert.equal(client.getState().status, 'unauthenticated');
  const signout = fake.calls.find(item => item.path.endsWith('/sign-out'));
  assert.equal(signout.headers.Authorization, 'Bearer fake-access-token');
});

test('invalid refresh becomes signed out and does not silently use anonymous state', async () => {
  const { auth } = load(); const fake = transportFor();
  const client = auth.createAuthClient(configured(), { transport: fake.transport });
  assert.equal((await client.initialize()).status, 'unauthenticated');
  assert.equal(client.getState().status, 'unauthenticated');
});

test('auth client defensively accepts only the CloudBase region allowlist', async () => {
  const { auth } = load();
  for (const region of ['ap-shanghai', 'ap-guangzhou', 'ap-singapore']) {
    const fake = transportFor();
    const client = auth.createAuthClient({ ...configured(), region }, { transport: fake.transport });
    assert.equal((await client.initialize()).status, 'unauthenticated');
  }
  const rejected = auth.createAuthClient({ ...configured(), region: 'ap-unknown' }, { transport: transportFor().transport });
  assert.deepEqual(JSON.parse(JSON.stringify(await rejected.initialize())), { status: 'error', email: null, errorCode: 'auth_not_configured' });
});

test('account boundaries clear legacy browser state on first login and account switch, but not same-user reload', async () => {
  const { auth, values } = load(); let cleared = 0;
  const stores = { clearAll: async () => { cleared += 1; values.clear(); } };
  await auth.establishAccountBoundary({ userId: userA, stores, storage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } });
  assert.equal(cleared, 1);
  assert.equal(values.get(auth.ACCOUNT_MARKER_KEY), userA);
  const storage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  await auth.establishAccountBoundary({ userId: userA, stores, storage });
  assert.equal(cleared, 1);
  await auth.establishAccountBoundary({ userId: userB, stores, storage });
  assert.equal(cleared, 2);
  await auth.clearAccountBoundary({ stores, storage });
  assert.equal(cleared, 3);
  assert.equal(values.has(auth.ACCOUNT_MARKER_KEY), false);
});
