const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.resolve(__dirname, '..', '..', 'app.js'), 'utf8');

function loadMerchantReplyLifecycle(context) {
  const start = appSource.indexOf('function replyNeedsClarification');
  const end = appSource.indexOf('async function prepareImageFiles', start);
  assert.ok(start >= 0 && end > start);
  const render = context.render;
  const showToast = context.showToast;
  vm.runInNewContext(
    `${appSource.slice(start, end)}; globalThis.appendMerchantReplyForm = appendMerchantReplyForm; globalThis.reconcileMerchantReplyState = reconcileMerchantReplyState; globalThis.submitMerchantReply = submitMerchantReply; globalThis.renderOverlay = renderOverlay;`,
    context,
    { filename: 'app.js' },
  );
  context.render = render;
  context.showToast = showToast;
  return context;
}

function createSheetContext(state, apiClient = {}, overrides = {}) {
  const sheet = {
    form: null,
    querySelector: () => null,
    append(form) { this.form = form; },
  };
  const context = {
    state,
    apiClient: { isConfigured: true, ...apiClient },
    currentCandidate: () => state.candidates[state.activeCandidate] || null,
    escapeHtml: value => String(value).replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character])),
    app: { querySelector: () => sheet },
    document: { createElement: () => ({ dataset: {}, className: '', innerHTML: '' }) },
    saveState: () => {},
    render: () => { context.rendered = (context.rendered || 0) + 1; },
    showToast: message => { context.toast = message; },
    ...overrides,
  };
  return { context, sheet };
}

function question(id, candidateId) {
  return { id, candidate_id: candidateId, question_text: `问题 ${id}`, field_key: 'season', reason: 'reason' };
}

function reply(id, questionId, candidateId, parseStatus = 'answered') {
  return { id, followup_question_id: questionId, candidate_id: candidateId, parse_status: parseStatus, raw_text: `回复 ${id}` };
}

function baseState() {
  return {
    activeCandidate: 0,
    sessionId: 'session-1',
    decisionVersionId: 'decision-1',
    questionStatus: 'completed',
    questionDecisionVersionId: 'decision-1',
    candidates: [
      { id: 'candidate-a', letter: 'A', serverCandidateId: 'server-a' },
      { id: 'candidate-b', letter: 'B', serverCandidateId: 'server-b' },
    ],
    followupQuestions: [],
    merchantReplyIds: {},
    merchantReplies: {},
  };
}

test('fully answered current candidate shows guidance instead of a textarea', () => {
  const state = baseState();
  const questions = [question('question-a', 'server-a'), question('question-b', 'server-b')];
  const savedA = reply('reply-a', 'question-a', 'server-a');
  state.followupQuestions = questions;
  state.merchantReplyIds = { 'question-a': savedA.id };
  state.merchantReplies = { 'question-a': savedA };
  const { context, sheet } = createSheetContext(state);
  loadMerchantReplyLifecycle(context).appendMerchantReplyForm();

  assert.doesNotMatch(sheet.form.innerHTML, /textarea/);
  assert.match(sheet.form.innerHTML, /候选 A 的商家回复已保存，请切换到仍待补充的候选茶/);
  assert.match(sheet.form.innerHTML, /候选 B/);
});

test('all answered candidates show the aggregate update button without a textarea', () => {
  const state = baseState();
  const questions = [question('question-a', 'server-a'), question('question-b', 'server-b')];
  const replies = [reply('reply-a', 'question-a', 'server-a'), reply('reply-b', 'question-b', 'server-b')];
  state.followupQuestions = questions;
  state.merchantReplyIds = { 'question-a': 'reply-a', 'question-b': 'reply-b' };
  state.merchantReplies = Object.fromEntries(replies.map(item => [item.followup_question_id, item]));
  const { context, sheet } = createSheetContext(state);
  loadMerchantReplyLifecycle(context).appendMerchantReplyForm();

  assert.doesNotMatch(sheet.form.innerHTML, /textarea/);
  assert.match(sheet.form.innerHTML, /所有需要回复的候选茶已保存。/);
  assert.match(sheet.form.innerHTML, /data-action="update-merchant-judgement"/);
});

test('phantom submit reconciles readiness and never shows missing-question error', async () => {
  const state = baseState();
  const qA = question('question-a', 'server-a');
  const qB = question('question-b', 'server-b');
  const savedA = reply('reply-a', 'question-a', 'server-a');
  state.followupQuestions = [qA, qB];
  state.merchantReplyIds = { 'question-a': savedA.id };
  state.merchantReplies = { 'question-a': savedA };
  let snapshotCalls = 0;
  const { context } = createSheetContext(state, {
    getSelectionSnapshot: async () => {
      snapshotCalls += 1;
      return { questions: [qA, qB], merchant_replies: [savedA], question_decision_version_id: 'decision-1' };
    },
  });
  loadMerchantReplyLifecycle(context);

  await context.submitMerchantReply('should not be sent');

  assert.equal(snapshotCalls, 1);
  assert.doesNotMatch(context.toast || '', /请先生成当前问题/);
  assert.match(context.toast, /候选 A 的商家回复已保存/);
});

test('successful reply is followed by snapshot reconciliation to ready', async () => {
  const state = baseState();
  const qA = question('question-a', 'server-a');
  const qB = question('question-b', 'server-b');
  const savedA = reply('reply-a', 'question-a', 'server-a');
  const savedB = reply('reply-b', 'question-b', 'server-b');
  state.followupQuestions = [qA, qB];
  const calls = [];
  const { context } = createSheetContext(state, {
    createMerchantReply: async (_sessionId, payload) => { calls.push(payload); return savedA; },
    getSelectionSnapshot: async () => ({ questions: [qA, qB], merchant_replies: [savedA, savedB], question_decision_version_id: 'decision-2' }),
  });
  loadMerchantReplyLifecycle(context);

  await context.submitMerchantReply('商家回复');

  assert.equal(calls.length, 1);
  assert.equal(state.questionStatus, 'ready');
  assert.equal(state.questionDecisionVersionId, 'decision-2');
  assert.deepEqual(JSON.parse(JSON.stringify(state.merchantReplyIds)), { 'question-a': 'reply-a', 'question-b': 'reply-b' });
  assert.equal(context.toast, '全部待回复候选茶已保存，可更新本轮判断');
});

test('one merchant message is submitted to every pending question', async () => {
  const state = baseState();
  const questions = ['question-a', 'question-b', 'question-c'].map(id => question(id, 'server-a'));
  const calls = [];
  state.followupQuestions = questions;
  const replies = questions.map((item, index) => reply(`reply-${index}`, item.id, 'server-a'));
  const { context } = createSheetContext(state, {
    createMerchantReply: async (_sessionId, payload) => {
      calls.push(payload);
      return replies[calls.length - 1];
    },
    getSelectionSnapshot: async () => ({ questions, merchant_replies: replies, question_decision_version_id: 'decision-2' }),
  });
  loadMerchantReplyLifecycle(context);

  await context.submitMerchantReply('中焙，不提供试饮，这是秋茶。');

  assert.equal(calls.length, 3);
  assert.deepEqual(calls.map(item => item.followup_question_id), ['question-a', 'question-b', 'question-c']);
  assert.deepEqual(calls.map(item => item.raw_text), Array(3).fill('中焙，不提供试饮，这是秋茶。'));
});

test('batch submission skips an already satisfactory reply', async () => {
  const state = baseState();
  const questions = ['question-a', 'question-b', 'question-c'].map(id => question(id, 'server-a'));
  const savedA = reply('reply-a', 'question-a', 'server-a');
  const savedB = reply('reply-b', 'question-b', 'server-a');
  state.followupQuestions = questions;
  state.merchantReplyIds = { 'question-a': savedA.id };
  state.merchantReplies = { 'question-a': savedA };
  const calls = [];
  const { context } = createSheetContext(state, {
    createMerchantReply: async (_sessionId, payload) => {
      calls.push(payload);
      return payload.followup_question_id === 'question-b' ? savedB : reply('reply-c', 'question-c', 'server-a');
    },
    getSelectionSnapshot: async () => ({ questions, merchant_replies: [savedA, savedB, reply('reply-c', 'question-c', 'server-a')], question_decision_version_id: 'decision-2' }),
  });
  loadMerchantReplyLifecycle(context);

  await context.submitMerchantReply('补充完整商家回复');

  assert.deepEqual(calls.map(item => item.followup_question_id), ['question-b', 'question-c']);
});

test('all answered questions plus a zero-question candidate are globally ready', () => {
  const state = baseState();
  const qA = question('question-a', 'server-a');
  const savedA = reply('reply-a', 'question-a', 'server-a');
  state.activeCandidate = 1;
  state.followupQuestions = [qA];
  state.merchantReplyIds = { 'question-a': savedA.id };
  state.merchantReplies = { 'question-a': savedA };
  const { context, sheet } = createSheetContext(state);
  loadMerchantReplyLifecycle(context).appendMerchantReplyForm();

  assert.equal((sheet.form.innerHTML.match(/当前没有需要继续向商家确认的信息/g) || []).length, 0);
  assert.match(sheet.form.innerHTML, /所有需要回复的候选茶已保存。/);
  assert.match(sheet.form.innerHTML, /提交并更新判断/);
});

test('zero-question candidate does not duplicate its informational copy', () => {
  const state = baseState();
  const qA = question('question-a', 'server-a');
  const savedA = reply('reply-a', 'question-a', 'server-a');
  state.activeCandidate = 1;
  state.followupQuestions = [qA];
  state.merchantReplyIds = { };
  state.merchantReplies = { };
  const sheet = { form: null, querySelector: () => null, append(form) { this.form = form; } };
  const context = createSheetContext(state).context;
  context.app = { querySelector: selector => selector === '.ask-sheet' ? { append: form => { sheet.form = form; }, querySelector: () => null } : null };
  loadMerchantReplyLifecycle(context).appendMerchantReplyForm();

  assert.equal(sheet.form, null);
});

test('partial batch failure retains successes and leaves only failed questions pending', async () => {
  const state = baseState();
  const questions = ['question-a', 'question-b', 'question-c'].map(id => question(id, 'server-a'));
  const savedA = reply('reply-a', 'question-a', 'server-a');
  const savedB = reply('reply-b', 'question-b', 'server-a');
  state.followupQuestions = questions;
  const calls = [];
  const { context } = createSheetContext(state, {
    createMerchantReply: async (_sessionId, payload) => {
      calls.push(payload);
      if (payload.followup_question_id === 'question-c') throw Object.assign(new Error('temporary'), { code: 'network_unavailable' });
      return payload.followup_question_id === 'question-a' ? savedA : savedB;
    },
    getSelectionSnapshot: async () => ({ questions, merchant_replies: [savedA, savedB], question_decision_version_id: 'decision-2' }),
  });
  loadMerchantReplyLifecycle(context);

  await context.submitMerchantReply('完整商家回复');

  assert.equal(calls.length, 3);
  assert.deepEqual(Object.keys(state.merchantReplyIds).sort(), ['question-a', 'question-b']);
  assert.equal(state.questionStatus, 'completed');
  assert.match(context.toast, /还有 1 项未提交成功，请重试/);
});

test('clarification reply remains pending while unrelated answered questions are skipped', async () => {
  const state = baseState();
  const questions = ['question-a', 'question-b'].map(id => question(id, 'server-a'));
  const clarification = reply('reply-a', 'question-a', 'server-a', 'partially-answered');
  const savedB = reply('reply-b', 'question-b', 'server-a');
  state.followupQuestions = questions;
  state.merchantReplyIds = { 'question-a': clarification.id, 'question-b': savedB.id };
  state.merchantReplies = { 'question-a': clarification, 'question-b': savedB };
  const calls = [];
  const { context } = createSheetContext(state, {
    createMerchantReply: async (_sessionId, payload) => { calls.push(payload); return reply('reply-a2', 'question-a', 'server-a'); },
    getSelectionSnapshot: async () => ({ questions, merchant_replies: [reply('reply-a2', 'question-a', 'server-a'), savedB], question_decision_version_id: 'decision-2' }),
  });
  loadMerchantReplyLifecycle(context);

  await context.submitMerchantReply('只补充澄清项');

  assert.deepEqual(calls.map(item => item.followup_question_id), ['question-a']);
});

test('snapshot failure after reply keeps the returned local reply and avoids false failure', async () => {
  const state = baseState();
  const qA = question('question-a', 'server-a');
  const qB = question('question-b', 'server-b');
  const savedA = reply('reply-a', 'question-a', 'server-a');
  const savedB = reply('reply-b', 'question-b', 'server-a');
  state.followupQuestions = [qA, qB];
  state.merchantReplyIds = { 'question-b': savedB.id };
  state.merchantReplies = { 'question-b': savedB };
  const { context } = createSheetContext(state, {
    createMerchantReply: async () => savedA,
    getSelectionSnapshot: async () => { throw Object.assign(new Error('temporary network failure'), { code: 'network_unavailable' }); },
  });
  loadMerchantReplyLifecycle(context);

  await context.submitMerchantReply('商家回复');

  assert.equal(state.merchantReplyIds['question-a'], 'reply-a');
  assert.strictEqual(state.merchantReplies['question-a'], savedA);
  assert.equal(state.questionStatus, 'ready');
  assert.notEqual(state.questionStatus, 'failed');
});

test('clarification-required reply keeps the current question textarea', () => {
  const state = baseState();
  const qA = question('question-a', 'server-a');
  const clarification = reply('reply-a', 'question-a', 'server-a', 'partially-answered');
  state.followupQuestions = [qA];
  state.merchantReplyIds = { 'question-a': clarification.id };
  state.merchantReplies = { 'question-a': clarification };
  const { context, sheet } = createSheetContext(state);
  loadMerchantReplyLifecycle(context).appendMerchantReplyForm();

  assert.match(sheet.form.innerHTML, /textarea/);
  assert.match(sheet.form.innerHTML, /保存商家回复/);
  assert.doesNotMatch(sheet.form.innerHTML, /对应：/);
});

test('refresh with all persisted replies restores ready state', async () => {
  const qA = question('question-a', 'server-a');
  const qB = question('question-b', 'server-b');
  const savedA = reply('reply-a', 'question-a', 'server-a');
  const savedB = reply('reply-b', 'question-b', 'server-b');
  const state = {
    sessionId: 'session-1',
    activeCandidate: 0,
    activeCandidateId: null,
    activeSelectionFlow: true,
    screen: 'result',
    need: {},
    candidates: [],
    followupQuestions: [],
    merchantReplyIds: {},
    merchantReplies: {},
    questionStatus: 'completed',
    questionDecisionVersionId: null,
    decisionVersionId: null,
    decisionJobId: null,
    decisionStatus: 'not_requested',
    rejudgeJobId: null,
  };
  const context = {
    state,
    apiClient: {
      isConfigured: true,
      getSelectionSnapshot: async () => ({
        candidates: [{ id: 'server-a', display_label: 'A', images: [] }],
        session: { need: {} },
        current_decision_id: 'decision-1',
        questions: [qA, qB],
        merchant_replies: [savedA, savedB],
        question_decision_version_id: 'decision-1',
        question_generation_status: 'completed',
        session_decision_job: null,
        rejudge_job: null,
      }),
      getCurrentDecision: async () => ({ id: 'decision-1' }),
    },
    currentCandidate: () => state.candidates[state.activeCandidate] || null,
    candidateIdentity: candidate => candidate?.id,
    syncActiveCandidate: () => {},
    GuanchaAdapters: { activeRecoveryScreen: () => 'result' },
    applySessionDecision: () => {},
    refreshSelectionAnswer: async () => {},
    startCandidatePolling: () => {},
    startDecisionPolling: () => {},
    startRejudgePolling: () => {},
    saveState: () => {},
    render: () => {},
    merchantQuestionReadiness: () => {
      const ids = state.followupQuestions.map(item => item.id);
      return ids.length && ids.every(id => state.merchantReplyIds[id]) ? 'ready' : 'completed';
    },
  };
  const start = appSource.indexOf('async function resumeLiveBackendState');
  const end = appSource.indexOf('function fitLabel', start);
  assert.ok(start >= 0 && end > start);
  vm.runInNewContext(`${appSource.slice(start, end)}; globalThis.resumeLiveBackendState = resumeLiveBackendState;`, context, { filename: 'app.js' });

  await context.resumeLiveBackendState();

  assert.equal(state.questionStatus, 'ready');
  assert.deepEqual(JSON.parse(JSON.stringify(state.merchantReplyIds)), { 'question-a': 'reply-a', 'question-b': 'reply-b' });
});
