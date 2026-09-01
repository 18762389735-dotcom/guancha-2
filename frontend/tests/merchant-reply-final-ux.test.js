const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.resolve(__dirname, '..', '..', 'app.js'), 'utf8');

function loadMerchantForm(context) {
  const start = appSource.indexOf('function replyNeedsClarification');
  const end = appSource.indexOf('async function prepareImageFiles', start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(
    `${appSource.slice(start, end)}; globalThis.appendMerchantReplyForm = appendMerchantReplyForm;`,
    context,
    { filename: 'app.js' },
  );
}

function formContext(state) {
  const sheet = {
    form: null,
    querySelector: () => null,
    append(form) { this.form = form; },
  };
  const context = {
    state,
    currentCandidate: () => state.candidates[state.activeCandidate] || null,
    escapeHtml: value => String(value).replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character])),
    app: { querySelector: () => sheet },
    document: { createElement: () => ({ dataset: {}, className: '', innerHTML: '' }) },
    saveState: () => {},
    render: () => {},
  };
  return { context, sheet };
}

test('candidate with no questions gets no saved-reply claim or textarea', () => {
  const state = {
    activeCandidate: 0,
    questionStatus: 'completed',
    candidates: [
      { letter: 'A', serverCandidateId: 'server-a' },
      { letter: 'B', serverCandidateId: 'server-b' },
    ],
    followupQuestions: [{ id: 'question-b', candidate_id: 'server-b', question_text: '问题 B', field_key: 'season' }],
    merchantReplyIds: {},
    merchantReplies: {},
  };
  const { context, sheet } = formContext(state);
  loadMerchantForm(context);
  context.appendMerchantReplyForm();

  assert.doesNotMatch(sheet.form.innerHTML, /textarea/);
  assert.match(sheet.form.innerHTML, /候选 A 当前没有需要继续向商家确认的信息/);
  assert.doesNotMatch(sheet.form.innerHTML, /商家回复已保存/);
});

function fitLabel(sensoryMatch, overallOrder) {
  const start = appSource.indexOf('function fitLabel');
  const end = appSource.indexOf('async function maybeStartSessionDecision', start);
  const context = { GuanchaAdapters: { sensoryNeedMatch: () => sensoryMatch } };
  vm.runInNewContext(`${appSource.slice(start, end)}; globalThis.fitLabel = fitLabel;`, context, { filename: 'app.js' });
  return context.fitLabel({ decision: { overall_order: overallOrder } }, { sensory_interpretations: [] });
}

test('fit label keeps overall ranking visible without claiming taste certainty', () => {
  assert.equal(fitLabel(0, 1), '综合条件下当前更优先');
  assert.equal(fitLabel(0, 2), '口味方向仍待体验确认');
  assert.equal(fitLabel(1, 1), '当前更接近你的方向');
});
