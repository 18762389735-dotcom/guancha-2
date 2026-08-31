const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const syncSource = fs.readFileSync(path.join(root, 'preference-sync.js'), 'utf8');
const apiSource = fs.readFileSync(path.join(root, 'api-client.js'), 'utf8');
const appSource = fs.readFileSync(path.resolve(root, '..', 'app.js'), 'utf8');

function loadSync() {
  const window = { structuredClone };
  window.window = window;
  vm.runInNewContext(syncSource, window, { filename: 'preference-sync.js' });
  return window.GuanchaPreferenceSync;
}

function profile(tea = []) {
  return {
    o1: { tea, coffee: [], milk: [], juice: [] },
    o2: { sweetness: 50, flavors: [] },
  };
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

test('P9-4A /me methods require bearer and do not send X-Client-Id', async () => {
  const requests = [];
  const api = loadApi({
    transport: request => {
      requests.push(request);
      return Promise.resolve({ ok: true, body: request.path.endsWith('/selection-sessions') ? [] : request.path.endsWith('/preference-evidence') ? [] : { profile: profile(), revision: 0, updated_at: null } });
    },
  });

  await api.getMyPreferences();
  await api.putMyPreferences(profile(['绿茶']), 0);
  await api.getMyPreferenceEvidence();
  await api.persistMyPreferenceEvidence([]);
  await api.listMySelectionSessions();

  assert.deepEqual(requests.map(request => [request.method, request.path]), [
    ['GET', '/api/v1/me/preferences'],
    ['PUT', '/api/v1/me/preferences'],
    ['GET', '/api/v1/me/preference-evidence'],
    ['PUT', '/api/v1/me/preference-evidence'],
    ['GET', '/api/v1/me/selection-sessions?limit=20'],
  ]);
  for (const request of requests) {
    assert.equal(request.headers.Authorization, 'Bearer fake-access-token');
    assert.equal(request.headers['X-Client-Id'], undefined);
  }
  assert.deepEqual(JSON.parse(requests[1].payload), { profile: profile(['绿茶']), expected_revision: 0 });
});

test('authenticated hydration lets server revision zero replace legacy local preferences', async () => {
  const sync = loadSync();
  const state = { o1: profile(['绿茶']).o1, o2: profile(['绿茶']).o2, preferenceRevision: 0 };
  let savedProfile = 0;
  let savedEvidence;
  const result = await sync.hydrate({
    api: {
      getMyPreferences: async () => ({ profile: profile(), revision: 0, updated_at: null }),
      getMyPreferenceEvidence: async () => [],
    },
    state,
    saveLocal: () => { savedProfile += 1; },
    saveEvidence: items => { savedEvidence = items; },
  });

  assert.deepEqual(JSON.parse(JSON.stringify(result)), { preferenceLoaded: true, evidenceLoaded: true });
  assert.deepEqual(state.o1, profile().o1);
  assert.deepEqual(state.o2, profile().o2);
  assert.equal(state.preferenceRevision, 0);
  assert.equal(savedProfile, 1);
  assert.deepEqual(savedEvidence, []);
});

test('successful cloud preference write updates state, revision and local cache', async () => {
  const sync = loadSync();
  const state = { o1: profile(['绿茶']).o1, o2: profile(['绿茶']).o2, preferenceRevision: 0 };
  let saved = 0;
  const notifications = [];
  const result = await sync.persistProfile({
      api: { putMyPreferences: async (next, revision) => { assert.equal(revision, 0); assert.deepEqual(JSON.parse(JSON.stringify(next)), profile(['绿茶'])); return { profile: profile(['乌龙茶']), revision: 1, updated_at: '2026-08-31T00:00:00Z' }; } },
    state,
    saveLocal: () => { saved += 1; },
    notify: message => notifications.push(message),
  });

  assert.equal(result, true);
  assert.deepEqual(state.o1, profile(['乌龙茶']).o1);
  assert.equal(state.preferenceRevision, 1);
  assert.equal(saved, 1);
  assert.deepEqual(notifications, ['偏好已同步']);
});

test('revision conflict refetches latest server state and never silently overwrites it', async () => {
  const sync = loadSync();
  const state = { o1: profile(['绿茶']).o1, o2: profile(['绿茶']).o2, preferenceRevision: 1 };
  let putCalls = 0;
  let fetchCalls = 0;
  let conflictCalls = 0;
  const result = await sync.persistProfile({
    api: {
      putMyPreferences: async () => { putCalls += 1; const error = new Error('conflict'); error.code = 'preferences_revision_conflict'; throw error; },
      getMyPreferences: async () => { fetchCalls += 1; return { profile: profile(['红茶']), revision: 2, updated_at: '2026-08-31T00:00:00Z' }; },
    },
    state,
    saveLocal: () => {},
    onConflict: () => { conflictCalls += 1; },
  });

  assert.equal(result, false);
  assert.equal(putCalls, 1);
  assert.equal(fetchCalls, 1);
  assert.equal(conflictCalls, 1);
  assert.equal(state.preferenceRevision, 2);
  assert.deepEqual(state.o1, profile(['红茶']).o1);
});

test('app bootstrap hydrates authenticated preferences before business-ready state', () => {
  assert.match(appSource, /const preferenceSync = await hydrateAuthenticatedPreferences\(\);/);
  assert.match(appSource, /const currentUser = await apiClient\.getMe\(\);[\s\S]*const preferenceSync = await hydrateAuthenticatedPreferences\(\);[\s\S]*appReady = true;/);
  assert.match(appSource, /void GuanchaPreferenceSync\.persistEvidence/);
});
