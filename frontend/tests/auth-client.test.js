const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'auth-client.js'), 'utf8');
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
function fakeSdk(initialSession = null) {
  let session = initialSession;
  let listener = null;
  let verify = null;
  const calls = [];
  const auth = {
    getSession: async () => ({ data: { session } }),
    signInWithPassword: async input => { calls.push(['login', input]); session = { access_token: 'fake-access-token', user: { email: input.email } }; return { data: { session } }; },
    signUp: async input => { calls.push(['signup', input]); verify = async ({ token }) => token === '123456' ? ({ data: { session: session = { access_token: 'fake-access-token', user: { email: input.email } } } }) : ({ error: { code: 'invalid' } }); return { data: { verifyOtp: verify } }; },
    signOut: async () => { calls.push(['signout']); session = null; return { data: {} }; },
    onAuthStateChange: callback => { listener = callback; return { data: { subscription: { unsubscribe: () => { listener = null; } } } }; },
  };
  return { sdk: { init: input => { calls.push(['init', input]); return { auth }; } }, calls, emit: (event, nextSession, info = null) => listener && listener(event, nextSession, info) };
}

test('restore, login, signup verification and transient access token follow the CloudBase v3 boundary', async () => {
  const { auth, values } = load(); const fake = fakeSdk();
  const client = auth.createAuthClient(configured(), { sdk: fake.sdk });
  assert.equal((await client.initialize()).status, 'unauthenticated');
  await client.signIn('tea@example.com', 'Password1');
  assert.equal(client.getState().status, 'authenticated');
  assert.equal(await client.getAccessToken(), 'fake-access-token');
  assert.equal(JSON.stringify(client.getState()).includes('fake-access-token'), false);
  await client.startSignUp('new@example.com', 'Password1');
  await client.verifySignUp('123456');
  assert.equal(client.getState().email, 'new@example.com');
  assert.equal([...values.values()].join(''), '');
  assert.deepEqual(JSON.parse(JSON.stringify(fake.calls[0])), ['init', { env: 'env-test', region: 'ap-shanghai', accessKey: 'public-key' }]);
  assert.equal(fake.calls.some(item => JSON.stringify(item).includes('Password1')), true);
});

test('OTP failures, malformed SDK responses and signout fail closed without exporting a session', async () => {
  const { auth } = load(); const fake = fakeSdk();
  const client = auth.createAuthClient(configured(), { sdk: fake.sdk });
  await client.initialize(); await client.startSignUp('tea@example.com', 'Password1');
  await assert.rejects(client.verifySignUp('bad'), error => error.code === 'verification_invalid');
  await client.signOut();
  assert.equal(client.getState().status, 'unauthenticated');
  const malformed = auth.createAuthClient(configured(), { sdk: { init: () => ({ auth: { getSession: async () => ({ data: {} }) } }) } });
  assert.deepEqual(JSON.parse(JSON.stringify(await malformed.initialize())), { status: 'error', email: null, errorCode: 'auth_sdk_unavailable' });
});

test('CloudBase v3 lifecycle signature ignores token refresh and blocks a required-auth subscriber on signed out', async () => {
  const { auth } = load(); const fake = fakeSdk({ access_token: 'fake-access-token', user: { email: 'tea@example.com' } });
  const client = auth.createAuthClient(configured(), { sdk: fake.sdk });
  await client.initialize();
  let businessReady = true;
  client.subscribe(next => { if (next.status === 'unauthenticated') businessReady = false; });
  const refreshed = { access_token: 'refreshed-fake-token', user: { email: 'tea@example.com' } };
  fake.emit('TOKEN_REFRESHED', refreshed, null);
  assert.equal(client.getState().status, 'authenticated');
  assert.equal(businessReady, true);
  fake.emit('SIGNED_OUT', null, null);
  assert.equal(client.getState().status, 'unauthenticated');
  assert.equal(businessReady, false);
});

test('callable app.auth remains a compatibility fallback after the v3 object path', async () => {
  const { auth } = load(); const fake = fakeSdk();
  const client = auth.createAuthClient(configured(), { sdk: { init: () => ({ auth: () => ({
    getSession: async () => ({ data: { session: null } }), signInWithPassword: async () => ({ error: {} }), signUp: async () => ({ error: {} }), signOut: async () => ({ data: {} }), onAuthStateChange: () => ({ data: {} }),
  }) }) } });
  assert.equal((await client.initialize()).status, 'unauthenticated');
});

test('auth client defensively accepts only the CloudBase region allowlist', async () => {
  const { auth } = load();
  for (const region of ['ap-shanghai', 'ap-guangzhou', 'ap-singapore']) {
    const client = auth.createAuthClient({ ...configured(), region }, { sdk: fakeSdk().sdk });
    assert.equal((await client.initialize()).status, 'unauthenticated');
  }
  const rejected = auth.createAuthClient({ ...configured(), region: 'ap-unknown' }, { sdk: fakeSdk().sdk });
  assert.deepEqual(JSON.parse(JSON.stringify(await rejected.initialize())), { status: 'error', email: null, errorCode: 'auth_not_configured' });
});

test('a relevant lifecycle error fails closed without exposing CloudBase details', async () => {
  const { auth } = load(); const fake = fakeSdk({ access_token: 'fake-access-token', user: { email: 'tea@example.com' } });
  const client = auth.createAuthClient(configured(), { sdk: fake.sdk });
  await client.initialize();
  fake.emit('SIGNED_IN', null, { error: { token: 'not-exposed' } });
  assert.deepEqual(JSON.parse(JSON.stringify(client.getState())), { status: 'error', email: null, errorCode: 'auth_state_change_failed' });
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
