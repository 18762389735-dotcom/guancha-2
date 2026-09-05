const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.resolve(__dirname, '..', '..', 'app.js'), 'utf8');

function sourceBetween(startMarker, endMarker) {
  const start = appSource.indexOf(startMarker);
  const end = appSource.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start);
  return appSource.slice(start, end);
}

test('sample candidate uses the existing project-owned fixture images through the normal add flow', async () => {
  const source = sourceBetween('async function addSampleCandidate()', 'async function appendCandidateImage');
  const candidate = { id: 'local-candidate-1' };
  const fetched = [];
  const context = {
    SAMPLE_CANDIDATE_ASSETS: [
      'test-fixtures/demo-images/candidate-a-qingxiang-1.png',
      'test-fixtures/demo-images/candidate-a-qingxiang-2.png',
    ],
    sampleCandidateInFlight: false,
    fetch: async asset => {
      fetched.push(asset);
      return { ok: true, blob: async () => ({ type: 'image/png' }) };
    },
    File: class FakeFile {
      constructor(parts, name, options) { this.parts = parts; this.name = name; this.type = options.type; }
    },
    addCandidate: async files => { context.files = files; return { ok: true, candidate }; },
    saveState: () => {},
    setScreen: screen => { context.state.screen = screen; },
    showToast: message => { context.toast = message; },
    render: () => {},
    state: { screen: 'candidates', overlay: 'source' },
  };
  vm.runInNewContext(`${source}; globalThis.addSampleCandidate = addSampleCandidate;`, context, { filename: 'app.js' });

  assert.equal(await context.addSampleCandidate(), true);
  assert.deepEqual(fetched, context.SAMPLE_CANDIDATE_ASSETS);
  assert.equal(context.files.length, 2);
  assert.equal(context.files[0].type, 'image/png');
  assert.equal(candidate.isDemoSample, true);
  assert.equal(context.state.overlay, null);
});

test('sample candidate is clearly offered from the empty state and source sheet', () => {
  assert.equal((appSource.match(/data-action="use-sample-candidate"/g) || []).length, 2);
  assert.match(appSource, /没有截图也可以先看完整流程。示例内容不会使用你的照片。/);
  assert.match(appSource, /<b>拍照<\/b>/);
  assert.match(appSource, /<b>从相册选择<\/b>/);
  assert.match(appSource, /sampleCandidateInFlight \? '正在准备示例候选…' : '使用示例'/);
  assert.match(appSource, /candidate-a-qingxiang-1\.png/);
  assert.match(appSource, /candidate-a-qingxiang-2\.png/);
});

test('analysis failures use product language and never render provider error codes', () => {
  const source = sourceBetween('function renderAnalysis()', 'function appendMerchantReplyForm()');
  assert.match(source, /这张图片暂时没分析成功，请确认截图清晰后再试一次/);
  assert.doesNotMatch(source, /错误代码/);
  assert.doesNotMatch(source, /escapeHtml\(candidate\.jobError\)/);
});

test('critical async actions have UI-side duplicate-submit guards', () => {
  assert.match(appSource, /if \(analysisInFlight\) return;/);
  assert.match(appSource, /if \(warehouseSaveInFlight\) return;/);
  assert.match(appSource, /if \(brewSaveInFlight\) return;/);
  assert.match(appSource, /if \(!reply \|\| merchantReplyInFlight\) return;/);
  assert.match(appSource, /if \(rejudgeInFlight\) return;/);
});

test('sample marker is forwarded to extraction and merchant fallback boundaries', () => {
  assert.match(appSource, /uploadCandidateImage\(candidate\.serverCandidateId, runtime\.file, undefined, \{ demoSample: candidate\.isDemoSample === true \}\)/);
  assert.match(appSource, /createMerchantReply\(state\.sessionId,[\s\S]*demoSample: candidate\?\.isDemoSample === true/);
  assert.match(appSource, /rejudgeMerchantReply\(state\.sessionId, undefined, \{ demoSample: currentCandidate\(\)\?\.isDemoSample === true \}\)/);
  assert.match(appSource, /isDemoSample: demoSampleByServerId\.get\(remote\.id\) === true/);
});

test('real analysis has staged progress and a bounded retryable deadline', () => {
  assert.match(appSource, /const ANALYSIS_TOTAL_DEADLINE_MS = 65000/);
  assert.match(appSource, /正在读取商品信息…/);
  assert.match(appSource, /正在整理香型、工艺和商品信息…/);
  assert.match(appSource, /正在结合你的偏好进行判断…/);
  assert.match(appSource, /这张图片信息比较多，分析还需要一点时间…/);
  assert.match(appSource, /setTimeout\(markAnalysisDeadlineReached, remainingMs\)/);
  assert.match(appSource, /这次分析没有顺利完成，可以重新试一次/);
});

test('analysis progress copy changes at the intended user-facing stages', () => {
  const start = appSource.indexOf('function analysisElapsedMs');
  const end = appSource.indexOf('async function reconcilePersistedCandidateJobs', start);
  assert.ok(start >= 0 && end > start);
  const context = {
    state: { analysisStartedAt: 0 },
    Date: { now: () => 0 },
    ANALYSIS_PROGRESS_STAGES: [
      [8000, '正在读取商品信息…'],
      [20000, '正在整理香型、工艺和商品信息…'],
      [35000, '正在结合你的偏好进行判断…'],
      [Infinity, '这张图片信息比较多，分析还需要一点时间…'],
    ],
  };
  vm.runInNewContext(`${appSource.slice(start, end)}; globalThis.analysisProgressMessage = analysisProgressMessage;`, context, { filename: 'app.js' });
  assert.equal(context.analysisProgressMessage(0), '正在读取商品信息…');
  assert.equal(context.analysisProgressMessage(8000), '正在整理香型、工艺和商品信息…');
  assert.equal(context.analysisProgressMessage(20000), '正在结合你的偏好进行判断…');
  assert.equal(context.analysisProgressMessage(35000), '这张图片信息比较多，分析还需要一点时间…');
});

test('first authenticated bootstrap allows one silent retry only for transient failures', () => {
  const start = appSource.indexOf('function isTransientBootstrapError');
  const end = appSource.indexOf('function rebuildApiClient', start);
  assert.ok(start >= 0 && end > start);
  const context = {};
  vm.runInNewContext(`${appSource.slice(start, end)}; globalThis.isTransientBootstrapError = isTransientBootstrapError;`, context, { filename: 'app.js' });

  assert.equal(context.isTransientBootstrapError({ code: 'service_unavailable' }), true);
  assert.equal(context.isTransientBootstrapError({ code: 'network_unavailable' }), true);
  assert.equal(context.isTransientBootstrapError({ code: 'invalid_access_token' }), false);
  assert.equal(context.isTransientBootstrapError({ code: 'validation_error' }), false);
  assert.match(appSource, /bootstrapAttempt === 0 && isTransientBootstrapError\(error\)/);
});
