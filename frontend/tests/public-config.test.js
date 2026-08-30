const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '..', 'public-config.js'), 'utf8');

function loadConfig() {
  const window = {};
  window.window = window;
  vm.runInNewContext(source, window, { filename: 'public-config.js' });
  return window.GuanchaPublicConfig;
}

test('public product bounds retain five candidates and two screenshots', () => {
  const config = loadConfig();
  assert.equal(config.get().maxCandidates, 5);
  assert.equal(config.get().maxImagesPerCandidate, 2);

  const applied = config.apply({
    candidate_limit: 5,
    candidate_image_limit: 2,
    // Older API responses may still carry the retired 1/1 rollout flags.
    phase2_candidate_limit: 1,
    phase2_candidate_image_limit: 1,
  });
  assert.equal(applied.maxCandidates, 5);
  assert.equal(applied.maxImagesPerCandidate, 2);
});

test('public auth config accepts only the backend CloudBase region allowlist', () => {
  const config = loadConfig();
  let applied = config.apply({ auth: { required: false, configured: false, provider: 'cloudbase', region: 'ap-shanghai' } });
  assert.equal(applied.auth.required, false);
  assert.equal(applied.auth.configured, false);

  for (const region of ['ap-shanghai', 'ap-guangzhou', 'ap-singapore']) {
    applied = config.apply({ auth: { required: true, configured: true, provider: 'cloudbase', env_id: 'env-test', region, publishable_key: 'public-key' } });
    assert.deepEqual(JSON.parse(JSON.stringify(applied.auth)), { required: true, configured: true, provider: 'cloudbase', envId: 'env-test', region, publishableKey: 'public-key' });
  }

  applied = config.apply({ auth: { required: true, configured: true, provider: 'other', env_id: 'env-test', region: 'ap-unknown', publishable_key: 'public-key' } });
  assert.equal(applied.auth.configured, false);
  assert.equal(applied.auth.region, 'ap-shanghai');
});

test('configured API public config failures fail closed while unconfigured static mode stays legacy-compatible', async () => {
  const config = loadConfig();
  await assert.rejects(config.loadForBoot({ isConfigured: true, getPublicConfig: async () => { throw new Error('network unavailable'); } }));
  await assert.rejects(config.loadForBoot({ isConfigured: true, getPublicConfig: async () => ({ auth: {} }) }));
  const legacy = await config.loadForBoot({ isConfigured: false, getPublicConfig: async () => { throw new Error('must not call'); } });
  assert.equal(legacy.legacy, true);
  assert.equal(legacy.config.auth.required, false);
});
