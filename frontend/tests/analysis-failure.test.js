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
