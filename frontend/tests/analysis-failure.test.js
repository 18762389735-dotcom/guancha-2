const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.resolve(__dirname, '..', '..', 'app.js'), 'utf8');

function renderAnalysisFor(state) {
  const start = appSource.indexOf('function renderAnalysis()');
  const end = appSource.indexOf('function appendMerchantReplyForm()', start);
  assert.ok(start >= 0 && end > start);
  const context = {
    state,
    currentCandidate: () => state.candidates[state.activeCandidate] || null,
    candidateIdentity: candidate => candidate.id,
    escapeHtml: value => String(value).replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character])),
    asset: name => `assets/${name}`,
  };
  vm.runInNewContext(`${appSource.slice(start, end)}; globalThis.renderAnalysis = renderAnalysis;`, context, { filename: 'app.js' });
  return context.renderAnalysis();
}

function loadAnalysisLifecycle(context) {
  const start = appSource.indexOf('async function reconcilePersistedCandidateJobs');
  const end = appSource.indexOf('function startCandidatePolling', start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(
    `${appSource.slice(start, end)}; globalThis.reconcilePersistedCandidateJobs = reconcilePersistedCandidateJobs; globalThis.startMvpAnalysis = startMvpAnalysis;`,
    context,
    { filename: 'app.js' },
  );
  return context;
}

function analysisContext(state, apiClient, overrides = {}) {
  const context = {
    state,
    apiClient,
    pendingImageRestore: Promise.resolve(),
    validateAnalysisCandidates: () => true,
    apiNeed: () => ({ need: 'test' }),
    readPreferenceEvidence: () => [],
    runtimeImages: new Map(),
    saveState: () => {},
    render: () => {},
    showToast: message => { overrides.toast = message; },
    applyExtraction: (candidate, extraction) => { candidate.extraction = extraction; },
    maybeStartSessionDecision: async () => {},
    clearStaleRemoteSelection: () => {},
    ...overrides,
  };
  context.startCandidatePolling = candidate => { (context.polled || (context.polled = [])).push(candidate.id); };
  context.showToast = message => { context.toast = message; };
  return context;
}

async function runAnalysis(state, apiClient, overrides = {}) {
  const context = loadAnalysisLifecycle(analysisContext(state, apiClient, overrides));
  await context.startMvpAnalysis();
  return context;
}

test('mixed candidate failures render a failure summary instead of perpetual loading', () => {
  const completedExtraction = { id: 'extraction-b', evidence_items: [] };
  const state = {
    activeCandidate: 1,
    candidates: [
      { id: 'candidate-a', letter: 'A', extractionStatus: 'failed', jobError: 'ai_schema_invalid' },
      { id: 'candidate-b', letter: 'B', extractionStatus: 'completed', extraction: completedExtraction },
    ],
  };

  const html = renderAnalysisFor(state);

  assert.match(html, /分析未完成/);
  assert.match(html, /候选 A/);
  assert.match(html, /ai_schema_invalid/);
  assert.match(html, /data-candidate-id="candidate-a"/);
  assert.doesNotMatch(html, /正在分析中/);
  assert.strictEqual(state.candidates[1].extraction, completedExtraction);
  assert.equal(state.candidates[1].extractionStatus, 'completed');
});

test('queued or processing candidates without failures remain in the loading state', () => {
  const html = renderAnalysisFor({
    activeCandidate: 0,
    candidates: [
      { id: 'candidate-a', extractionStatus: 'processing' },
      { id: 'candidate-b', extractionStatus: 'completed' },
    ],
  });

  assert.match(html, /正在分析中/);
  assert.doesNotMatch(html, /分析未完成/);
});

test('retrying one failed candidate preserves the completed candidate state', async () => {
  const completedExtraction = { id: 'extraction-b', evidence_items: [{ field_name: 'product_name' }] };
  const state = {
    activeCandidate: 0,
    candidates: [
      {
        id: 'candidate-a', serverCandidateId: 'server-a', serverImageId: 'image-a',
        extractionStatus: 'failed', jobError: 'ai_schema_invalid', extraction: null,
        extractionVersionId: 'old-version', jobId: 'old-job',
        images: [{ id: 'local-a', serverImageId: 'image-a', localOnly: false, status: 'failed' }],
      },
      { id: 'candidate-b', serverCandidateId: 'server-b', extractionStatus: 'completed', extraction: completedExtraction },
    ],
  };
  const context = {
    state,
    currentCandidate: () => state.candidates[state.activeCandidate],
    apiClient: { isConfigured: true, deleteCandidateImage: async () => {} },
    saveState: () => {},
    startMvpAnalysis: async () => { context.restarted = true; },
    render: () => {},
    setScreen: () => {},
  };
  const start = appSource.indexOf('async function retryMvpAnalysis');
  const end = appSource.indexOf('function asset(', start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(`${appSource.slice(start, end)}; globalThis.retryMvpAnalysis = retryMvpAnalysis;`, context, { filename: 'app.js' });

  await context.retryMvpAnalysis(state.candidates[0]);

  assert.equal(context.restarted, true);
  assert.equal(state.candidates[0].extractionStatus, 'queued');
  assert.equal(state.candidates[0].extraction, null);
  assert.strictEqual(state.candidates[1].extraction, completedExtraction);
  assert.equal(state.candidates[1].extractionStatus, 'completed');
});

test('shared analyze failure reconciles persisted jobs instead of failing every candidate', async () => {
  const state = {
    sessionId: 'session-1',
    candidates: [
      { id: 'candidate-a', serverCandidateId: 'server-a', jobId: 'job-a', extractionStatus: 'queued', jobError: null, images: [] },
      { id: 'candidate-b', serverCandidateId: 'server-b', jobId: 'job-b', extractionStatus: 'queued', jobError: null, images: [] },
    ],
  };
  const apiClient = {
    isConfigured: true,
    updateSelectionSession: async () => ({ id: 'session-1' }),
    analyzeSelectionSession: async () => { throw Object.assign(new Error('gateway failed'), { code: 'request_failed' }); },
    getJob: async jobId => ({ id: jobId, status: 'processing', error_code: null }),
  };

  const context = await runAnalysis(state, apiClient);

  assert.equal(state.candidates[0].extractionStatus, 'processing');
  assert.equal(state.candidates[1].extractionStatus, 'processing');
  assert.equal(state.candidates[0].jobError, null);
  assert.equal(state.candidates[1].jobError, null);
  assert.deepEqual(context.polled.sort(), ['candidate-a', 'candidate-b']);
  assert.equal(context.toast, '连接暂时中断，正在继续确认分析状态');
});

test('shared analyze failure applies mixed authoritative completed and failed job states', async () => {
  const state = {
    sessionId: 'session-1',
    candidates: [
      { id: 'candidate-a', serverCandidateId: 'server-a', jobId: 'job-a', extractionStatus: 'queued', jobError: null, images: [] },
      { id: 'candidate-b', serverCandidateId: 'server-b', jobId: 'job-b', extractionStatus: 'queued', jobError: null, images: [] },
    ],
  };
  const extraction = { id: 'extraction-a', evidence_items: [] };
  const apiClient = {
    isConfigured: true,
    updateSelectionSession: async () => ({ id: 'session-1' }),
    analyzeSelectionSession: async () => { throw Object.assign(new Error('gateway failed'), { code: 'request_failed' }); },
    getJob: async jobId => jobId === 'job-a'
      ? { id: jobId, status: 'completed', extraction_version_id: 'version-a', error_code: null }
      : { id: jobId, status: 'failed', extraction_version_id: null, error_code: 'ai_schema_invalid' },
    getCurrentExtraction: async () => extraction,
  };

  await runAnalysis(state, apiClient);

  assert.equal(state.candidates[0].extractionStatus, 'completed');
  assert.strictEqual(state.candidates[0].extraction, extraction);
  assert.equal(state.candidates[1].extractionStatus, 'failed');
  assert.equal(state.candidates[1].jobError, 'ai_schema_invalid');
});

test('temporary Job reconciliation failure keeps persisted candidate recoverable', async () => {
  const state = {
    sessionId: 'session-1',
    candidates: [
      { id: 'candidate-a', serverCandidateId: 'server-a', jobId: 'job-a', extractionStatus: 'queued', jobError: null, images: [] },
    ],
  };
  const apiClient = {
    isConfigured: true,
    updateSelectionSession: async () => ({ id: 'session-1' }),
    analyzeSelectionSession: async () => { throw Object.assign(new Error('gateway failed'), { code: 'request_failed' }); },
    getJob: async () => { throw Object.assign(new Error('temporary status failure'), { code: 'network_unavailable' }); },
  };

  const context = await runAnalysis(state, apiClient);

  assert.equal(state.candidates[0].extractionStatus, 'queued');
  assert.equal(state.candidates[0].jobError, null);
  assert.deepEqual(context.polled, ['candidate-a']);
});

test('candidate-specific upload failure does not affect another candidate Job', async () => {
  const state = {
    sessionId: null,
    candidates: [
      { id: 'candidate-a', letter: 'A', name: 'A', extractionStatus: 'queued', images: [{ id: 'local-a', localOnly: true }] },
      { id: 'candidate-b', letter: 'B', name: 'B', extractionStatus: 'queued', images: [{ id: 'local-b', localOnly: true }] },
    ],
  };
  const overrides = { polled: [] };
  const apiClient = {
    isConfigured: true,
    createSelectionSession: async () => ({ id: 'session-1' }),
    createCandidate: async (_sessionId, payload) => ({ id: `server-${payload.display_label.toLowerCase()}` }),
    uploadCandidateImage: async candidateId => {
      if (candidateId === 'server-a') throw Object.assign(new Error('upload failed'), { code: 'upload_failed' });
      return {
        image: { id: 'image-b', status: 'queued' },
        extraction_job: { id: 'job-b', status: 'queued' },
      };
    },
    analyzeSelectionSession: async () => ({ id: 'dispatch-1' }),
  };
  const context = analysisContext(state, apiClient, overrides);
  context.runtimeImages.set('local-a', { file: { name: 'a.png' } });
  context.runtimeImages.set('local-b', { file: { name: 'b.png' } });
  loadAnalysisLifecycle(context);
  await context.startMvpAnalysis();

  assert.equal(state.candidates[0].extractionStatus, 'failed');
  assert.equal(state.candidates[0].jobError, 'upload_failed');
  assert.equal(state.candidates[1].extractionStatus, 'queued');
  assert.equal(state.candidates[1].jobId, 'job-b');
  assert.deepEqual(context.polled, ['candidate-b']);
});

test('authoritative Job failure remains a candidate-specific failure', async () => {
  const state = {
    sessionId: 'session-1',
    candidates: [
      { id: 'candidate-a', serverCandidateId: 'server-a', jobId: 'job-a', extractionStatus: 'queued', jobError: null, images: [] },
    ],
  };
  const apiClient = {
    isConfigured: true,
    updateSelectionSession: async () => ({ id: 'session-1' }),
    analyzeSelectionSession: async () => { throw Object.assign(new Error('gateway failed'), { code: 'request_failed' }); },
    getJob: async () => ({ id: 'job-a', status: 'failed', error_code: 'ai_timeout' }),
  };

  const context = await runAnalysis(state, apiClient);

  assert.equal(state.candidates[0].extractionStatus, 'failed');
  assert.equal(state.candidates[0].jobError, 'ai_timeout');
  assert.deepEqual(context.polled || [], []);
});
