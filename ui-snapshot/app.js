const app = document.querySelector('#app');
const toast = document.querySelector('#toast');
const albumInput = document.querySelector('#album-input');
const cameraInput = document.querySelector('#camera-input');
const candidateImageInput = document.querySelector('#candidate-image-input');
let activeCameraStream = null;
let lastRenderedScreen = null;
let lastResultAnalyticsEdge = null;

const DRINKS = {
  tea: { label: '茶饮', icon: 'tea.png', options: ['绿茶', '花香茶', '乌龙茶', '红茶', '焙火茶', '陈香茶', '奶茶 / 果茶'] },
  coffee: { label: '咖啡', icon: 'coffee.png', options: ['美式 / 黑咖啡', '拿铁', '冷萃', '浅烘手冲', '深烘咖啡'] },
  milk: { label: '奶与植物乳', icon: 'milk.png', options: ['纯牛奶', '酸奶', '豆浆', '燕麦奶', '椰奶'] },
  juice: { label: '果蔬饮品', icon: 'juice.png', options: ['柑橘类果汁', '苹果 / 梨汁', '桃子 / 荔枝饮品', '葡萄 / 莓果汁', '热带水果汁', '蔬菜汁', '椰子水'] },
};
const FLAVORS = ['茉莉花', '兰花', '桂花', '玫瑰', '水蜜桃', '荔枝', '梨', '柑橘', '桂圆', '红枣', '青梅', '葡萄干', '嫩叶', '青草', '竹叶', '青豆', '板栗', '炒黄豆', '烤花生', '烤面包', '蜂蜜', '焦糖', '糯米', '陈皮'];
const ART = {
  can: 'art-can-clean.png',
  gaiwan: 'art-gaiwan-clean.png',
  cup: 'art-cup-clean.png',
  bag: 'art-bag-clean.png',
};
const configuredApiBaseUrl = window.GUANCHA_API_BASE_URL || window.API_BASE_URL || (/^https?:$/.test(window.location?.protocol || '') ? window.location.origin : '');
const apiClient = GuanchaApi.createApiClient({
  baseUrl: configuredApiBaseUrl,
  clientId: GuanchaApi.getOrCreateClientId(),
});
const productAnalytics = GuanchaProductAnalytics.create({ endpoint: `${configuredApiBaseUrl || ''}/api/v1/events` });
productAnalytics.track('app_open', { metadata: { screen: 'home' } });
const runtimeImages = new Map();
let pendingImageCandidateId = null;
// A cached older stores.js must never make the upload UI unusable.  Its
// fallback keeps the current page functional; a fresh load restores the
// IndexedDB-backed persistence path below.
const pendingImageStore = GuanchaStores.pendingImages || {
  save: async () => false,
  load: async () => null,
  remove: async () => false,
  clear: async () => false,
};
// Product P0 permits up to five candidates, with one optional supporting
// screenshot for each candidate.  The compact candidate card remains the
// same; only its existing small "+" control exposes the second-image path.
const PRODUCT_LIMITS = Object.freeze({ maxCandidates: 5, maxImagesPerCandidate: 2 });

const defaultState = {
  screen: 'home',
  overlay: null,
  openDrink: 'tea',
  o1: { tea: ['绿茶', '花香茶', '乌龙茶', '红茶'], coffee: [], milk: [], juice: [] },
  o2: { sweetness: 75, flavors: ['茉莉花', '兰花'] },
  need: { taste: '清爽花香', purpose: '送礼', budget: '150–300 元' },
  candidates: [],
  activeCandidate: 0,
  activeCandidateId: null,
  reply: '',
  history: [],
  sourceFor: null,
  activeSelectionFlow: false,
  preferenceFlow: null,
  ownershipChoice: 'bought',
  warehouse: [
    { id: 'spring', name: '春日乌龙', type: '乌龙茶', aroma: '清香型', status: 'drinking', source: '选茶结果', lastBrew: '今天', records: 1, art: 'can', facts: ['轻火焙制', '2026 年春茶', '支持 10g 试饮装'], risks: ['具体产地仍待确认'] },
    { id: 'peony', name: '白牡丹', type: '白茶', aroma: '花香', status: 'drinking', source: '手动入库', lastBrew: '7 月 28 日', records: 3, art: 'gaiwan', facts: ['茶类已确认：白茶'], risks: ['年份与产地未记录'] },
    { id: 'puer', name: '陈皮熟普', type: '黑茶', aroma: '陈香', status: 'paused', source: '手动入库', lastBrew: '较久之前', records: 2, art: 'can', facts: ['茶类已确认：黑茶'], risks: ['原始购买信息待补'] },
  ],
  selectedTeaId: null,
  brew: null,
  journalRecords: [
    { id: 'demo-0804', date: '2026-08-04', teaId: 'spring', infusions: [{ number: 1, suggested: 10, actual: 11 }, { number: 2, suggested: 12, actual: 12 }], plan: { ware: '盖碗', water: '110 ml', grams: '5 g', temp: '95℃' }, feedback: { taste: '喜欢', strength: '刚好', tags: ['清爽', '花香'], aroma: ['兰花', '茉莉花'], impression: '滋味顺口，花香清晰。', score: 4, repurchase: '想回购' }, suggestion: '暂时保持本次参数', createdAt: '14:20' },
  ],
};

function storedObject(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null');
    return value && typeof value === 'object' ? value : null;
  } catch { return null; }
}
function savedPreferenceSnapshot() {
  const ui = storedObject(GuanchaStores.uiSession.key);
  return ui || {};
}
function onboardingStatus() {
  return GuanchaOnboarding.resolveStatus(localStorage, savedPreferenceSnapshot());
}
function routeAfterHomeStart() {
  state.activeSelectionFlow = true;
  productAnalytics.startFlow();
  productAnalytics.track('start_selection', { metadata: { onboarding_status: onboardingStatus(), screen: 'home' } });
  if (onboardingStatus() === 'not_started') {
    state.preferenceFlow = 'onboarding';
    productAnalytics.track('onboarding_started', { metadata: { source: 'selection', screen: 'o1' } });
    return setScreen('o1');
  }
  state.preferenceFlow = null;
  return setScreen('candidates');
}
function completeSelectionFlow() {
  state.activeSelectionFlow = false;
  state.preferenceFlow = null;
  productAnalytics.endFlow();
}
function applyInitialRoute(next) {
  next.screen = GuanchaOnboarding.initialScreen({
    reload: GuanchaOnboarding.isReload(window.performance),
    state: next,
  });
  next.overlay = null;
  return next;
}
function loadState() {
  const uiFallback = { screen: defaultState.screen, overlay: null, openDrink: defaultState.openDrink, activeCandidate: 0, activeCandidateId: null, o1: defaultState.o1, o2: defaultState.o2, ownershipChoice: defaultState.ownershipChoice, brew: null, journalDate: null, activeRecordId: null };
  const selectionFallback = { sessionId: null, candidates: [], reply: '', need: defaultState.need, decisionVersionId: null, decisionJobId: null, decisionStatus: 'not_requested', selectionAnswer: null, followupQuestions: [], questionStatus: 'idle', questionDecisionVersionId: null, merchantReplyIds: {}, merchantReplies: {}, rejudgeJobId: null, lastDecisionDelta: null, deltaStatus: 'idle', jobIds: {} };
  const postPurchaseFallback = { warehouse: defaultState.warehouse, journalRecords: defaultState.journalRecords, history: [], selectedTeaId: null };
  GuanchaStores.migrateLegacy();
  const ui = GuanchaStores.uiSession.load(uiFallback);
  const bridge = GuanchaStores.selectionBridge.load(selectionFallback);
  const postPurchase = GuanchaStores.localPostPurchase.load(postPurchaseFallback);
  if (localStorage.getItem(GuanchaStores.selectionBridge.key) || localStorage.getItem(GuanchaStores.localPostPurchase.key)) {
    return applyInitialRoute(normalizeState({ ...structuredClone(defaultState), ...ui, ...bridge, ...postPurchase, overlay: null, sourceFor: null }));
  }
  return applyInitialRoute(structuredClone(defaultState));
}
function normalizeCandidate(candidate, index) {
  const legacyImageCount = Array.isArray(candidate.images) ? candidate.images.length : Number(candidate.images) || 0;
  const images = Array.isArray(candidate.images) ? candidate.images.slice(0, imageLimit()) : Array.from({ length: Math.min(imageLimit(), legacyImageCount) }, (_, imageIndex) => ({ id: `legacy-${index}-${imageIndex}`, status: 'idle', localOnly: true }));
  const hasServerResult = Boolean(candidate.serverCandidateId && candidate.extractionVersionId && candidate.extraction && candidate.extractionStatus === 'completed');
  const letter = String.fromCharCode(65 + index);
  return {
    ...candidate,
    id: candidate.id || `local-candidate-${index}`,
    letter,
    images,
    // Before extraction, names in the original prototype were decorative
    // examples.  Keep the card truthful until the provider supplies a result.
    name: hasServerResult ? candidate.name : `候选茶 ${letter}`,
    type: hasServerResult ? candidate.type : '商品信息整理中',
    extraction: hasServerResult ? candidate.extraction : null,
    extractionVersionId: hasServerResult ? candidate.extractionVersionId : null,
    decision: hasServerResult ? candidate.decision || null : null,
    extractionStatus: hasServerResult ? 'completed' : candidate.serverCandidateId && candidate.jobId ? candidate.extractionStatus || 'queued' : 'idle',
  };
}
function normalizeState(value) {
  const next = { ...value };
  if (typeof next.activeSelectionFlow !== 'boolean') {
    if (['home', 'warehouse', 'warehouse-detail', 'journal', 'settings'].includes(next.screen)) next.activeSelectionFlow = false;
    else delete next.activeSelectionFlow;
  }
  next.candidates = (Array.isArray(next.candidates) ? next.candidates : []).slice(0, candidateLimit()).map(normalizeCandidate);
  next.activeCandidate = GuanchaAdapters.resolveActiveCandidateIndex(next.candidates, next.activeCandidateId, next.activeCandidate);
  next.activeCandidateId = candidateIdentity(next.candidates[next.activeCandidate]);
  if (['analysis', 'result', 'rejudge'].includes(next.screen)) next.screen = 'candidates';
  return next;
}
let state = loadState();
// 旧版本地缓存没有茶仓与泡茶日记字段时，自动补齐演示数据。
if (!Array.isArray(state.warehouse)) state.warehouse = structuredClone(defaultState.warehouse);
if (!Array.isArray(state.journalRecords)) state.journalRecords = structuredClone(defaultState.journalRecords);
if (!state.ownershipChoice) state.ownershipChoice = 'bought';
let brewTimerId = null;
function renumberCandidates() {
  state.candidates.forEach((candidate, index) => {
    const letter = String.fromCharCode(65 + index);
    candidate.letter = letter;
    if (candidate.extractionStatus !== 'completed') {
      candidate.name = `候选茶 ${letter}`;
      candidate.type = '商品信息整理中';
    }
  });
}
async function restorePendingImages() {
  const pending = state.candidates.flatMap(candidate => (candidate.images || []).filter(image => image.localOnly && !image.serverImageId));
  await Promise.all(pending.map(async (image) => {
    if (runtimeImages.has(image.id)) return;
    const file = await pendingImageStore.load(image.id);
    if (file instanceof Blob) runtimeImages.set(image.id, { file, url: URL.createObjectURL(file) });
  }));
  render();
}
const pendingImageRestore = restorePendingImages();
function publicLimits() { return GuanchaPublicConfig.get(); }
function candidateLimit() { return Math.min(PRODUCT_LIMITS.maxCandidates, publicLimits().maxCandidates); }
function imageLimit() { return Math.min(PRODUCT_LIMITS.maxImagesPerCandidate, publicLimits().maxImagesPerCandidate); }

function saveState() {
  syncActiveCandidate();
  GuanchaStores.uiSession.save({ screen: state.screen, openDrink: state.openDrink || '', activeCandidateId: state.activeCandidateId, o1: state.o1, o2: state.o2, ownershipChoice: state.ownershipChoice, activeSelectionFlow: state.activeSelectionFlow === true, preferenceFlow: state.preferenceFlow || null });
  const persistedMerchantReplies = Object.fromEntries(Object.entries(state.merchantReplies || {}).map(([questionId, reply]) => [questionId, Object.fromEntries(['id','selection_session_id','decision_version_id','followup_question_id','candidate_id','status','processing_status','parse_status','created_at','updated_at'].filter(key => reply?.[key] != null).map(key => [key, reply[key]]))]));
  GuanchaStores.selectionBridge.save({ sessionId: state.sessionId || null, candidates: state.candidates, reply: '', need: state.need, decisionVersionId: state.decisionVersionId || null, decisionJobId: state.decisionJobId || null, decisionStatus: state.decisionStatus || 'not_requested', selectionAnswer: state.selectionAnswer || null, followupQuestions: state.followupQuestions || [], questionStatus: state.questionStatus || 'idle', questionDecisionVersionId: state.questionDecisionVersionId || null, merchantReplyIds: state.merchantReplyIds || {}, merchantReplies: persistedMerchantReplies, rejudgeJobId: state.rejudgeJobId || null, lastDecisionDelta: state.lastDecisionDelta || null, deltaStatus: state.deltaStatus || 'idle', jobIds: state.jobIds || {} });
  GuanchaStores.localPostPurchase.save({ warehouse: state.warehouse, journalRecords: state.journalRecords, history: state.history, selectedTeaId: state.selectedTeaId || null });
}
function apiNeed(need = state.need) {
  return {
    taste_text: need.taste || null,
    purpose_text: need.purpose || null,
    budget_text: need.budget || null,
    risk_attitude_text: null,
  };
}
function evidenceByField(extraction) {
  return (extraction?.evidence_items || []).reduce((values, item) => {
    if (item.field_name && item.normalized_value && values[item.field_name] === undefined && ['explicit', 'inferred'].includes(item.information_status)) values[item.field_name] = item.normalized_value;
    return values;
  }, {});
}
function evidenceForDisplay(extraction) {
  const fieldLabels = {
    product_name: '商品名称', tea_category: '茶类', tea_subtype: '具体茶类', origin: '产地信息',
    roast_or_style: '香型 / 焙火风格', aroma_claims: '香气描述', taste_claims: '滋味描述',
    year_or_harvest: '年份 / 采摘季节', grade: '等级', weight: '净含量', price: '价格',
    brew_claims: '冲泡说明', risk_flag: '风险提示', sample_available: '是否支持试饮 / 小样',
  };
  const sourceLabels = {
    'product-claim': '商品页面声明', 'merchant-claim': '商家回复声明',
    'user-input': '用户提供信息', 'system-inference': '基于页面内容推测', 'brew-feedback': '冲泡反馈',
  };
  const verificationLabels = {
    unverified: '未核验', 'user-confirmed': '用户已确认',
    'system-consistent': '内容一致', conflicting: '信息有冲突',
  };
  const informationLabels = {
    explicit: '页面明确写明', inferred: '根据页面内容推测', unknown: '暂未找到', conflict: '信息存在冲突',
  };
  return (extraction?.evidence_items || []).map((item) => ({
    ...item,
    displayName: fieldLabels[item.field_name] || '商品信息',
    displayValue: item.normalized_value || item.raw_text || '暂未找到',
    isConfirmed: ['explicit', 'inferred'].includes(item.information_status),
    sourceLabel: sourceLabels[item.source_type] || '商品页面声明',
    verificationLabel: verificationLabels[item.verification_status] || '未核验',
    statusLabel: informationLabels[item.information_status] || '暂未找到',
  }));
}
function riskForDisplay(value) {
  const labels = {
    season_claim_conflict: '季节信息存在冲突',
    origin_claim_conflict: '产地信息存在冲突',
    price_claim_conflict: '价格信息存在冲突',
    missing_key_information: '关键信息暂未找到',
  };
  return labels[value] || '有一项信息需要进一步确认';
}
function candidateImageGallery(candidate) {
  const images = (candidate.images || []).filter((image) => image.previewUrl || runtimeImages.get(image.id)?.url);
  if (!images.length) return `<span class="result-art ref-art ${candidate.letter === 'B' ? 'ref-art--gaiwan' : 'ref-art--can'}" role="img" aria-label="${escapeHtml(candidate.name)}的商品图"></span>`;
  return `<div class="result-image-gallery" aria-label="${escapeHtml(candidate.name)}的商品截图，可左右滑动查看">${images.map((image, index) => {
    const url = runtimeImages.get(image.id)?.url || image.previewUrl;
    return `<img class="result-image-slide" src="${url}" alt="商品截图 ${index + 1}" loading="lazy" />`;
  }).join('')}</div>`;
}
function applyExtraction(candidate, extraction) {
  const values = evidenceByField(extraction);
  candidate.extractionVersionId = extraction.id;
  candidate.extraction = extraction;
  candidate.extractionStatus = 'completed';
  candidate.name = values.product_name || candidate.name || '商品信息待确认';
  candidate.type = [values.tea_category, values.tea_subtype, values.roast_or_style].filter(Boolean).join(' · ') || candidate.type || '信息待确认';
  candidate.riskFlags = GuanchaAdapters.safeExtractionRiskFlags(extraction);
  const sourceImageIds = new Set(extraction.source_image_ids || [extraction.source_image_id].filter(Boolean));
  candidate.images = (candidate.images || []).map((image) => {
    if (!sourceImageIds.has(image.serverImageId)) return image;
    pendingImageStore.remove(image.id);
    return { ...image, status: 'completed', localOnly: false };
  });
}
function applySessionDecision(decision) {
  const activeId = candidateIdentity(currentCandidate());
  const decisions = Array.isArray(decision?.candidate_decisions) ? decision.candidate_decisions.slice() : [];
  const byCandidateId = new Map(state.candidates.map((candidate) => [candidate.serverCandidateId, candidate]));
  const decidedIds = new Set();
  decisions.sort((left, right) => Number(left.overall_order || 0) - Number(right.overall_order || 0)).forEach((item) => {
    const candidate = byCandidateId.get(item.candidate_id);
    if (!candidate) return;
    candidate.decision = item;
    decidedIds.add(item.candidate_id);
  });
  // The server owns decision order.  Candidates still awaiting extraction keep
  // their relative position after the returned, ordered decision rows.
  state.candidates = [
    ...decisions.map((item) => byCandidateId.get(item.candidate_id)).filter(Boolean),
    ...state.candidates.filter((candidate) => !decidedIds.has(candidate.serverCandidateId)),
  ];
  syncActiveCandidate(activeId);
}
function clearStaleRemoteSelection() {
  state.sessionId = null;
  state.decisionVersionId = null;
  state.decisionJobId = null;
  state.decisionStatus = 'not_requested';
  state.candidates.forEach((candidate) => {
    candidate.serverCandidateId = null;
    candidate.serverImageId = null;
    candidate.jobId = null;
    candidate.extraction = null;
    candidate.extractionVersionId = null;
    candidate.extractionStatus = 'queued';
    candidate.jobError = null;
    candidate.decision = null;
    candidate.riskFlags = [];
    candidate.images = (candidate.images || []).map((image) => ({
      ...image,
      serverImageId: null,
      localOnly: true,
      status: 'queued',
    }));
  });
}
function mvpDecision(candidate) {
  if (!candidate?.decision) return null;
  const labels = GuanchaAdapters.actionLabels;
  return {
    action: labels[candidate.decision.action_bucket] || labels['insufficient-information'],
    reasons: (candidate.decision.reasons || []).slice(0, 3),
  };
  /* Historical prototype scoring is intentionally disabled. It remains only
     to avoid a broad encoding-sensitive rewrite of the historical prototype.
  const facts = evidenceByField(candidate.extraction);
  const reasons = [];
  if (facts.tea_category) reasons.push(`已识别茶类：${facts.tea_category}`);
  if (facts.taste_claim || facts.taste_claims) reasons.push('商品页提供了口感描述，可与本次偏好对照');
  if (facts.origin || facts.grade || facts.year_or_harvest) reasons.push('商品页提供了部分来源或等级信息');
  if (!reasons.length) reasons.push('可确认信息较少，建议先核对商品详情');
  const hasRisk = (candidate.riskFlags || []).length > 0;
  return { action: hasRisk ? '建议补充信息' : '可以考虑', reasons: reasons.slice(0, 3) };
  */
}
async function startMvpAnalysis({ recoveredMissingSession = false } = {}) {
  await pendingImageRestore;
  if (!validateAnalysisCandidates()) return;
  if (!apiClient.isConfigured) return showToast('请先启动本地后端服务');
  try {
    state.screen = 'analysis';
    state.decisionStatus = 'not_requested';
    state.candidates.forEach(candidate => { if (candidate.extractionStatus !== 'completed') candidate.extractionStatus = 'uploading'; });
    render();
    const session = state.sessionId
      ? await apiClient.updateSelectionSession(state.sessionId, apiNeed(), readPreferenceEvidence())
      : await apiClient.createSelectionSession(apiNeed(), undefined, readPreferenceEvidence());
    state.sessionId = session.id;
    for (const candidate of state.candidates) {
      try {
        const firstRuntime = candidate.images[0] && runtimeImages.get(candidate.images[0].id);
        if (!candidate.serverCandidateId) {
          const created = await apiClient.createCandidate(session.id, {
            display_label: candidate.letter || 'A',
            display_name: candidate.name || firstRuntime?.file?.name?.replace(/\.[^.]+$/, '') || candidate.name || '候选茶',
          });
          candidate.serverCandidateId = created.id;
        }
        for (let index = 0; index < candidate.images.length; index += 1) {
          const localImage = candidate.images[index];
          const runtime = runtimeImages.get(localImage.id);
          if (localImage.serverImageId || !runtime?.file) continue;
          const uploaded = await apiClient.uploadCandidateImage(candidate.serverCandidateId, runtime.file);
          candidate.serverImageId = uploaded.image.id;
          candidate.jobId = uploaded.extraction_job.id;
          candidate.images[index] = { ...localImage, serverImageId: uploaded.image.id, status: uploaded.image.status, localOnly: false };
          candidate.extractionStatus = uploaded.extraction_job.status;
          startCandidatePolling(candidate);
        }
      } catch (error) {
        // Candidate jobs are independent: a failed upload must never erase or
        // mark another candidate's successful/processing job as failed.
        candidate.extractionStatus = 'failed';
        candidate.jobError = error?.code || 'network_error';
      }
    }
    saveState(); render();
    // The first analysis request is a server-side dispatch barrier: it starts
    // exactly one staged extraction Job per candidate.  Candidate pollers
    // then invoke the same endpoint again only after all extractions finish,
    // which preserves the existing Decision API contract.
    if (state.candidates.some(candidate => candidate.jobId && candidate.extractionStatus === 'queued')) {
      await apiClient.analyzeSelectionSession(state.sessionId);
    } else if (state.candidates.length && state.candidates.every(candidate => candidate.extractionStatus === 'completed')) {
      // A hard refresh can restore completed candidate extractions without an
      // active candidate poller. Reattach the existing decision flow instead
      // of leaving the analysis screen without a tracked decision Job.
      await maybeStartSessionDecision();
    }
  } catch (error) {
    if (error?.code === 'selection_session_not_found' && !recoveredMissingSession) {
      clearStaleRemoteSelection();
      saveState();
      return startMvpAnalysis({ recoveredMissingSession: true });
    }
    state.candidates.forEach(candidate => { if (candidate.extractionStatus !== 'completed') { candidate.extractionStatus = 'failed'; candidate.jobError = error.code || 'network_error'; } });
    saveState(); render();
  }
}
function startCandidatePolling(candidate) {
  if (!candidate?.jobId || !candidate?.serverCandidateId) return;
    GuanchaJobPoller.start({
      jobId: candidate.jobId,
      resourceId: candidate.serverCandidateId,
      versionId: candidate.jobId,
      fetchStatus: apiClient.getJob,
      getCurrentVersion: () => candidate.jobId,
      onUpdate: async (job) => {
        candidate.extractionStatus = job.status;
        candidate.jobError = job.error_code || null;
        if (job.status === 'completed' && job.extraction_version_id) {
          try {
            applyExtraction(candidate, await apiClient.getCurrentExtraction(candidate.serverCandidateId));
            saveState(); render(); await maybeStartSessionDecision();
          } catch { candidate.extractionStatus = 'failed'; candidate.jobError = 'result_unavailable'; saveState(); render(); }
          return;
        }
        if (job.status === 'failed') { saveState(); render(); }
        else { saveState(); render(); }
      },
    });
}
async function resumeLiveBackendState() {
  if (!apiClient.isConfigured || !state.sessionId) return;
  try {
    const snapshot = await apiClient.getSelectionSnapshot(state.sessionId);
    const activeId = state.activeCandidateId || candidateIdentity(currentCandidate());
    const recoveryScreen = GuanchaAdapters.activeRecoveryScreen(snapshot);
    const pendingLocal = state.candidates.filter(candidate => !candidate.serverCandidateId && (candidate.images || []).some(image => image.localOnly));
    state.candidates = snapshot.candidates.map((remote, index) => ({
      letter: remote.display_label || String.fromCharCode(65 + index), name: remote.display_name || `候选茶 ${remote.display_label || index + 1}`,
      type: '商品信息整理中', fields: '', serverCandidateId: remote.id,
      images: (remote.images || []).map(image => ({ id: `server-${image.id}`, serverImageId: image.id, status: image.status, localOnly: false })),
      serverImageId: remote.images?.at(-1)?.id || null, jobId: remote.images?.at(-1)?.current_job_id || null,
      extractionStatus: remote.current_extraction?.status || remote.images?.at(-1)?.current_job_status || remote.images?.at(-1)?.status || 'queued', extraction: null,
    })).concat(pendingLocal);
    syncActiveCandidate(activeId);
    const serverNeed = snapshot.session?.need || {};
    state.need = { taste: serverNeed.taste_text || '', purpose: serverNeed.purpose_text || '', budget: serverNeed.budget_text || '' };
    state.decisionVersionId = snapshot.current_decision_id || null;
    state.selectionAnswer = null;
    // The server owns question/reply/rejudge progress. Browser storage only
    // caches presentation state and must not decide aggregate readiness.
    state.followupQuestions = snapshot.questions || [];
    state.questionDecisionVersionId = snapshot.question_decision_version_id || null;
    state.merchantReplyIds = Object.fromEntries((snapshot.merchant_replies || []).map(reply => [reply.followup_question_id, reply.id]));
    state.merchantReplies = Object.fromEntries((snapshot.merchant_replies || []).map(reply => [reply.followup_question_id, reply]));
    state.lastDecisionDelta = snapshot.decision_delta || null;
    const sessionDecisionJob = snapshot.session_decision_job || null;
    state.decisionJobId = ['queued', 'processing'].includes(sessionDecisionJob?.status) ? sessionDecisionJob.id : null;
    if (['failed', 'stale'].includes(sessionDecisionJob?.status)) {
      state.decisionStatus = 'failed';
      state.decisionError = sessionDecisionJob.error_code || 'decision_failed';
    }
    state.rejudgeJobId = ['queued', 'processing'].includes(snapshot.rejudge_job?.status) ? snapshot.rejudge_job.id : null;
    if (state.rejudgeJobId) state.questionStatus = 'rejudging';
    else if (snapshot.decision_delta || (snapshot.question_decision_version_id && snapshot.question_decision_version_id !== snapshot.current_decision_id)) state.questionStatus = 'completed';
    else if (state.followupQuestions.length) {
      const questionIds = new Set(state.followupQuestions.map(item => item.id));
      state.questionStatus = [...questionIds].every(id => state.merchantReplyIds[id]) ? 'ready' : 'completed';
    } else if (snapshot.question_generation_status === 'completed') state.questionStatus = 'not-needed';
    else if (snapshot.question_generation_status === 'failed') state.questionStatus = 'failed';
    else state.questionStatus = 'idle';
    // Only a reload of an explicitly active selection flow may resume a
    // transient screen. A normal reopen stays on Home even if cached server
    // identifiers still exist.
    if (state.activeSelectionFlow === true && state.screen !== 'home') state.screen = recoveryScreen;
  } catch (error) {
    if (error?.code === 'selection_session_not_found') clearStaleRemoteSelection();
    return;
  }
  for (const candidate of state.candidates) {
    if (!candidate?.serverCandidateId) continue;
    if (['queued', 'processing'].includes(candidate.extractionStatus) && candidate.jobId) startCandidatePolling(candidate);
    if (candidate.extractionStatus === 'completed' && !candidate.extraction) {
      try { applyExtraction(candidate, await apiClient.getCurrentExtraction(candidate.serverCandidateId)); } catch { candidate.extractionStatus = 'failed'; candidate.jobError = 'result_unavailable'; }
    }
  }
  // A completed server Decision is authoritative.  A browser refresh can
  // otherwise retain an obsolete job id and keep the user on “分析中” even
  // though the result has already been persisted.
  if (state.decisionVersionId) {
    state.decisionJobId = null;
    try {
      const decision = await apiClient.getCurrentDecision(state.sessionId);
      applySessionDecision(decision);
      await refreshSelectionAnswer();
      state.decisionStatus = 'ready';
      // A refresh has no reliable visual history: normalizeState deliberately
      // returns transient screens to candidates.  Once the server confirms a
      // current Decision, resume the useful destination instead of presenting
      // an already-completed selection as if it still needs analysis.
      if (state.screen === 'candidates') state.screen = 'result';
    } catch { state.decisionStatus = 'failed'; }
  } else if (state.decisionJobId) {
    state.decisionStatus = 'loading';
    startDecisionPolling(state.decisionJobId);
  }
  if (state.rejudgeJobId) startRejudgePolling(state.rejudgeJobId);
  saveState(); render();
}
function fitLabel(candidate, answerCandidate) {
  const bucket = candidate?.decision?.action_bucket;
  if (bucket === 'not-recommended-now') return '目前不优先考虑';
  if (bucket === 'insufficient-information') return '目前还看不清实际风格';
  // A server order can be driven by evidence sufficiency or trial cost.  It
  // must not be presented as a taste win when the explicit need found no
  // positive match for either candidate.
  if (GuanchaAdapters.sensoryNeedMatch(candidate?.decision) <= 0) return '口味方向暂未分出高下';
  if (Number(candidate?.decision?.overall_order) === 1) return '当前更接近你的方向';
  if ((answerCandidate?.sensory_interpretations || []).length) return '更偏另一种风格';
  return answerCandidate?.verdict || '目前还需要更多线索';
}
async function maybeStartSessionDecision() {
  if (!state.sessionId || state.decisionJobId || !state.candidates.length || !state.candidates.every(candidate => candidate.extractionStatus === 'completed')) return;
  try {
    state.decisionStatus = 'loading';
    const job = await apiClient.analyzeSelectionSession(state.sessionId);
    state.decisionJobId = job.id; saveState();
    startDecisionPolling(job.id);
  } catch (error) { state.decisionStatus = 'failed'; state.decisionError = error.code || 'decision_failed'; saveState(); render(); }
}
function startRejudgePolling(jobId) {
  if (!jobId || !state.sessionId) return;
  GuanchaJobPoller.start({
    jobId, resourceId: state.sessionId, versionId: jobId, fetchStatus: apiClient.getJob,
    getCurrentVersion: () => state.rejudgeJobId,
    onUpdate: async status => {
      if (status.status === 'completed') {
        state.rejudgeJobId = null;
        await resumeLiveBackendState();
        setScreen('rejudge');
      } else if (status.status === 'failed') {
        state.rejudgeJobId = null;
        state.questionStatus = 'failed';
        state.rejudgeError = status.error_code || 'rejudge_failed';
        saveState(); render();
      }
    },
  });
}
function startDecisionPolling(jobId) {
  if (!jobId || !state.sessionId) return;
  GuanchaJobPoller.start({ jobId, resourceId: state.sessionId, versionId: jobId, fetchStatus: apiClient.getJob, getCurrentVersion: () => state.decisionJobId, onUpdate: async status => {
      if (status.status === 'completed' && status.decision_version_id) {
        const decision = await apiClient.getCurrentDecision(state.sessionId);
        state.decisionVersionId = decision.id;
        applySessionDecision(decision);
        await refreshSelectionAnswer();
        state.decisionJobId = null; state.decisionStatus = 'ready'; saveState(); setScreen('result');
      } else if (status.status === 'failed') { state.decisionJobId = null; state.decisionStatus = 'failed'; state.decisionError = status.error_code || 'decision_failed'; saveState(); render(); }
    }});
}

async function openFollowupQuestions() {
  if (!state.decisionVersionId || !apiClient.isConfigured) return showToast('请先完成本轮分析');
  state.overlay = 'ask'; state.questionStatus = 'loading'; render();
  try {
    let questions = await apiClient.getDecisionQuestions(state.decisionVersionId);
    if (!questions.length) questions = await apiClient.generateDecisionQuestions(state.decisionVersionId);
    state.followupQuestions = questions;
    state.questionDecisionVersionId = state.decisionVersionId;
    // Reopening the sheet must preserve the aggregate-rejudge readiness that
    // was earned by replies saved before this render.
    const requiredQuestionIds = new Set(questions.map(item => item.id));
    state.questionStatus = requiredQuestionIds.size === 0
      ? 'not-needed'
      : [...requiredQuestionIds].every(id => state.merchantReplyIds?.[id]) ? 'ready' : 'completed';
    productAnalytics.track('merchant_question_viewed', { candidate_id: currentCandidate()?.serverCandidateId, decision_version_id: state.decisionVersionId, metadata: { question_count: questions.length, screen: state.screen } });
    saveState(); render();
  } catch (error) {
    state.questionStatus = error.code === 'decision_stale' ? 'stale' : 'failed';
    state.followupQuestions = []; saveState(); render();
  }
}
function replyNeedsClarification(reply) {
  return ['partially-answered', 'evasive', 'not-answered', 'conflicting'].includes(reply?.parse_status);
}
function replyGuidance(question) {
  const answer = String(question?.reply?.raw_text || '').trim();
  const quoted = answer ? `“${answer}”` : '这条回复';
  if (question?.fieldKey === 'roast_level') {
    return `商家回复${quoted}，但未能确认具体焙火等级。请追问：这款是轻火、 中火还是足火？`;
  }
  if (question?.fieldKey === 'sample_available') {
    return `商家回复${quoted}，但仍无法确认是否能购买或领取试饮小样。请追问：是否提供小样 / 试饮装？`;
  }
  if (question?.fieldKey === 'season') {
    return `商家回复${quoted}，但未能确认具体采摘季节。请追问：这是春茶、秋茶，还是其他批次？`;
  }
  return `商家回复${quoted}，但还不足以确认“${question?.fieldLabel || '这项信息'}”。请让商家直接说明这项信息。`;
}
async function submitMerchantReply(rawText) {
  const questions = (state.followupQuestions || []).filter(item => item.candidate_id === currentCandidate()?.serverCandidateId && (!state.merchantReplyIds?.[item.id] || replyNeedsClarification(state.merchantReplies?.[item.id])));
  const question = questions[0];
  if (!question || !state.sessionId || !state.decisionVersionId || !apiClient.isConfigured) return showToast('请先生成当前问题');
  try {
    state.questionStatus = 'submitting'; render();
    const reply = await apiClient.createMerchantReply(state.sessionId, {
      decision_version_id: state.decisionVersionId, followup_question_id: question.id, raw_text: rawText,
    });
    state.merchantReplyIds = state.merchantReplyIds || {};
    state.merchantReplies = state.merchantReplies || {};
    state.merchantReplyIds[question.id] = reply.id;
    state.merchantReplies[question.id] = reply;
    const requiredQuestionIds = new Set((state.followupQuestions || []).map(item => item.id));
    state.questionStatus = [...requiredQuestionIds].every(id => state.merchantReplyIds[id]) ? 'ready' : 'completed';
    saveState(); render();
    if (state.questionStatus !== 'ready') return showToast('商家回复已保存，请继续补充其他候选茶的回复');
    showToast('全部待回复候选茶已保存，可更新本轮判断');
  } catch (error) { state.questionStatus = 'failed'; state.rejudgeError = error.code || 'rejudge_failed'; saveState(); render(); }
}
async function refreshSelectionAnswer() {
  if (!state.sessionId || !apiClient.isConfigured || !apiClient.getSelectionAnswer) return;
  try { state.selectionAnswer = await apiClient.getSelectionAnswer(state.sessionId); } catch { state.selectionAnswer = null; }
}
async function updateMerchantJudgement() {
  const replyIds = Object.values(state.merchantReplyIds || {});
  if (!replyIds.length || !state.sessionId || !apiClient.isConfigured) return showToast('请先保存商家回复');
  try {
    state.questionStatus = 'rejudging'; render();
    const job = await apiClient.rejudgeMerchantReply(state.sessionId);
    state.rejudgeJobId = job.id; state.questionStatus = 'rejudging'; saveState(); render();
    GuanchaJobPoller.start({ jobId: job.id, resourceId: state.sessionId, versionId: job.id, fetchStatus: apiClient.getJob, getCurrentVersion: () => state.rejudgeJobId, onUpdate: async status => {
      if (status.status === 'completed' && status.decision_version_id) {
        const decision = await apiClient.getCurrentDecision(state.sessionId);
        state.decisionVersionId = decision.id;
        applySessionDecision(decision);
        await refreshSelectionAnswer();
        const candidate = currentCandidate();
        if (candidate?.serverCandidateId) applyExtraction(candidate, await apiClient.getCurrentExtraction(candidate.serverCandidateId));
        state.deltaStatus = status.decision_delta_id ? 'loading' : 'unavailable'; state.screen = 'rejudge'; state.overlay = null; render();
        try {
          state.lastDecisionDelta = status.decision_delta_id ? await apiClient.getDecisionDelta(status.decision_delta_id) : null;
          state.deltaStatus = state.lastDecisionDelta ? 'completed' : 'unavailable';
        } catch (error) {
          state.lastDecisionDelta = null;
          state.deltaStatus = error.code === 'resource_not_owned' ? 'denied' : 'unavailable';
        }
        state.rejudgeJobId = null; state.questionStatus = 'completed'; state.overlay = null; saveState(); setScreen('rejudge');
      } else if (status.status === 'failed') { state.rejudgeJobId = null; state.questionStatus = status.error_code === 'candidate_extraction_not_retryable' ? 'not-actionable' : 'failed'; state.rejudgeError = status.error_code || 'rejudge_failed'; saveState(); render(); }
    }});
  } catch (error) { state.questionStatus = 'failed'; state.rejudgeError = error.code || 'rejudge_failed'; saveState(); render(); }
}
async function retryMvpAnalysis() {
  const candidate = currentCandidate();
  if (!candidate?.serverCandidateId || !apiClient.isConfigured) return startMvpAnalysis();
  if (candidate.extractionStatus === 'failed') return reuploadMvpAnalysis();
  try {
    const job = await apiClient.retryExtraction(candidate.serverCandidateId);
    candidate.jobId = job.id; candidate.extractionStatus = job.status; candidate.jobError = null; saveState(); setScreen('analysis');
    GuanchaJobPoller.start({ jobId: job.id, resourceId: candidate.serverCandidateId, versionId: job.id, fetchStatus: apiClient.getJob, getCurrentVersion: () => candidate.jobId, onUpdate: async status => {
      candidate.extractionStatus = status.status;
      if (status.status === 'completed' && status.extraction_version_id) { applyExtraction(candidate, await apiClient.getCurrentExtraction(candidate.serverCandidateId)); saveState(); setScreen('result'); }
      else if (status.status === 'failed') { candidate.jobError = status.error_code; saveState(); render(); }
    }});
  } catch (error) { candidate.extractionStatus = 'failed'; candidate.jobError = error.code || 'network_error'; saveState(); render(); }
}
async function reuploadMvpAnalysis() {
  const candidate = currentCandidate();
  if (!candidate?.serverCandidateId || !candidate.serverImageId) return startMvpAnalysis();
  try {
    // Keep the session and candidate, but replace its single P0 image only.
    const deletedImageId = candidate.serverImageId;
    try { await apiClient.deleteCandidateImage(deletedImageId); } catch { /* old failed image expires with its session */ }
    candidate.serverImageId = null; candidate.jobId = null;
    candidate.extraction = null; candidate.extractionVersionId = null;
    candidate.extractionStatus = 'queued'; candidate.jobError = null;
    const deletedImageIndex = candidate.images.findIndex(image => image.serverImageId === deletedImageId);
    if (deletedImageIndex >= 0) candidate.images[deletedImageIndex] = { ...candidate.images[deletedImageIndex], serverImageId: null, localOnly: true, status: 'queued' };
    saveState();
    return startMvpAnalysis();
  } catch (error) {
    candidate.jobError = error.code || 'network_error'; saveState(); render();
  }
}
function asset(name) { return `assets/ui/${encodeURIComponent(name)}`; }
function wordmark(file, className, label) {
  return `<img class="wordmark ${className}" src="assets/ui/wordmarks-v2/${file}" alt="${label}" />`;
}
function desktopWordmark(file, className, label) {
  return `<div class="desktop-wordmark ${className}"><span>${escapeHtml(label)}</span></div>`;
}
function icon(name, size = 24) {
  const common = `width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"`;
  const paths = {
    back: '<path d="m15 18-6-6 6-6"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    up: '<path d="m18 15-6-6-6 6"/>',
    close: '<path d="m18 6-12 12M6 6l12 12"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>',
    camera: '<path d="M4 7h3l1.5-2h7L17 7h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z"/><circle cx="12" cy="13" r="3"/>',
    photo: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.3"/><path d="m21 15-4.5-4.5L8 19"/>',
    copy: '<rect x="8" y="8" width="11" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/>',
    leaf: '<path d="M20 4C10 4 4 9 4 18c8 0 15-5 16-14Z"/><path d="M4 18c4-4 8-6 12-8"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v17H6.5A2.5 2.5 0 0 0 4 22Z"/><path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v17h4.5A2.5 2.5 0 0 1 20 22Z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c.7-4.2 3.4-6 8-6s7.3 1.8 8 6"/>',
    pot: '<path d="M5 10h14l-1 8H6Z"/><path d="M8 10c0-3 8-3 8 0M3 12h2M19 12h2M9 18h6"/><path d="M10 5h4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    jar: '<path d="M7 5h10M8 3h8M7 7v12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V7"/><path d="M9 11h6"/>',
    play: '<path d="m9 5 10 7-10 7Z" fill="currentColor" stroke="none"/>',
    pause: '<path d="M8 6v12M16 6v12"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    chevron: '<path d="m9 18 6-6-6-6"/>',
  };
  return `<svg ${common}>${paths[name] || paths.leaf}</svg>`;
}
function escapeHtml(value = '') { return String(value).replace(/[&<>'"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[c])); }
function showToast(message) {
  toast.textContent = message; toast.classList.add('show');
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.classList.remove('show'), 1800);
}
function selectedDrinkCount(key) { return state.o1[key]?.length || 0; }
function hasAnyO1() { return Object.values(state.o1).some(items => items.length); }
function currentCandidate() { return state.candidates[state.activeCandidate] || null; }
function isRejudged() { return state.screen === 'rejudge'; }
function trackResultView() {
  if (!['result', 'rejudge'].includes(state.screen)) return false;
  const edge = [state.screen, currentCandidate()?.serverCandidateId || currentCandidate()?.id || 'none', state.decisionVersionId || 'none'].join(':');
  if (edge === lastResultAnalyticsEdge) return false;
  lastResultAnalyticsEdge = edge;
  return productAnalytics.track('candidate_result_viewed', {
    candidate_id: currentCandidate()?.serverCandidateId,
    decision_version_id: state.decisionVersionId || undefined,
    metadata: { candidate_count: state.candidates.length, screen: state.screen },
  });
}
function candidateIdentity(candidate) { return GuanchaAdapters.candidateIdentity(candidate); }
function syncActiveCandidate(anchor = state.activeCandidateId) {
  state.activeCandidate = GuanchaAdapters.resolveActiveCandidateIndex(state.candidates, anchor, state.activeCandidate);
  state.activeCandidateId = candidateIdentity(state.candidates[state.activeCandidate]);
}

function render() {
  // Most interactions re-render the current screen so that selected states
  // are reflected immediately. Keep the scroll position of that same screen;
  // navigation still starts at the top because the screen name changes.
  const previousPage = app.querySelector('.page');
  const previousScrollTop = previousPage?.scrollTop || 0;
  const preserveScroll = lastRenderedScreen === state.screen;
  const templates = {
    home: renderHome,
    candidates: renderCandidates,
    o1: renderO1,
    o2: renderO2,
    analysis: renderAnalysis,
    result: renderResult,
    rejudge: renderResult,
    ownership: renderOwnership,
    warehouse: renderWarehouse,
    'warehouse-detail': renderWarehouseDetail,
    'warehouse-add': renderWarehouseAdd,
    journal: renderJournal,
    'journal-day': renderJournalDay,
    'choose-tea': renderChooseTea,
    prepare: renderPrepare,
    timer: renderTimer,
    'infusion-done': renderInfusionDone,
    feedback: renderFeedback,
    advanced: renderAdvanced,
    'brew-result': renderBrewResult,
    'record-detail': renderRecordDetail,
    settings: renderSettings,
    stub: renderStub,
  };
  app.innerHTML = (templates[state.screen] || renderHome)() + renderOverlay();
  lastRenderedScreen = state.screen;
  if (!preserveScroll) trackResultView();
  if (preserveScroll && previousScrollTop > 0) {
    requestAnimationFrame(() => {
      const page = app.querySelector('.page');
      if (page) page.scrollTop = previousScrollTop;
    });
  }
  if (state.overlay === 'ask' && ['completed', 'ready'].includes(state.questionStatus) && merchantQuestions(currentCandidate()).length) appendMerchantReplyForm();
  if (state.overlay === 'ask' && state.questionStatus === 'completed') {
    const privacy = app.querySelector('.ask-sheet .privacy');
    if (privacy) privacy.textContent = '商家信息会先单独保存，全部候选茶回复齐后再统一更新判断；仍属于未核验声明。';
    const tip = app.querySelector('.ask-sheet .ask-tip');
    if (tip) tip.textContent = '商家回复仅用于补足当前判断；不会在单条回复后提前复判。';
  }
  if (state.overlay === 'ask' && state.questionStatus === 'ready') {
    const privacy = app.querySelector('.ask-sheet .privacy');
    if (privacy) privacy.textContent = '商家回复已全部保存；确认后将统一更新本轮判断。';
    const tip = app.querySelector('.ask-sheet .ask-tip');
    if (tip) tip.textContent = '所有待回复候选茶已齐，可进行一次统一复判。';
  }
  if (state.overlay === 'ask' && state.questionStatus === 'not-actionable') {
    const note = app.querySelector('.ask-sheet .privacy');
    if (note) note.textContent = '商家回复未形成可用的新信息，原判断保持。';
    const tip = app.querySelector('.ask-sheet .ask-tip');
    if (tip) tip.textContent = '商家回复已处理，当前不需要再次复判。';
  }
  cleanRenderedArts();
  bindResultSwipe();
  if (state.overlay === 'camera') requestAnimationFrame(startCamera);
  syncBrewTimer();
}

function cleanRenderedArts() {
  app.querySelectorAll('.tea-art-frame > img').forEach((img) => {
    if (img.getAttribute('src')?.includes('-clean.png')) {
      img.dataset.checkerCleaned = 'asset';
      return;
    }
    const clean = () => {
      if (img.dataset.checkerCleaned) return;
      try {
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.drawImage(img, 0, 0);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
        for (let index = 0; index < pixels.data.length; index += 4) {
          const red = pixels.data[index];
          const green = pixels.data[index + 1];
          const blue = pixels.data[index + 2];
          const neutral = Math.max(red, green, blue) - Math.min(red, green, blue) < 6;
          const isCheckerTone = red > 246 || (red >= 214 && red <= 235);
          if (neutral && isCheckerTone) pixels.data[index + 3] = 0;
        }
        context.putImageData(pixels, 0, 0);
        img.dataset.checkerCleaned = 'true';
        img.src = canvas.toDataURL('image/png');
      } catch {
        img.dataset.checkerCleaned = 'fallback';
      }
    };
    if (img.complete && img.naturalWidth) clean();
    else img.addEventListener('load', clean, { once: true });
  });
}

function greeting() {
  return `<header class="home-greeting" data-action="open-preferences" role="button" tabindex="0" aria-label="打开口味偏好">
    <span class="greeting-avatar" aria-hidden="true"></span>
    <div><div class="greeting-title">Hi，茶友</div><div class="greeting-copy">慢慢看，帮你找到今天适合的茶。</div></div>
    <div class="greeting-arrow">›</div>
  </header>`;
}
function tabbar() {
  const activeMap = { home: 'select', journal: 'journal', 'journal-day': 'journal', warehouse: 'warehouse', 'warehouse-detail': 'warehouse', 'warehouse-add': 'warehouse', settings: 'settings' };
  const active = activeMap[state.screen] || (state.screen === 'stub' ? state.stubTab : '');
  const items = [['select','选茶','leaf'], ['journal','泡茶日记','pot'], ['warehouse','茶仓库','jar'], ['settings','设置','user']];
  return `<nav class="tabbar" aria-label="主导航">${items.map(([key,label,svg]) => `<button class="tab ${active === key ? 'active' : ''}" data-action="tab" data-tab="${key}">${icon(svg, 27)}<span>${label}</span></button>`).join('')}</nav>`;
}
function historyCard(item, index) {
  const artClass = index === 1 ? 'ref-art--history-2' : index === 2 ? 'ref-art--history-3' : 'ref-art--history';
  return `<button class="history-item" data-action="open-history" aria-label="查看选茶记录">
    <span class="history-art ref-art ${artClass}" aria-hidden="true"></span>
    <div><div class="history-meta">${item.date}</div><div class="history-name">选茶结果</div><div class="history-status">AI 优先候选${escapeHtml(item.recommended_candidate_label || '未记录')} · 你选择候选${escapeHtml(item.selected_candidate_label || '未记录')}</div></div>
    <span class="history-arrow">›</span>
  </button>`;
}
function renderHome() {
  const filled = state.history.length > 0;
  const content = filled ? `<div class="history-list">${state.history.slice(0,3).map(historyCard).join('')}</div>` : `<div class="empty-record"><span class="home-scene" role="img" aria-label="观茶插画"></span><h3>还没有选茶记录</h3><p>开始你的第一次选茶吧</p></div>`;
  return `<section class="page home-page" aria-label="选茶首页">${greeting()}${wordmark('select-tea.svg', 'wordmark--select-tea', '选茶')}<p class="title-sub">帮你选到适合自己的茶</p>
  <section class="record-panel card"><div class="panel-head"><span>选茶记录</span>${filled ? '<button class="view-all" data-action="open-history">查看全部 ›</button>' : ''}</div>${content}<button class="primary-btn record-action" data-action="start-task">开始选茶</button></section>${tabbar()}</section>`;
}

function needsChips() { return `<div class="need-chips"><span class="need-chip">${escapeHtml(state.need.taste)}</span><span class="need-chip">${escapeHtml(state.need.purpose)}</span><span class="need-chip">预算 ${escapeHtml(state.need.budget)}</span></div>`; }
function needCard({ editable = true, className = '' } = {}) {
  return `<section class="need-card card ${className}"><div class="need-head"><span>本次需求</span>${editable ? `<button class="edit-need" data-action="open-need-edit">${icon('edit',17)}可编辑</button>` : ''}</div>${needsChips()}<span class="leaf-float">${icon('leaf',34)}</span></section>`;
}
function renderCandidates() {
  const has = state.candidates.length > 0;
  const list = has ? `<div class="candidate-list">${state.candidates.map((candidate, index) => candidateRow(candidate, index)).join('')}</div><p class="candidate-more">还可添加 ${candidateLimit() - state.candidates.length} 款候选茶</p>` : `<div class="candidate-empty"><span class="candidate-scene" role="img" aria-label="候选茶分析插画"></span><h3>还没有候选茶</h3><p>点击右下角 +，把你正在纠结的茶放进来。<br>观茶会帮你看清它们对你有什么不同。</p></div>`;
  return `<section class="page page-tight" aria-label="添加候选"><button class="icon-btn back-btn" data-action="go-home" aria-label="返回">${icon('back')}</button>${wordmark('add-candidate.svg', 'wordmark--add-candidate', '添加候选')}${needCard()}
  <section class="candidate-panel card no-offset"><h2 class="candidate-panel-title">候选茶 <span>(${state.candidates.length}/${candidateLimit()})</span></h2>${list}</section>
  <div class="bottom-actions"><button class="primary-btn" data-action="start-analysis" ${has ? '' : 'disabled'}>开始分析${has ? ` ${state.candidates.length} 款茶` : ''}</button><button class="add-round" data-action="open-source" aria-label="添加候选茶">+</button></div></section>`;
}
function candidateRow(candidate, index) {
  const images = Array.isArray(candidate.images) ? candidate.images : [];
  const firstImage = images[0] && runtimeImages.get(images[0].id);
  const previewUrl = firstImage?.url || images[0]?.previewUrl;
  const visual = previewUrl ? `<img class="candidate-image-thumb" src="${previewUrl}" alt="候选 ${candidate.letter} 已暂存图片" />` : `<span class="candidate-art ref-art ${candidate.letter === 'B' ? 'ref-art--gaiwan' : 'ref-art--can'}" role="img" aria-label="${escapeHtml(candidate.name)}的小插图"></span>`;
  const addImage = images.length < imageLimit() ? `<button class="candidate-image-add" data-action="add-candidate-image" data-candidate-id="${escapeHtml(candidate.id)}" aria-label="为候选${candidate.letter}补第${images.length + 1}张图片">+</button>` : '';
  const readText = candidate.extractionStatus === 'completed' ? `已加入比较 · ${images.length} 张商品截图` : `已暂存 ${images.length}/${imageLimit()} 张商品截图`;
  const extractText = candidate.extractionStatus === 'completed' ? '已整理商品信息与风格线索' : '等待整理这款茶与你需求有关的差异';
  return `<article class="candidate-row"><span class="candidate-image-slot">${visual}${addImage}</span><div><div class="candidate-name">${escapeHtml(candidate.name)}</div><div class="candidate-type">${escapeHtml(candidate.type)}</div><div class="candidate-read">${readText}</div><div class="candidate-extract">${extractText}</div></div><button class="remove-candidate" data-action="remove-candidate" data-index="${index}" aria-label="删除候选${candidate.letter}">×</button></article>`;
}

function renderO1() {
  return `<section class="page preference-page"><button class="icon-btn back-btn" data-action="go-home" aria-label="返回">${icon('back')}</button><div class="progress-mark"><span class="active"></span><span></span></div><h1>你平时喜欢喝什么？</h1><p class="lead">不用懂茶，从你熟悉的饮品开始，让观茶先了解你喜欢什么感觉。</p><div class="preference-categories">${Object.entries(DRINKS).map(([key, info]) => drinkGroup(key, info)).join('')}</div><div class="preference-footer"><button class="primary-btn" data-action="go-o2">下一步</button><button class="skip" data-action="skip-preferences">暂时跳过，稍后再设置</button></div></section>`;
}
async function saveSelectionNeed(nextNeed) {
  try {
    const transition = await GuanchaAdapters.prepareNeedUpdate({
      state, nextNeed, isApiConfigured: apiClient.isConfigured,
      updateRemote: () => apiClient.updateSelectionSession(state.sessionId, apiNeed(nextNeed), readPreferenceEvidence()),
    });
    Object.assign(state, transition);
    state.overlay = null;
    state.screen = 'candidates';
    saveState(); render(); showToast('本次需求已更新，请重新分析候选茶');
  } catch (error) {
    showToast(error?.code === 'selection_session_not_found' ? '本次选择已失效，请返回候选重新开始' : '需求更新失败，请稍后重试');
  }
}
function drinkGroup(key, info) {
  const isOpen = state.openDrink === key;
  const count = selectedDrinkCount(key);
  const toggleLabel = isOpen ? `收起${info.label}` : `展开${info.label}`;
  return `<section class="drink-group"><div class="drink-main"><img class="drink-icon" src="assets/o1-category-icons/${info.icon}" alt="" /><div><div class="drink-main-title">${info.label}</div>${count ? `<div class="drink-count">已选 ${count} 项</div>` : ''}</div>${count ? '<span class="selection-check">✓</span>' : '<span></span>'}<button class="chevron" data-action="toggle-drink" data-key="${key}" aria-label="${toggleLabel}">${isOpen ? '⌄' : '⌃'}</button></div>${isOpen ? `<div class="drink-options">${info.options.map(option => `<button class="option-btn ${state.o1[key].includes(option) ? 'selected' : ''}" data-action="toggle-drink-option" data-key="${key}" data-value="${option}">${option}</button>`).join('')}</div>` : ''}</section>`;
}
function sweetnessLabel(value) { return value === 0 ? '不需要甜感' : value === 25 ? '微微回甜' : value === 50 ? '清甜' : value === 75 ? '明显蜜甜' : '浓郁熟甜'; }
function renderO2() {
  const value = state.o2.sweetness;
  return `<section class="page preference-page"><button class="icon-btn back-btn" data-action="go-o1" aria-label="返回上一步">${icon('back')}</button><div class="progress-mark"><span></span><span class="active"></span></div><h1>风味与口感</h1><p class="lead">这些只是口味参考，之后会用来解释哪款茶更接近你。</p><section class="sweetness-card card"><div class="card-title-row"><span>自然甜感</span><b class="sweet-value">${sweetnessLabel(value)}</b></div><div class="slider-wrap"><input class="sweet-slider" data-action="sweetness" style="--p:${value}%" type="range" min="0" max="100" step="25" value="${value}" aria-label="自然甜感" /><div class="sweet-labels">${[0,25,50,75,100].map(v => `<span class="${v === value ? 'active' : ''}">${sweetnessLabel(v)}</span>`).join('')}</div></div></section><section class="flavor-card card"><div class="card-title-row"><span>风味偏好</span><b class="sweet-value">已选 ${state.o2.flavors.length}/5</b></div><div class="flavor-grid">${FLAVORS.map(flavor => `<button class="flavor-item ${state.o2.flavors.includes(flavor) ? 'selected' : ''}" data-action="toggle-flavor" data-value="${flavor}"><img src="assets/flavors-normalized/${encodeURIComponent(flavor)}.png" alt="" /><span>${flavor}</span></button>`).join('')}</div></section><div class="preference-footer"><button class="primary-btn" data-action="finish-preferences">完成设置</button></div></section>`;
}

function renderAnalysis() {
  const candidate = currentCandidate();
  const failed = candidate?.extractionStatus === 'failed';
  const errorCode = failed && candidate?.jobError ? `<small class="analysis-error-code">错误代码：${escapeHtml(candidate.jobError)}</small>` : '';
  return `<section class="analysis-page" aria-live="polite"><img src="${asset('AI分析等待插画.svg')}" alt="分析等待插画" /><h1>${failed ? '分析未完成' : '正在分析中'}</h1><p>${failed ? '请检查图片或服务后重新上传分析。' : '正在整理这些茶的商品信息、风格线索和与你这次需求有关的差异。'}</p>${errorCode}${failed ? '<button class="primary-btn" data-action="retry-analysis">重新上传并分析</button>' : '<div class="analysis-dots"><i></i><i></i><i></i></div>'}</section>`;
}

function appendMerchantReplyForm() {
  const sheet = app.querySelector('.ask-sheet');
  if (!sheet || sheet.querySelector('[data-action="submit-merchant-reply"]')) return;
  const form = document.createElement('form');
  form.className = 'merchant-reply-form'; form.dataset.action = 'submit-merchant-reply';
  const ready = state.questionStatus === 'ready';
  const currentQuestion = merchantQuestions(currentCandidate()).find(item => !item.reply || replyNeedsClarification(item.reply));
  const needsClarification = replyNeedsClarification(currentQuestion?.reply);
  const targetLabel = currentQuestion ? `（对应：${escapeHtml(currentQuestion.question)}）` : '';
  form.innerHTML = ready && !needsClarification
    ? '<p class="soft-note">所有需要回复的候选茶已保存。确认后统一更新本轮判断。</p><button class="primary-btn" type="button" data-action="update-merchant-judgement">提交并更新判断</button>'
    : `<label>商家回复${targetLabel}</label><textarea name="merchant-reply" required maxlength="4000" placeholder="${needsClarification ? '请只补充当前问题的明确回答' : '粘贴商家对当前问题的回复'}"></textarea><button class="primary-btn" type="submit">${needsClarification ? '补充商家回复' : '提交商家回复'}</button>`;
  sheet.append(form);
}
// Archived composition for reference only. It is deliberately not reachable
// from the competition result page because it contains hand-authored demo text.
function legacyResultDataForDebugOnly(candidate, rejudged) {
  if (!candidate?.decision) return { label: '', additions: [], sections: [] };
  if (rejudged && !candidate.extraction) return { label: '更新后的判断', additions: [], sections: [] };
  if (rejudged && candidate.extraction) {
    const decision = mvpDecision(candidate);
    const merchant = (candidate.extraction.evidence_items || []).filter(item => item.source_type === 'merchant-claim').slice(0, 3);
    return { label: decision.action, additions: merchant.map(item => item.normalized_value || item.raw_text || item.field_name), sections: [
      ['更新后的判断', `<ul>${decision.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>`],
      ['商家补充信息', `<ul>${merchant.map(item => `<li>${escapeHtml(item.normalized_value || item.raw_text || item.field_name)}</li>`).join('') || '<li>商家回复没有形成可用的新信息，原判断保持。</li>'}</ul>`],
    ] };
  }
  if (rejudged) return {
    label: candidate.letter === 'A' ? '本轮推荐' : '可作为备选',
    additions: ['确认：轻火焙制', '确认：2026 年春茶', '确认：支持 10g 试饮装'],
    sections: [
      ['判断变化', `<div class="decision-change"><span>原判断：当前优先关注・先问再买</span><b>→</b><span>更新后：本轮推荐</span></div><p class="soft-note" style="margin:12px 0 0">焙火程度、采摘季节与试饮方式已明确，降低了风格判断和试错成本的不确定性。</p>`],
      ['为什么推荐', `<ul><li>轻火风格更符合“${escapeHtml(state.need.taste)}”的本次需求。</li><li>春茶信息补足后，对鲜爽感的判断更有依据。</li><li>有小样可试，送礼前的试错成本更低。</li></ul>`],
      ['仍待确认', '<ul><li>具体产地仍未说明。</li><li>如在意产区，可购买前再向商家确认。</li></ul>'],
      ['下一步建议', '<ul><li>建议先试饮 10g 小样，确认香气和入口感后再决定是否购买正装。</li></ul>'],
    ],
  };
  const differs = candidate.letter === 'B';
  return {
    label: differs ? '可作为备选' : '当前优先关注・先问再买',
    sections: [
      ['已知事实', `<ul><li>商品页明确写有：${escapeHtml(candidate.type)}。</li><li>已读取：${escapeHtml(candidate.fields)}等信息。</li></ul>`],
      ['匹配推断', `<ul><li>${differs ? '花香调与本次需求有一定契合。' : `清香型与“${escapeHtml(state.need.taste)}”的本次需求更接近。`}</li><li>${differs ? '红茶的熟甜感可能更明显。' : '相较重焙火风格，预计入口负担更轻。'}</li></ul>`],
      ['待确认 / 隐藏风险', `<ul><li>未说明具体焙火程度。</li><li>未说明采摘季节或是否提供试饮装。</li><li>缺失信息会影响对风格与试错成本的判断。</li></ul>`],
      ['下一步建议', '<ul><li>建议先向商家确认焙火程度、采摘季节与是否提供试饮装。</li><li>点击底部“去问商家”获取可复制问题。</li></ul>'],
    ],
  };
}
function resultData(candidate, rejudged) {
  if (!candidate?.decision) return { label: '', additions: [], sections: [] };
  return {
    label: rejudged ? '正在读取更新后的服务端判断' : '正在读取服务端判断',
    additions: [],
    sections: [['当前状态', '<p class="soft-note">当前没有可展示的服务端提取结果。请返回候选页等待分析完成，或重新发起分析。</p>']],
  };
}
function renderDecisionDelta() {
  if (!isRejudged()) return '';
  if (state.deltaStatus === 'loading') return '<section class="result-section"><h3>这次补充对你意味着什么</h3><p class="soft-note">正在读取本次复判的变化说明…</p></section>';
  if (state.deltaStatus === 'denied') return '<section class="result-section"><h3>这次补充对你意味着什么</h3><p class="soft-note">无法读取这次复判的变化说明。</p></section>';
  const delta = state.lastDecisionDelta;
  if (!delta) return '<section class="result-section"><h3>这次补充对你意味着什么</h3><p class="soft-note">本次没有可展示的结构化变化；当前判断保持可见。</p></section>';
  const deltaFieldLabel = (value) => ({
    aroma_style: '香型或焙火方向', roast_level: '焙火程度', price: '到手价格',
    sample_available: '是否可试饮', season: '采摘季节', origin_text: '产地说明',
  })[String(value || '')] || '与本次选择有关的商品信息';
  const changes = [...(delta.added_facts || []), ...(delta.updated_fields || [])]
    .map(deltaFieldLabel).filter((value, index, values) => values.indexOf(value) === index).slice(0, 3);
  const risks = [
    ...(delta.resolved_risks || []).map(value => `风险下降：${value}`),
    ...(delta.added_risks || []).map(value => `新增风险：${value}`),
  ].slice(0, 3);
  const unresolved = (delta.unresolved_fields || []).map(deltaFieldLabel)
    .filter((value, index, values) => values.indexOf(value) === index).slice(0, 2);
  const unchanged = !changes.length && !risks.length && !unresolved.length && !delta.ranking_changed && !delta.action_tier_changed;
  const parts = [];
  if (delta.explanation) parts.push('<p>商家补充的信息已合并进本次判断，下面只保留会影响你选择的变化。</p>');
  if (changes.length) parts.push(`<ul>${changes.map(value => `<li>商家补充了：${escapeHtml(value)}</li>`).join('')}</ul>`);
  if (risks.length) parts.push(`<ul>${risks.map(value => `<li>${escapeHtml(value)}</li>`).join('')}</ul>`);
  if (unresolved.length) parts.push(`<p class="soft-note">商家回复已收到，但其中的表述还不足以形成可用事实，因此没有把它当作已确认信息。</p><ul>${unresolved.map(value => `<li>请补充确认：${escapeHtml(value)}</li>`).join('')}</ul>`);
  if (delta.ranking_changed) parts.push('<p class="soft-note">这些新信息改变了候选之间的比较，当前优先选择已更新。</p>');
  else if (delta.action_tier_changed) parts.push('<p class="soft-note">新信息改变了下一步该先试、先问还是可以考虑购买。</p>');
  else if (changes.length || risks.length || unresolved.length) parts.push('<p class="soft-note">补充信息没有改变当前首选，但让这款为什么更接近或偏离本次需求更明确。</p>');
  if (unchanged) parts.push('<p class="soft-note">本次补充没有改变当前选择，也没有形成足以改变判断的新线索。</p>');
  return `<section class="result-section"><h3>这次补充对你意味着什么</h3>${parts.join('')}</section>`;
}
function renderResult() {
  if (!currentCandidate()) { state.screen = 'candidates'; return renderCandidates(); }
  const candidate = currentCandidate(); const rejudged = isRejudged(); const data = resultData(candidate, rejudged);
  if (rejudged && !candidate.extraction) data.sections.push(['本次判断变化', renderDecisionDelta()]);
  if (candidate.extraction && !candidate.decision) {
    const decisionFailed = state.decisionStatus === 'failed';
    return `<section class="analysis-page" aria-live="polite"><h1>${decisionFailed ? '判断未完成' : '正在生成本次判断'}</h1><p>${decisionFailed ? '服务端判断暂时不可用，可重新发起本次判断。' : '商品信息已整理，正在结合你这次的需求形成判断。'}</p>${decisionFailed ? '<button class="primary-btn" data-action="retry-decision">重新判断</button>' : ''}<button class="secondary-btn" data-action="back-from-result">返回候选页</button></section>`;
  }
  if (candidate.extraction) {
    const answerCandidate = (state.selectionAnswer?.candidates || []).find(item => item.candidate_id === candidate.serverCandidateId);
    const facts = (answerCandidate?.known_facts || []).map(item => `<li><b>${escapeHtml(item.label)}</b><span class="evidence-value">${escapeHtml(item.value)}</span><small>${escapeHtml(item.basis)}</small></li>`).join('') || '<li>截图中没有可确认的商品字段。</li>';
    const merchantFacts = (answerCandidate?.merchant_facts || []).map(item => `<li><b>${escapeHtml(item.label)}</b><span class="evidence-value">${escapeHtml(item.value)}</span><small>${escapeHtml(item.basis)}</small></li>`).join('');
    const risks = (answerCandidate?.risks || candidate.riskFlags || []).map((value) => `<li>${escapeHtml(typeof value === 'string' ? riskForDisplay(value) : value)}</li>`).join('') || '<li>未发现明确风险提示；未出现的信息仍需自行核对。</li>';
    const uncertainties = (answerCandidate?.decision_uncertainties || []).slice(0, 2).map(item => `<li><b>${escapeHtml(item.label)}</b>：${escapeHtml(item.why_it_matters)}<small>${escapeHtml(item.change_if || '补充后可能改变当前选择。')}</small></li>`).join('') || '';
    const decision = mvpDecision(candidate);
    const dots = state.candidates.map((_, index) => `<i class="${index === state.activeCandidate ? 'active' : ''}"></i>`).join('');
    // One comparison has one evidence-completion round.  Before that round the
    // only primary action is to ask; after the aggregate rejudge every card
    // offers the same deliberate choice to take that specific tea into the
    // tea store.  A per-card bucket must not accidentally create two flows.
    const comparisonSettled = isRejudged()
      || state.lastDecisionDelta?.new_decision_version_id === state.decisionVersionId
      || (state.questionStatus === 'not-needed' && state.questionDecisionVersionId === state.decisionVersionId);
    const sensory = answerCandidate?.sensory_interpretations || [];
    const preferenceReference = GuanchaAdapters.buildPreferenceReference({ o1: state.o1, o2: state.o2, onboardingStatus: onboardingStatus() });
    const personalFit = GuanchaAdapters.buildPersonalFitPresentation({ need: state.need, sensoryInterpretations: sensory, preferenceReference });
    const fitLines = personalFit.lines.map((line) => `<li>${escapeHtml(line)}</li>`).join('');
    const fitCaveat = GuanchaAdapters.sensoryNeedMatch(candidate.decision) <= 0
      ? '<p class="soft-note result-fit-caveat">这款目前没有足够证据表明更符合你这次的口味方向；它的当前顺序也可能来自信息完整度与试错条件，不代表一定更合口味。</p>'
      : '';
    const sensoryHtml = sensory.slice(0, 2).map((item) => `<li><b>${escapeHtml(item.label)}</b>：${escapeHtml(item.text)}<small>${escapeHtml(item.boundary)}</small></li>`).join('');
    const question = answerCandidate?.next_step;
    const nextStep = question ? `<li><b>最值得问</b>：${escapeHtml(question.text)}<small>这个回答可能改变当前选择。</small></li>` : '';
    return `<section class="page result-page"><button class="icon-btn back-btn" data-action="back-from-result" aria-label="返回">${icon('back')}</button>${wordmark('analysis-result.svg', 'wordmark--analysis-result', '分析结果')}${needCard({ className: 'result-need' })}<div class="result-stage" id="result-stage"><div class="carousel-meta"><div class="dots">${dots}</div><span>${state.activeCandidate + 1} / ${state.candidates.length}</span></div><article class="result-card card"><div class="priority-label">${escapeHtml(fitLabel(candidate, answerCandidate))}</div><div class="result-scroll"><header class="result-hero">${candidateImageGallery(candidate)}<div><div class="result-name">候选茶 ${candidate.letter} · ${escapeHtml(candidate.name)}</div><div class="result-type">${escapeHtml(candidate.type)}</div></div></header><section class="result-section"><h3>为什么它更像 / 不像你会喜欢</h3>${fitCaveat}<ul>${fitLines}</ul></section>${renderDecisionDelta()}${merchantFacts ? `<section class="result-section evidence-section"><h3>商家本次补充</h3><ul>${merchantFacts}</ul></section>` : ''}${sensoryHtml ? `<section class="result-section"><h3>这些专业信息可能意味着什么</h3><ul>${sensoryHtml}</ul></section>` : ''}${nextStep || uncertainties ? `<section class="result-section"><h3>现在最值得确认</h3><ul>${nextStep}${uncertainties}</ul></section>` : ''}<section class="result-section evidence-section"><h3>商品页目前能确认</h3><ul>${facts}</ul></section><section class="result-section"><h3>风险提示</h3><ul>${risks}</ul></section></div></article><p class="result-hint">左右滑动，或点击两侧露出的卡片，查看每款茶的差异。</p></div><div class="result-actions">${comparisonSettled ? '<button class="primary-btn" data-action="confirm-choice">选择这款，加入茶仓</button><button class="text-link result-secondary-action" data-action="back-from-result">暂不加入，返回候选</button>' : '<button class="primary-btn" data-action="ask">去问商家</button><p class="soft-note result-secondary-action">补齐商家回复后，会统一复判，再决定是否加入茶仓。</p>'}</div></section>`;
  }
  const heading = rejudged
    ? wordmark('updated-judgment.svg', 'wordmark--updated-judgment', '更新后的判断')
    : wordmark('analysis-result.svg', 'wordmark--analysis-result', '分析结果');
  const note = '';
  const dots = state.candidates.map((_, index) => `<i class="${index === state.activeCandidate ? 'active' : ''}"></i>`).join('');
  const additions = rejudged ? `<section class="result-section"><h3><span class="leaf-mark">${icon('leaf',22)}</span>商家回复补充了什么</h3><div class="change-chips">${data.additions.map(item => `<span class="change-chip">${item}</span>`).join('')}</div></section>` : '';
  return `<section class="page result-page"><button class="icon-btn back-btn" data-action="back-from-result" aria-label="返回">${icon('back')}</button>${heading}${note}${needCard({ className: 'result-need' })}<div class="result-stage" id="result-stage"><div class="carousel-meta"><div class="dots">${dots}</div><span>${state.activeCandidate + 1} / ${state.candidates.length}</span></div><article class="result-card card"><div class="priority-label">${data.label}</div><div class="result-scroll"><header class="result-hero"><span class="result-art ref-art ${candidate.letter === 'B' ? 'ref-art--gaiwan' : 'ref-art--can'}" role="img" aria-label="${escapeHtml(candidate.name)}的小插图"></span><div><div class="result-name">候选 ${candidate.letter} ・ ${escapeHtml(candidate.name)}</div><div class="result-type">${escapeHtml(candidate.type)}</div></div></header>${additions}${data.sections.map(([title, body]) => `<section class="result-section"><h3><span class="leaf-mark">${icon('leaf',22)}</span>${title}</h3>${body}</section>`).join('')}</div></article><p class="result-hint">左右滑动查看其他候选茶的${rejudged ? '更新判断' : '分析结果'}</p>${rejudged ? `<div class="result-options"><button data-action="slide-prev">♧ 可作为备选</button><button data-action="slide-next">♧ 暂不推荐</button></div>` : ''}</div><div class="result-actions"><button class="primary-btn" data-action="${rejudged ? 'confirm-choice' : 'ask'}">${rejudged ? '✧ 我决定选这款' : '去问商家'}</button></div></section>`;
}
function renderStub() {
  const map = { brew: ['泡茶', '泡茶插画.svg', '泡茶流程将在下一阶段补齐。'], trace: ['茶迹', '茶迹插画.svg', '记录你的喝茶感受与选茶轨迹。'], settings: ['设置', '观茶图形.svg', '在这里维护你的饮品偏好。'] };
  const [title, image, copy] = map[state.stubTab] || map.settings;
  return `<section class="stub-page"><img src="${asset(image)}" alt="" /><h1>${title}</h1><p>${copy}</p>${state.stubTab === 'settings' ? '<button class="primary-btn" data-action="open-preferences">编辑口味偏好</button>' : ''}${tabbar()}</section>`;
}

/* 茶仓库与泡茶日记：比赛版仅使用本地模拟数据，不依赖后端。 */
function getTea(id = state.selectedTeaId) { return state.warehouse.find(item => item.id === id) || state.warehouse[0]; }
function artElement(art = 'can', className = 'tea-art') {
  const file = ART[art] || ART.can;
  return `<span class="${className} tea-art-frame" aria-hidden="true"><img src="${asset(file)}" alt=""></span>`;
}
function teaArt(tea, className = 'tea-art') { return artElement(tea?.art === 'gaiwan' ? 'gaiwan' : 'can', className); }
function topBack(action = 'go-journal') { return `<button class="icon-btn back-btn" data-action="${action}" aria-label="返回">${icon('back')}</button>`; }
function titleWithSub(file, label, sub, className = '') { return `${desktopWordmark(file, `feature-title ${className}`, label)}${sub ? `<p class="feature-sub">${sub}</p>` : ''}`; }
function statusLabel(status) { return status === 'drinking' ? '正在喝' : status === 'paused' ? '暂时不喝' : '已喝完'; }
function statusTea(tea) { return `<span class="tea-status ${tea.status}">${statusLabel(tea.status)}</span>`; }

function renderOwnership() {
  const candidate = currentCandidate() || { name: '春日乌龙', type: '乌龙茶 · 清香型' };
  return `<section class="page ownership-page">${topBack('back-from-ownership')}
    <h1 class="fallback-title">把它带回观茶</h1>
    <article class="ownership-summary card">${teaArt({ art:'can' })}<div><h2>${escapeHtml(candidate.name)}</h2><p>${escapeHtml(candidate.type)}</p><div class="need-chips"><span class="need-chip">${escapeHtml(state.need.taste)}</span><span class="need-chip">${escapeHtml(state.need.purpose)}</span><span class="need-chip">预算 ${escapeHtml(state.need.budget)}</span></div></div><span class="leaf-float">${icon('leaf',34)}</span></article>
    <p class="ownership-hint">选择一种情况，确认后加入茶仓。</p>
    <div class="ownership-options">
      ${ownershipOption('bought','我已经买到','本次购入，加入茶仓后开始记录','can')}
      ${ownershipOption('owned','我本来就有','已有的茶，也可以加入茶仓开始记录','gaiwan')}
    </div>
    <div class="screen-bottom"><button class="primary-btn" data-action="confirm-warehouse">确认加入茶仓</button><button class="text-link" data-action="save-choice-only">暂不加入茶仓，仅保存选择结果</button></div>
  </section>`;
}
function ownershipOption(value, title, copy, art) { const selected = state.ownershipChoice === value; return `<button class="ownership-option ${selected ? 'selected' : ''}" data-action="set-ownership" data-value="${value}">${teaArt({art},'ownership-art')}<span><b>${title}</b><small>${copy}</small></span><i class="radio-dot ${selected ? 'selected' : ''}">${selected ? icon('check',16) : ''}</i></button>`; }

function renderWarehouse() {
  const drinking = state.warehouse.filter(item => item.status === 'drinking');
  const paused = state.warehouse.filter(item => item.status === 'paused');
  return `<section class="warehouse-screen"><section class="page warehouse-page">${greeting()}<div class="warehouse-intro">${titleWithSub('标题_茶仓库.svg','茶仓库','把正在喝的茶放在这里。')}<button class="manual-stock" data-action="open-warehouse-add">${icon('plus',16)} 手动入库</button></div>
    <section class="warehouse-section"><h2>正在喝</h2>${drinking.length ? drinking.map(warehouseCard).join('') : warehouseEmpty('茶仓里还没有正在喝的茶')}</section>
    ${paused.length ? `<section class="warehouse-section paused-section"><h2>暂时不喝</h2>${paused.map(warehouseCard).join('')}</section>` : ''}
    ${tabbar()}</section></section>`;
}
function warehouseCard(tea) { return `<article class="warehouse-card card"><button class="warehouse-main" data-action="open-tea" data-id="${tea.id}">${teaArt(tea,'warehouse-art')}<div class="warehouse-copy"><h3>${escapeHtml(tea.name)}</h3><p><i></i>${escapeHtml(tea.type)} · ${escapeHtml(tea.aroma)}</p><small>来源：${escapeHtml(tea.source)}</small><hr><div class="warehouse-meta"><span>${icon('clock',17)} 最近泡茶：${escapeHtml(tea.lastBrew)}</span><span>${icon('book',17)} 已记录：${tea.records} 次</span></div></div><span class="warehouse-arrow">${icon('right',22)}</span></button>${tea.status === 'drinking' ? `<button class="brew-this" data-action="brew-this" data-id="${tea.id}">今天泡这款 ${icon('leaf',15)}</button>` : `<button class="resume-tea" data-action="resume-tea" data-id="${tea.id}">恢复到正在喝</button>`}</article>`; }
function warehouseEmpty(copy) { return `<div class="simple-empty"><span>${icon('jar',42)}</span><p>${copy}</p><button class="secondary-btn" data-action="open-warehouse-add">手动入库</button></div>`; }

function renderWarehouseDetail() {
  const tea = getTea(); if (!tea) return renderWarehouse();
  return `<section class="page detail-page">${topBack('go-warehouse')}${titleWithSub('标题_茶仓库.svg','茶仓库','这款茶的记录与状态。','detail-title')}
    <article class="tea-detail-hero card">${teaArt(tea,'detail-art')}<div><h2>${escapeHtml(tea.name)}</h2><p>${escapeHtml(tea.type)} · ${escapeHtml(tea.aroma)}</p>${statusTea(tea)}<small>来源：${escapeHtml(tea.source)}</small></div></article>
    <section class="detail-list card"><h3>选茶信息</h3><p><b>已保留：</b>${tea.facts.map(escapeHtml).join('、')}</p><p><b>仍待确认：</b>${tea.risks.map(escapeHtml).join('、')}</p></section>
    <section class="detail-list card"><h3>使用记录</h3><p>最近泡茶：${escapeHtml(tea.lastBrew)}</p><p>已记录：${tea.records} 次</p><div class="status-switch"><button class="${tea.status === 'drinking' ? 'on' : ''}" data-action="set-tea-status" data-status="drinking">正在喝</button><button class="${tea.status === 'paused' ? 'on' : ''}" data-action="set-tea-status" data-status="paused">暂时不喝</button><button class="${tea.status === 'finished' ? 'on' : ''}" data-action="set-tea-status" data-status="finished">已喝完</button></div></section>
    <div class="screen-bottom"><button class="primary-btn" data-action="brew-this" data-id="${tea.id}">今天泡这款</button></div></section>`;
}

function renderWarehouseAdd() {
  return `<section class="page form-page">${topBack('go-warehouse')}${titleWithSub('标题_手动入库.svg','手动入库','先记住这款茶，信息之后还可以慢慢补。')}
    <form class="stock-form" data-action="save-stock"><label>茶名 <em>必填</em><input required name="name" placeholder="例如：白牡丹、凤凰单丛" /></label><label>茶类 <span>可选</span><select name="type"><option value="不确定">不确定</option><option>绿茶</option><option>白茶</option><option>黄茶</option><option>青茶（乌龙茶）</option><option>红茶</option><option>黑茶</option><option>再加工茶</option></select></label><label>你记得的香气或备注 <span>可选</span><input name="aroma" placeholder="例如：花香、清爽、朋友送的" /></label><p class="form-tip">没有品牌、年份或产地也没关系，观茶会先给你一个温和的冲泡起点。</p><button class="primary-btn" type="submit">加入茶仓库</button><button class="text-link" type="button" data-action="go-warehouse">取消</button></form></section>`;
}

function renderJournal() {
  const selected = journalDate(); const selectedRecords = recordsOn(selected);
  return `<section class="page journal-page">${greeting()}${titleWithSub('标题_泡茶日记.svg','泡茶日记','记录每一次泡茶。')}
    <section class="calendar-card card"><div class="calendar-head"><h2>2026 年 8 月</h2><div><button data-action="calendar-prev" aria-label="上个月">${icon('back',20)}</button><button data-action="calendar-next" aria-label="下个月">${icon('right',20)}</button></div></div><div class="week-row"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div><div class="calendar-grid">${calendarCells()}</div><button class="calendar-summary" data-action="open-day" data-date="${selected}"><span><b>${prettyDate(selected)}${selected === TODAY ? ' · 今天' : ''}</b><small>${selectedRecords.length ? `已有 ${selectedRecords.length} 次泡茶记录` : selected === TODAY ? '还没有泡茶记录' : '暂无泡茶记录'}</small></span><b>${selected === TODAY ? '查看今天' : '查看当天'}</b></button></section>${tabbar()}</section>`;
}
const TODAY = '2026-08-04';
function journalDate() { return state.journalDate || TODAY; }
function prettyDate(date) { const [,m,d] = date.split('-'); return `${Number(m)} 月 ${Number(d)} 日`; }
function recordsOn(date) { return state.journalRecords.filter(record => record.date === date); }
function calendarCells() { const records = new Set(state.journalRecords.map(item => item.date)); const firstBlank = 5; const cells = Array.from({length:firstBlank}, () => '<span class="calendar-blank"></span>'); for (let day=1; day<=31; day++) { const date=`2026-08-${String(day).padStart(2,'0')}`; const future = date > TODAY; const selected = date === journalDate(); const has = records.has(date); cells.push(`<button class="calendar-day ${selected ? 'selected' : ''} ${future ? 'future' : ''}" data-action="select-date" data-date="${date}" ${future ? 'aria-label="未来日期"' : ''}><b>${day}</b>${has ? '<i></i>' : ''}</button>`); } return cells.join(''); }

function renderJournalDay() {
  const date = journalDate(); const records = recordsOn(date); const today = date === TODAY;
  return `<section class="page day-page">${topBack('go-journal')}<p class="day-date">${prettyDate(date)}${today ? ' · 今天' : ''}</p>${titleWithSub('标题_今日泡茶.svg', today ? '今日泡茶' : '当天记录', today ? '看看今天与茶相遇的片刻。' : '')}
    <section class="day-records">${records.length ? records.map(dayRecordCard).join('') : `<div class="day-empty card"><span>${icon('pot',48)}</span><h2>这一天还没有泡茶记录</h2><p>${today ? '现在开始，记录今天的第一泡吧。' : '过去日期暂不支持补记。'}</p></div>`}</section>
    ${today ? `<div class="screen-bottom"><button class="primary-btn" data-action="start-brew">开始一次泡茶</button></div>` : ''}</section>`;
}
function dayRecordCard(record) { const tea=getTea(record.teaId); return `<button class="day-record card" data-action="open-record" data-id="${record.id}">${teaArt(tea,'day-art')}<span><h3>${escapeHtml(tea.name)}</h3><b>已完成 ${record.infusions.length} 泡</b><p>${record.createdAt} · ${record.plan.ware} · ${record.plan.grams} · ${record.plan.temp}</p><div>${record.feedback.tags.map(tag=>`<i>${escapeHtml(tag)}</i>`).join('')}</div></span>${icon('right',23)}</button>`; }

function renderChooseTea() {
  const available=state.warehouse.filter(item=>item.status==='drinking'); const paused=state.warehouse.filter(item=>item.status==='paused');
  return `<section class="page choose-tea-page">${topBack('go-day')}${titleWithSub('标题_准备泡茶.svg','选择今天要泡的茶','从正在喝的茶里选一款。')}
    <section class="choose-list"><h2>正在喝</h2>${available.length ? available.map(chooseTeaRow).join('') : warehouseEmpty('还没有可泡的茶')}</section>${paused.length ? `<section class="choose-list"><h2>暂时不喝</h2>${paused.map(chooseTeaRow).join('')}</section>` : ''}
    ${state.selectedTeaId ? `<div class="screen-bottom"><button class="primary-btn" data-action="go-prepare">查看冲泡准备</button></div>` : ''}</section>`;
}
function chooseTeaRow(tea) { const selected=state.selectedTeaId===tea.id; return `<button class="choose-tea-row ${selected ? 'selected' : ''}" data-action="select-tea" data-id="${tea.id}">${teaArt(tea,'choose-art')}<span><b>${escapeHtml(tea.name)}</b><small>${escapeHtml(tea.type)} · ${escapeHtml(tea.aroma)}</small><em>已记录 ${tea.records} 次</em></span><i class="radio-dot ${selected ? 'selected' : ''}">${selected ? icon('check',16) : ''}</i></button>`; }

function ensureBrew() { if (!state.brew) state.brew={ teaId:state.selectedTeaId || 'spring', infusion:1, remaining:10, running:false, completed:[], plan:{ware:'盖碗',water:'110 ml',grams:'5 g',temp:'95℃',seconds:10}, feedback:{taste:'',strength:'',source:'',tags:[],impression:'',repurchase:'',aroma:[],advanced:{},score:0} }; return state.brew; }
function renderPrepare() { const brew=ensureBrew(); const tea=getTea(brew.teaId); return `<section class="page prepare-page">${topBack('go-choose-tea')}${titleWithSub('标题_准备泡茶.svg','准备泡茶','先给你一个温和、可调整的起点。')}
  <article class="prepare-hero card">${teaArt(tea,'prepare-art')}<div><h2>${escapeHtml(tea.name)}</h2><p>${escapeHtml(tea.type)} · ${escapeHtml(tea.aroma)}</p><small>第一次尝试</small></div></article>
  <section class="plan-card card"><h2>建议冲泡方案</h2><div class="plan-grid"><span>${icon('pot',24)}<b>${brew.plan.ware}</b></span><span>${icon('jar',24)}<b>${brew.plan.water}</b></span><span>${icon('leaf',24)}<b>${brew.plan.grams}</b></span><span>℃<b>${brew.plan.temp}</b></span></div><div class="time-plan"><span>第 1 泡建议出汤时间</span><b>${brew.plan.seconds} 秒</b></div><p class="plan-basis">依据：${escapeHtml(tea.type)}、${escapeHtml(tea.aroma)}${tea.facts.includes('轻火焙制') ? '、轻火信息' : ''}</p></section>
  <div class="screen-bottom"><button class="primary-btn" data-action="start-timer">开始第 1 泡计时</button><button class="text-link" data-action="open-plan-editor">调整茶具与参数</button></div></section>`; }

function renderTimer() { const brew=ensureBrew(); const tea=getTea(brew.teaId); const time=`00:${String(Math.max(0,brew.remaining)).padStart(2,'0')}`; const done=brew.remaining<=0; const total=Math.max(1, brew.plan.seconds); const progress=Math.max(0,Math.min(1,1-(brew.remaining/total))); return `<section class="page timer-page">${topBack('exit-brew')}<p class="timer-kicker">${escapeHtml(tea.name)} · 第 ${brew.infusion} 泡</p><div class="timer-ring ${done ? 'done' : ''}" style="--timer-progress:${progress}"><span class="timer-progress-orbit" aria-hidden="true"><i></i></span><b>${time}</b><span>${done ? '可以出汤' : brew.running ? '正在计时' : '准备开始'}</span></div><p class="timer-note">建议 ${brew.plan.seconds} 秒 · 实际计时可随时暂停或提前完成</p><div class="timer-controls"><button class="timer-main" data-action="timer-toggle">${brew.running ? icon('pause',28) : icon('play',28)}<span>${brew.running ? '暂停' : '继续计时'}</span></button><button class="timer-early" data-action="finish-infusion">提前完成</button></div>${done ? `<button class="primary-btn timer-finish" data-action="finish-infusion">完成第 ${brew.infusion} 泡</button>` : ''}</section>`; }

function renderInfusionDone() { const brew=ensureBrew(); const tea=getTea(brew.teaId); return `<section class="page infusion-page">${topBack('exit-brew')}<span class="finish-check">${icon('check',34)}</span><h1>第 ${brew.infusion} 泡已完成</h1><p>${escapeHtml(tea.name)} · 实际 ${brew.completed.at(-1)?.actual || brew.plan.seconds} 秒</p><article class="infusion-summary card">${teaArt(tea,'infusion-art')}<div><h2>已完成 ${brew.completed.length} 泡</h2><p>${brew.completed.map(item=>`第 ${item.number} 泡 ${item.actual} 秒`).join(' · ')}</p></div></article><div class="screen-bottom"><button class="primary-btn" data-action="next-infusion">继续第 ${brew.infusion + 1} 泡</button><button class="text-link" data-action="go-feedback">结束本次泡茶并记录感受</button></div></section>`; }

function choiceButtons(field, values, selected, cls='feedback-options') {
  return `<div class="${cls}">${values.map(value => {
    const isSelected = Array.isArray(selected) ? selected.includes(value) : selected === value;
    return `<button class="${isSelected ? 'selected' : ''}" data-action="set-feedback" data-field="${field}" data-value="${value}">${value}</button>`;
  }).join('')}</div>`;
}
function renderFeedback() { const brew=ensureBrew(); const f=brew.feedback; const showSource=['一般','不喜欢'].includes(f.taste)||['偏淡','偏浓'].includes(f.strength); return `<section class="page feedback-page">${topBack('exit-brew')}${titleWithSub('标题_泡茶记录.svg','泡茶记录','用半分钟，记下这一杯的感受。')}
  <section class="feedback-card card"><h2>这杯合你的口味吗？<em>必选</em></h2>${choiceButtons('taste',['喜欢','一般','不喜欢','还不确定'],f.taste)}<h2>本次浓淡如何？<em>必选</em></h2>${choiceButtons('strength',['偏淡','刚好','偏浓','不确定'],f.strength)}${showSource ? `<h2>你觉得问题主要来自？</h2>${choiceButtons('source',['茶本身不太适合','本次泡法可能不合适','两方面都有','还判断不出来'],f.source)}` : ''}<h2>这一泡的感觉 <span>最多 2 个</span></h2>${choiceButtons('tags',['清爽','顺口','醇厚','没感觉','不确定'],f.tags,'feedback-options multi')}<label class="impression-label">今天对这杯茶的印象 <span>可选</span><textarea data-action="feedback-impression" maxlength="80" placeholder="写一句想记住的话…">${escapeHtml(f.impression)}</textarea></label><h2>回购意愿 <span>可选</span></h2>${choiceButtons('repurchase',['想回购','暂不确定','不会回购'],f.repurchase)}</section>
  <div class="screen-bottom"><button class="primary-btn" data-action="go-advanced">继续（可选进阶记录）</button><button class="text-link" data-action="save-record">跳过进阶记录，保存</button></div></section>`; }

function renderAdvanced() { const brew=ensureBrew(); const f=brew.feedback; const dimensions={汤感:['偏薄','顺滑','有厚度','不确定'],回甘:['未感到','有一点','明显','不确定'],生津:['未感到','有一点','明显','不确定'],体感:['无明显感受','放松温暖','有刺激感','不确定'],余韵:['很短','有一点','较久','不确定']}; return `<section class="page advanced-page">${topBack('go-feedback')}${titleWithSub('标题_泡茶记录.svg','多记一点','这些都是可选的，只为帮助你自己回看。')}
  <section class="feedback-card card"><div class="advanced-head"><h2>香气 <span>最多 3 个</span></h2><b>已选 ${f.aroma.length}/3</b></div>${choiceButtons('aroma',FLAVORS.concat(['没有明显香气','不确定']),f.aroma,'aroma-grid multi')}${Object.entries(dimensions).map(([key,values])=>`<section class="dimension"><h3>${key}<small>${advancedExplain(key)}</small></h3>${choiceButtons(`advanced-${key}`,values,f.advanced[key])}</section>`).join('')}<h2>给这次泡茶打个分 <span>可选</span></h2><div class="score-row">${[1,2,3,4,5].map(value=>`<button class="${f.score===value ? 'selected' : ''}" data-action="set-feedback" data-field="score" data-value="${value}">${value}</button>`).join('')}</div></section>
  <div class="screen-bottom"><button class="primary-btn" data-action="save-record">保存泡茶记录</button></div></section>`; }
function advancedExplain(key) { return ({汤感:'入口偏薄、顺滑，还是有厚度？',回甘:'咽下后是否慢慢泛出甜感？',生津:'喝后口腔是否变得湿润？',体感:'主观上是否放松、温暖或有刺激感？',余韵:'吞咽后香气或滋味停留多久？'})[key]; }

function renderBrewResult() {
  const record = state.journalRecords.find(item => item.id === state.activeRecordId) || state.journalRecords.at(-1);
  const f = record?.feedback || { tags: [] };
  const text = record?.suggestion || '按当前参数再试一次，暂不精确调整';
  const analysis = record?.feedbackAnalysis;
  const analysisHtml = analysis ? `<hr><h2>这次体验分析</h2><p>${analysis.attribution === 'tea' ? '更可能来自茶本身' : analysis.attribution === 'brewing' ? '更可能来自泡法' : '暂时无法区分'}</p><p>${(analysis.attribution_reasons || []).join(' ')}</p><p>${analysis.next_brew_adjustment?.reason || ''}</p><small>这是一次记录形成的低置信度参考，不会自动修改口味卡；你可以忽略或删除记录。</small>` : record?.feedbackAnalysisStatus === 'failed' ? `<p class="soft-note">分析失败，可重新分析这次体验。</p><button class="text-link" data-action="analyze-brew-feedback" data-id="${escapeHtml(record?.id || '')}">重新分析这次体验</button>` : '<p class="soft-note">正在分析这次体验…</p>';
  return `<section class="page brew-result-page">${topBack('go-day')}<span class="finish-check">${icon('check',34)}</span><h1 class="fallback-title small">记录好了</h1><p class="result-lead">这是一份给下次自己的小提醒。</p><section class="brew-result-card card"><h2>下次冲泡建议</h2><p class="suggestion">${text}</p><small>这是下一次试泡起点，不是专业结论。</small>${analysisHtml}<hr><h2>你的这次记录</h2><p>合口味程度：${f.taste || '还不确定'} · 浓淡：${f.strength || '不确定'}</p><p>感受：${f.tags?.length ? f.tags.join('、') : '未填写'}</p></section><div class="screen-bottom"><button class="primary-btn" data-action="go-day">回到今天的记录</button><button class="text-link" data-action="open-latest-record">查看完整记录</button></div></section>`;
}

function renderRecordDetail() { const record=state.journalRecords.find(item=>item.id===state.activeRecordId)||state.journalRecords.at(-1); if(!record) return renderJournalDay(); const tea=getTea(record.teaId); const f=record.feedback; return `<section class="page record-detail-page">${topBack('go-day')}${titleWithSub('标题_泡茶记录.svg','泡茶记录','${prettyDate(record.date)} · ${record.createdAt}')}
  <article class="tea-detail-hero card">${teaArt(tea,'detail-art')}<div><h2>${escapeHtml(tea.name)}</h2><p>${escapeHtml(tea.type)} · ${escapeHtml(tea.aroma)}</p><small>来源：${escapeHtml(tea.source)}</small></div></article>
  ${detailSection('整次冲泡信息',`${record.plan.ware} · ${record.plan.water} · 投茶 ${record.plan.grams} · ${record.plan.temp}`)}${detailSection('泡次记录',record.infusions.map(item=>`第 ${item.number} 泡：建议 ${item.suggested} 秒，实际 ${item.actual} 秒`).join('<br>'))}${detailSection('基础反馈',`合口味程度：${f.taste||'未填写'}<br>本次浓淡：${f.strength||'未填写'}<br>感受：${f.tags?.length?f.tags.join('、'):'未填写'}`)}${detailSection('进阶记录',`香气：${f.aroma?.length?f.aroma.join('、'):'未填写'}<br>${Object.entries(f.advanced||{}).map(([k,v])=>`${k}：${v}`).join('<br>')||'未填写'}`)}${detailSection('个人记录',`印象：${f.impression||'未填写'}<br>评分：${f.score ? `${f.score}/5` : '未评分'} · 回购：${f.repurchase||'未填写'}`)}${detailSection('反馈影响',record.suggestion||'暂时保持本次参数')}<button class="danger-link" data-action="delete-record">删除这次泡茶记录</button></section>`; }
function detailSection(title, body) { return `<section class="detail-list card"><h3>${title}</h3><p>${body}</p></section>`; }

function renderSettings() { return `<section class="page settings-page">${greeting()}${titleWithSub('标题_设置.svg','设置','把你的偏好和本地演示数据放在这里。')}<section class="settings-list card"><button data-action="open-preferences"><span>${icon('leaf',23)} 初始口味偏好</span>${icon('right',20)}</button><button data-action="show-evidence"><span>${icon('book',23)} 近期饮用证据</span>${icon('right',20)}</button><button data-action="reset-demo"><span>${icon('jar',23)} 清除本地演示数据</span>${icon('right',20)}</button></section>${tabbar()}</section>`; }

function renderOverlay() {
  if (state.overlay === 'source') return `<div class="overlay" data-action="close-overlay"><section class="sheet"><div class="sheet-handle"></div>${wordmark('add-candidate.svg', 'wordmark--source-add', '添加候选')}<button class="source-choice" data-action="choose-camera"><span>${icon('camera')}</span><b>拍照</b><span>›</span></button><button class="source-choice" data-action="choose-album"><span>${icon('photo')}</span><b>从相册上传商品截图</b><span>›</span></button><button class="sheet-close" style="display:block;margin:28px auto 0" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></section></div>`;
  if (state.overlay === 'camera') return `<div class="overlay"><section class="sheet camera-sheet"><div class="sheet-handle"></div><div class="sheet-title-row"><h2 class="sheet-title">拍照识别</h2><button class="sheet-close" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></div><div class="camera-preview"><video id="camera-preview" autoplay playsinline muted aria-label="电脑摄像头预览"></video><div class="camera-fallback" id="camera-fallback" hidden>暂时无法访问电脑摄像头。<br><button class="secondary-btn" data-action="choose-album">从相册选择图片</button></div></div><div class="camera-actions"><button class="camera-cancel" data-action="close-overlay" aria-label="取消">${icon('close',22)}</button><button class="camera-capture" data-action="capture-camera" aria-label="拍摄"></button><span aria-hidden="true"></span></div></section></div>`;
  if (state.overlay === 'need-editor') return `<div class="modal"><form class="modal-box" data-action="save-needs"><h2>编辑本次需求</h2><div class="form-row"><label for="need-taste">口味或偏好</label><input id="need-taste" name="taste" value="${escapeHtml(state.need.taste)}" /></div><div class="form-row"><label for="need-purpose">用途</label><input id="need-purpose" name="purpose" value="${escapeHtml(state.need.purpose)}" /></div><div class="form-row"><label for="need-budget">预算</label><input id="need-budget" name="budget" value="${escapeHtml(state.need.budget)}" /></div><div class="modal-actions"><button class="secondary-btn" type="button" data-action="close-overlay">取消</button><button class="primary-btn" style="width:auto;height:44px;padding:0 18px;font-size:16px" type="submit">保存</button></div></form></div>`;
  if (state.overlay === 'plan-editor') { const p=ensureBrew().plan; return `<div class="modal"><form class="modal-box" data-action="save-plan"><h2>调整冲泡参数</h2><div class="form-row"><label>茶具<input name="ware" value="${escapeHtml(p.ware)}" /></label></div><div class="form-row"><label>注水量<input name="water" value="${escapeHtml(p.water)}" /></label></div><div class="form-row"><label>投茶量<input name="grams" value="${escapeHtml(p.grams)}" /></label></div><div class="form-row"><label>水温<input name="temp" value="${escapeHtml(p.temp)}" /></label></div><div class="form-row"><label>第 1 泡秒数<input type="number" min="3" max="60" name="seconds" value="${p.seconds}" /></label></div><div class="modal-actions"><button class="secondary-btn" type="button" data-action="close-overlay">取消</button><button class="primary-btn" style="width:auto;height:44px;padding:0 18px;font-size:16px" type="submit">保存参数</button></div></form></div>`; }
  if (state.overlay === 'ask') {
    const candidate = currentCandidate(); const questions = merchantQuestions(candidate);
    const unresolvedReply = questions.find(item => replyNeedsClarification(item.reply));
    const replyNotice = unresolvedReply ? `<p class="soft-note">${escapeHtml(replyGuidance(unresolvedReply))}</p>` : '';
    const body = state.questionStatus === 'loading' ? '<p class="soft-note">正在生成可回答的问题…</p>' : state.questionStatus === 'stale' ? '<p class="soft-note">当前判断已失效，请重新分析后再生成问题。</p>' : state.questionStatus === 'failed' ? '<p class="soft-note">问题生成失败，请重试。</p><button class="primary-btn" data-action="ask">重试生成</button>' : questions.length ? `${replyNotice}${questions.map((item,index) => `<article class="question-card"><div class="question-text"><span class="question-no">${index+1}</span>${escapeHtml(item.question)}</div><p class="question-reason">为什么值得问？${escapeHtml(item.reason || '这个回答可能改变当前选择，避免只凭商品页判断。')}</p><button class="copy-btn" data-action="copy-question" data-index="${index}">${icon('copy',16)} 复制</button></article>`).join('')}<button class="copy-all" data-action="copy-all">${icon('copy',20)} 复制全部问题</button><p class="privacy">提交商家回复后，系统会在服务端完成复判并返回变化说明。</p>` : '<p class="soft-note">目前没有值得继续追问的信息。</p>';
    return `<div class="overlay"><section class="sheet ask-sheet"><div class="sheet-handle"></div><div class="sheet-title-row">${wordmark('ask-merchant.svg', 'wordmark--ask-merchant', '问商家')}<button class="sheet-close" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></div><p class="ask-target">候选 ${candidate.letter} ・ ${escapeHtml(candidate.name)} <span class="leaf-mark">♧</span></p><p class="ask-tip"><i></i>这不是补字段：它可能改变当前哪款更值得优先考虑。</p>${body}</section></div>`;
  }
  return '';
}
function merchantQuestions(candidate) {
  const fieldLabels = { roast_level: '焙火程度', aroma_style: '香型', season: '采摘季节', price: '到手价格', sample_available: '是否可试饮' };
  return (state.followupQuestions || []).filter(item => item.candidate_id === candidate.serverCandidateId).map(item => ({
    id: item.id,
    // Older immutable question records used a double-question form. Present
    // the same question naturally without mutating its auditable record.
    question: String(item.question_text || '').replace('请问这款茶的是否提供小样或试饮装是什么？', '请问这款茶是否提供小样或试饮装？'),
    reason: item.reason,
    fieldKey: item.field_key,
    fieldLabel: fieldLabels[item.field_key] || '这项商品信息',
    reply: state.merchantReplies?.[item.id] || null,
  }));
}

async function prepareImageFiles(files, remaining) {
  return GuanchaImagePreparation.prepareFiles(files, {
    remaining,
    allowedImageMimeTypes: publicLimits().allowedImageMimeTypes,
    maxImageBytes: publicLimits().maxImageBytes,
  });
}
function createSquarePreview(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve(null);
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => resolve(null);
      image.onload = () => {
        try {
          const side = Math.min(image.naturalWidth, image.naturalHeight);
          const sourceX = Math.max(0, Math.round((image.naturalWidth - side) / 2));
          const desiredY = Math.round(image.naturalHeight * 0.30 - side / 2);
          const sourceY = Math.max(0, Math.min(image.naturalHeight - side, desiredY));
          const canvas = document.createElement('canvas');
          canvas.width = 160;
          canvas.height = 160;
          canvas.getContext('2d').drawImage(image, sourceX, sourceY, side, side, 0, 0, 160, 160);
          resolve(canvas.toDataURL('image/jpeg', 0.82));
        } catch {
          resolve(null);
        }
      };
      image.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  });
}
async function stageImages(files) {
  return Promise.all(files.map(async (file) => {
    const id = `local-image-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    runtimeImages.set(id, { file, url: URL.createObjectURL(file) });
    await pendingImageStore.save(id, file);
    return { id, status: 'queued', localOnly: true, previewUrl: await createSquarePreview(file) };
  }));
}
async function addCandidate(files) {
  if (state.candidates.length >= candidateLimit()) return { ok: false, code: 'candidate_limit_exceeded', message: `最多添加 ${candidateLimit()} 款候选茶` };
  const prepared = await prepareImageFiles(files, imageLimit());
  if (!prepared.ok) return prepared;
  const index = state.candidates.length;
  const defaults = {
    name: `候选茶 ${String.fromCharCode(65 + index)}`,
    type: '商品信息整理中',
    fields: '商品信息',
    art: ART.can,
  };
  try {
    const candidate = { id: `local-candidate-${Date.now()}-${index}`, letter: String.fromCharCode(65 + index), images: await stageImages(prepared.files), extractionStatus: 'queued', ...defaults };
    state.candidates.push(candidate);
    state.activeSelectionFlow = true;
    state.activeCandidate = state.candidates.length - 1;
    state.activeCandidateId = candidateIdentity(candidate);
    state.decisionVersionId = null;
    saveState();
    return { ok: true, candidate, converted: prepared.converted };
  } catch {
    return { ok: false, code: 'local_image_stage_failed', message: '图片暂存失败，请重试' };
  }
}
async function appendCandidateImage(candidateId, files) {
  const candidate = state.candidates.find((item) => item.id === candidateId);
  if (!candidate) return showToast('候选茶不存在，请刷新后重试');
  const prepared = await prepareImageFiles(files, imageLimit() - candidate.images.length);
  if (!prepared.ok) return showToast(prepared.message);
  candidate.images.push(...await stageImages(prepared.files));
  state.activeSelectionFlow = true;
  candidate.extractionStatus = 'queued';
  state.decisionVersionId = null;
  saveState(); render();
  showToast('第 2 张图片已暂存，等待服务端任务');
}
function hasUsableCandidateImages(candidate) {
  const images = Array.isArray(candidate.images) ? candidate.images : [];
  if (images.length < 1 || images.length > imageLimit()) return false;
  // A candidate only needs one usable product screenshot.  A second image is
  // optional support material: after a browser restart its locally staged copy
  // may no longer be available, but it must not invalidate an already accepted
  // server image or prevent the user from analysing the candidate.
  return images.some((image) => {
    const runtime = runtimeImages.get(image.id);
    if (runtime?.file) return publicLimits().allowedImageMimeTypes.includes(runtime.file.type) && runtime.file.size <= publicLimits().maxImageBytes;
    // A screenshot already accepted by the server is usable while its job is
    // queued/processing too.  Requiring a completed extraction here wrongly
    // disabled the main analysis button after a refresh.
    return Boolean(image.serverImageId && !image.localOnly && ['queued', 'processing', 'completed'].includes(image.status));
  });
}
function validateAnalysisCandidates() {
  if (!state.candidates.length) { showToast('请先添加候选茶'); return false; }
  if (!state.candidates.every(hasUsableCandidateImages)) { showToast(`每个候选需保留 1–${imageLimit()} 张有效图片后才能分析`); return false; }
  return true;
}
function stopCamera() {
  if (!activeCameraStream) return;
  activeCameraStream.getTracks().forEach(track => track.stop());
  activeCameraStream = null;
}
async function startCamera() {
  const video = document.querySelector('#camera-preview');
  if (!video) return;
  stopCamera();
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera unavailable');
    activeCameraStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' } },
    });
    if (state.overlay !== 'camera') return stopCamera();
    video.srcObject = activeCameraStream;
    await video.play();
  } catch {
    stopCamera();
    const fallback = document.querySelector('#camera-fallback');
    if (fallback) fallback.hidden = false;
  }
}
function captureCamera() {
  const video = document.querySelector('#camera-preview');
  if (!video || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth) {
    return showToast('相机正在准备中，请稍候');
  }
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  canvas.toBlob(async blob => {
    if (!blob) return showToast('拍摄失败，请重试');
    stopCamera();
    state.overlay = null;
    const added = await addCandidate([new File([blob], 'camera.jpg', { type: 'image/jpeg' })]);
    if (!added.ok) return showToast(added.message);
    setScreen('candidates');
    showToast('候选图片已加入比较，等待整理商品信息');
  }, 'image/jpeg', .9);
}
function stopBrewTimer() { if (brewTimerId) { clearInterval(brewTimerId); brewTimerId = null; } }
function syncBrewTimer() {
  stopBrewTimer();
  if (state.screen !== 'timer' || !state.brew?.running) return;
  brewTimerId = setInterval(() => {
    if (state.screen !== 'timer' || !state.brew?.running) return stopBrewTimer();
    state.brew.remaining = Math.max(0, state.brew.remaining - 1);
    if (state.brew.remaining === 0) state.brew.running = false;
    saveState(); render();
  }, 1000);
}
function setScreen(screen) { stopCamera(); state.screen = screen; state.overlay = null; render(); saveState(); }

document.addEventListener('click', event => {
  const target = event.target.closest('[data-action]'); if (!target) return;
  const action = target.dataset.action;
  if (action === 'go-home') { if (state.activeSelectionFlow) productAnalytics.track('flow_abandoned', { stage: state.screen, metadata: { screen: state.screen } }); return setScreen('home'); }
  if (action === 'start-task') return routeAfterHomeStart();
  if (action === 'open-preferences') { state.preferenceFlow = 'edit'; return setScreen('o1'); }
  if (action === 'go-o1') return setScreen('o1');
  if (action === 'go-o2') return setScreen('o2');
  if (action === 'skip-preferences') {
    const isFirstOnboarding = state.preferenceFlow === 'onboarding';
    productAnalytics.track('onboarding_skipped', { metadata: { source: state.preferenceFlow || 'settings', screen: 'o1' } });
    GuanchaOnboarding.markStatus(localStorage, 'skipped');
    if (isFirstOnboarding) {
      state.o1 = { tea: [], coffee: [], milk: [], juice: [] };
      state.o2 = { ...state.o2, flavors: [] };
    }
    const next = isFirstOnboarding ? 'candidates' : 'home';
    state.preferenceFlow = null;
    showToast('已跳过口味设置，本次需求仍优先');
    return setScreen(next);
  }
  if (action === 'finish-preferences') {
    productAnalytics.track('onboarding_completed', { metadata: { source: state.preferenceFlow || 'settings', screen: 'o2' } });
    GuanchaOnboarding.markStatus(localStorage, 'completed');
    const next = state.preferenceFlow === 'onboarding' ? 'candidates' : 'home';
    state.preferenceFlow = null;
    showToast(hasAnyO1() ? '已记作口味参考，选茶时会优先以你这次的需求为准。' : '已保存设置，本次需求仍优先');
    return setScreen(next);
  }
  if (action === 'tab') { const tab = target.dataset.tab; const screens = { select:'home', journal:'journal', warehouse:'warehouse', settings:'settings' }; return setScreen(screens[tab] || 'home'); }
  if (action === 'toggle-drink') { state.openDrink = state.openDrink === target.dataset.key ? '' : target.dataset.key; saveState(); render(); return; }
  if (action === 'toggle-drink-option') { const { key, value } = target.dataset; const items = state.o1[key]; state.o1[key] = items.includes(value) ? items.filter(item => item !== value) : [...items, value]; saveState(); render(); return; }
  if (action === 'toggle-flavor') { const value = target.dataset.value; const items = state.o2.flavors; state.o2.flavors = items.includes(value) ? items.filter(item => item !== value) : items.length >= 5 ? items : [...items, value]; saveState(); render(); return; }
  if (action === 'open-need-edit') { productAnalytics.track('need_started', { metadata: { has_budget: Boolean(state.need?.budget), has_sensory_need: Boolean(state.need?.taste), screen: state.screen } }); state.overlay='need-editor'; return render(); }
  if (action === 'close-overlay') {
    if (target.classList.contains('overlay') && event.target !== target) return;
    stopCamera(); state.overlay=null; return render();
  }
  if (action === 'open-source') { state.overlay='source'; return render(); }
  if (action === 'add-candidate-image') { pendingImageCandidateId=target.dataset.candidateId; candidateImageInput.click(); return; }
  if (action === 'choose-camera') { state.overlay='camera'; return render(); }
  if (action === 'capture-camera') return captureCamera();
  if (action === 'choose-album') { stopCamera(); state.overlay=null; render(); albumInput.click(); return; }
  if (action === 'remove-candidate') { const activeId=candidateIdentity(currentCandidate()); const [removed]=state.candidates.splice(Number(target.dataset.index),1); if (removed?.serverCandidateId && apiClient.isConfigured) apiClient.deleteCandidate(removed.serverCandidateId).catch(() => {}); (removed?.images || []).forEach(image=>{ const runtime=runtimeImages.get(image.id); if(runtime) URL.revokeObjectURL(runtime.url); runtimeImages.delete(image.id); pendingImageStore.remove(image.id); }); renumberCandidates(); syncActiveCandidate(activeId); state.decisionVersionId=null; saveState(); return render(); }
  if (action === 'start-analysis') return startMvpAnalysis();
  if (action === 'retry-analysis') return retryMvpAnalysis();
  if (action === 'retry-decision') { state.decisionJobId = null; state.decisionStatus = 'not_requested'; saveState(); return maybeStartSessionDecision(); }
  if (action === 'ask') return openFollowupQuestions();
  if (action === 'update-merchant-judgement') return updateMerchantJudgement();
  if (action === 'copy-question') { const item = merchantQuestions(currentCandidate())[Number(target.dataset.index)]; if (item) productAnalytics.track('merchant_question_copied', { candidate_id: currentCandidate()?.serverCandidateId, decision_version_id: state.decisionVersionId || undefined, metadata: { question_field: item.field_key || 'unknown', question_count: 1, screen: state.screen } }); return item?.question && copyText(item.question); }
  if (action === 'copy-all') { const questions = merchantQuestions(currentCandidate()); productAnalytics.track('merchant_question_copied', { candidate_id: currentCandidate()?.serverCandidateId, decision_version_id: state.decisionVersionId || undefined, metadata: { question_count: questions.length, source: 'copy_all', screen: state.screen } }); return copyText(questions.map((item,index)=>`${index+1}. ${item.question}`).join('\n')); }
  if (action === 'slide-prev') { slide(-1); return trackResultView(); }
  if (action === 'slide-next') { slide(1); return trackResultView(); }
  if (action === 'back-from-result') return setScreen('candidates');
  if (action === 'confirm-choice') { productAnalytics.track('candidate_selected', { candidate_id: currentCandidate()?.serverCandidateId, decision_version_id: state.decisionVersionId || undefined, metadata: { action_bucket: currentCandidate()?.decision?.action_bucket || 'unknown', screen: state.screen } }); return setScreen('ownership'); }
  if (action === 'back-from-ownership') return setScreen('candidates');
  if (action === 'set-ownership') { state.ownershipChoice = target.dataset.value; saveState(); return render(); }
  if (action === 'confirm-warehouse') {
    const candidate = currentCandidate() || { name:'春日乌龙', type:'乌龙茶 · 清香型' };
    const existing = state.warehouse.find(item => item.name === candidate.name);
    if (!existing) {
      const extracted = evidenceByField(candidate.extraction);
      state.warehouse.unshift({
        id:`tea-${Date.now()}`, name:candidate.name, product_name:candidate.name,
        type:extracted.tea_category || candidate.type.split('·')[0].trim(), tea_category:extracted.tea_category || null,
        tea_subtype:extracted.tea_subtype || null, origin:extracted.origin || null,
        roast_or_style:extracted.roast_or_style || null, aroma:extracted.aroma_claims || candidate.type.split('·')[1]?.trim() || '不确定',
        risk_flags:candidate.riskFlags || [], extraction_version_id:candidate.extractionVersionId || null,
        candidate_id:candidate.serverCandidateId || null, sourceDecisionId:state.decisionVersionId || null,
        joined_at:new Date().toISOString(), status:'drinking', source:state.ownershipChoice === 'bought' ? '本次购入' : '已有茶叶', lastBrew:'还未泡过', records:0, art:'can', facts:['来自本次截图提取'], risks:candidate.riskFlags?.length ? candidate.riskFlags : ['产地与年份待补']
      });
    }
    state.selectedTeaId = (existing || state.warehouse[0]).id;
    productAnalytics.track('tea_stock_added', { candidate_id: candidate.serverCandidateId || undefined, decision_version_id: state.decisionVersionId || undefined, metadata: { source: 'selection', screen: 'ownership' } });
    addSelectionHistory(candidate);
    completeSelectionFlow();
    showToast('已加入茶仓库'); return setScreen('warehouse');
  }
  if (action === 'save-choice-only') { addSelectionHistory(currentCandidate()); completeSelectionFlow(); showToast('已保存选茶结果'); return setScreen('home'); }
  if (action === 'go-warehouse') return setScreen('warehouse');
  if (action === 'open-warehouse-add') return setScreen('warehouse-add');
  if (action === 'open-tea') { state.selectedTeaId=target.dataset.id; return setScreen('warehouse-detail'); }
  if (action === 'brew-this') { state.selectedTeaId=target.dataset.id; state.brew=null; return setScreen('prepare'); }
  if (action === 'resume-tea') { const tea=getTea(target.dataset.id); if (tea) tea.status='drinking'; saveState(); return render(); }
  if (action === 'set-tea-status') { const tea=getTea(); if (tea) tea.status=target.dataset.status; saveState(); return render(); }
  if (action === 'go-journal') return setScreen('journal');
  if (action === 'open-day') { state.journalDate=target.dataset.date || journalDate(); return setScreen('journal-day'); }
  if (action === 'select-date') { const date=target.dataset.date; if (date > TODAY) return showToast('还未到这一天'); state.journalDate=date; return render(); }
  if (action === 'calendar-prev' || action === 'calendar-next') return showToast('比赛版固定展示 2026 年 8 月');
  if (action === 'start-brew') { state.selectedTeaId=null; state.brew=null; return setScreen('choose-tea'); }
  if (action === 'go-day') return setScreen('journal-day');
  if (action === 'go-choose-tea') return setScreen('choose-tea');
  if (action === 'select-tea') { state.selectedTeaId=target.dataset.id; state.brew=null; saveState(); return render(); }
  if (action === 'go-prepare') { if (!state.selectedTeaId) return showToast('请先选择一款茶'); return setScreen('prepare'); }
  if (action === 'open-plan-editor') { state.overlay='plan-editor'; return render(); }
  if (action === 'start-timer') { const brew=ensureBrew(); brew.running=true; brew.remaining=brew.plan.seconds; return setScreen('timer'); }
  if (action === 'timer-toggle') { const brew=ensureBrew(); brew.running=!brew.running; saveState(); return render(); }
  if (action === 'finish-infusion') { const brew=ensureBrew(); const actual=Math.max(1, brew.plan.seconds-brew.remaining); if (!brew.completed.some(item=>item.number===brew.infusion)) brew.completed.push({number:brew.infusion,suggested:brew.plan.seconds,actual}); brew.running=false; saveState(); return setScreen('infusion-done'); }
  if (action === 'next-infusion') { const brew=ensureBrew(); brew.infusion+=1; brew.plan.seconds=Math.min(30,brew.plan.seconds+2); brew.remaining=brew.plan.seconds; brew.running=true; return setScreen('timer'); }
  if (action === 'go-feedback') return setScreen('feedback');
  if (action === 'exit-brew') { if (state.brew && !window.confirm('要结束这次泡茶吗？未保存的记录会丢失。')) return; state.brew=null; return setScreen('journal-day'); }
  if (action === 'set-feedback') { setFeedback(target.dataset.field,target.dataset.value); saveState(); return render(); }
  if (action === 'go-advanced') { const f=ensureBrew().feedback; if (!f.taste || !f.strength) return showToast('请先完成两项基础感受'); return setScreen('advanced'); }
  if (action === 'save-record') { const f=ensureBrew().feedback; if (!f.taste || !f.strength) return showToast('请先完成两项基础感受'); saveBrewRecord(); return setScreen('brew-result'); }
  if (action === 'analyze-brew-feedback') { const record=state.journalRecords.find(item=>item.id===target.dataset.id); const tea=record && getTea(record.teaId); if (record && tea) analyzeBrewRecord(record, tea); return; }
  if (action === 'open-record') { state.activeRecordId=target.dataset.id; return setScreen('record-detail'); }
  if (action === 'open-latest-record') { state.activeRecordId=state.journalRecords.at(-1)?.id; return setScreen('record-detail'); }
  if (action === 'delete-record') { if (!window.confirm('确定删除这次泡茶记录吗？')) return; const recordId=state.activeRecordId; state.journalRecords=state.journalRecords.filter(item=>item.id!==recordId); GuanchaStores.preferenceEvidence.save({items:readPreferenceEvidence().filter(item=>item.source_brew_session_id!==recordId)}); saveState(); return setScreen('journal-day'); }
  if (action === 'show-evidence') return showToast('近期饮用证据：清爽花香、兰花与茉莉花偏好。');
  if (action === 'reset-demo') { if (!window.confirm('清除本地演示数据并恢复初始状态？')) return; runtimeImages.forEach(item=>URL.revokeObjectURL(item.url)); runtimeImages.clear(); GuanchaStores.clearAll(); state=structuredClone(defaultState); return setScreen('home'); }
  if (action === 'open-history') return showToast('选茶记录详情将在后续版本补齐');
});
document.addEventListener('submit', event => {
  const form = event.target.closest('form[data-action]'); if (!form) return;
  event.preventDefault(); const data = new FormData(form);
  if (form.dataset.action === 'submit-merchant-reply') { const reply=String(data.get('merchant-reply') || '').trim(); if (reply) { productAnalytics.track('merchant_reply_started', { candidate_id: currentCandidate()?.serverCandidateId, decision_version_id: state.decisionVersionId || undefined, metadata: { screen: state.screen } }); submitMerchantReply(reply); } return; }
  if (form.dataset.action === 'save-needs') {
    const nextNeed = { taste:data.get('taste').trim()||'清爽花香', purpose:data.get('purpose').trim()||'送礼', budget:data.get('budget').trim()||'150–300 元' };
    saveSelectionNeed(nextNeed); return;
  }
  if (form.dataset.action === 'save-stock') { const name=String(data.get('name')).trim(); if(!name) return showToast('请填写茶名'); state.warehouse.unshift({ id:`tea-${Date.now()}`, name, type:String(data.get('type'))||'不确定', aroma:String(data.get('aroma')).trim()||'不确定', status:'drinking', source:'手动入库', lastBrew:'还未泡过', records:0, art:'can', facts:['待补充'], risks:['产地与年份未记录'] }); saveState(); showToast('已加入茶仓库'); return setScreen('warehouse'); }
  if (form.dataset.action === 'save-plan') { const p=ensureBrew().plan; ['ware','water','grams','temp'].forEach(key=>p[key]=String(data.get(key)).trim()||p[key]); p.seconds=Math.min(60,Math.max(3,Number(data.get('seconds'))||10)); state.overlay=null; saveState(); render(); showToast('冲泡参数已更新'); }
});
document.addEventListener('input', event => {
  if (event.target.matches('[data-action="feedback-impression"]')) { ensureBrew().feedback.impression=event.target.value.slice(0,80); saveState(); return; }
  if (!event.target.matches('[data-action="sweetness"]')) return;
  state.o2.sweetness=Number(event.target.value);
  event.target.style.setProperty('--p', `${state.o2.sweetness}%`);
  const value = document.querySelector('.sweet-value'); if (value) value.textContent=sweetnessLabel(state.o2.sweetness);
  document.querySelectorAll('.sweet-labels span').forEach((label,index)=>label.classList.toggle('active', index * 25 === state.o2.sweetness));
});
document.addEventListener('change', event => { if (event.target.matches('[data-action="sweetness"]')) saveState(); });
async function addCandidateFromInput(files) {
  const added = await addCandidate(files);
  if (!added.ok) return showToast(added.message);
  setScreen('candidates');
  showToast('候选图片已加入比较，等待整理商品信息');
}
albumInput.addEventListener('change', async event => { if (event.target.files?.length) await addCandidateFromInput(event.target.files); event.target.value=''; });
cameraInput.addEventListener('change', async event => { if (event.target.files?.length) await addCandidateFromInput(event.target.files); event.target.value=''; });
candidateImageInput.addEventListener('change', async event => { if (pendingImageCandidateId && event.target.files?.length) await appendCandidateImage(pendingImageCandidateId, event.target.files); pendingImageCandidateId=null; event.target.value=''; });
function addSelectionHistory(candidate) {
  if (!candidate) return;
  const identity = GuanchaAdapters.buildSelectionHistoryIdentity({ candidates: state.candidates, selectedCandidate: candidate });
  const selectedId = candidateIdentity(candidate);
  const exists=state.history.some(item=>item.selected_candidate_id===selectedId);
  if (!exists) state.history.unshift({ date:'08.04', ...identity });
  saveState();
}
function setFeedback(field, value) {
  const feedback=ensureBrew().feedback;
  if (field === 'tags') {
    const values=feedback.tags; feedback.tags=values.includes(value) ? values.filter(item=>item!==value) : values.length>=2 ? values : [...values,value];
    return;
  }
  if (field === 'aroma') {
    const isExclusive=['没有明显香气','不确定'].includes(value);
    if (isExclusive) { feedback.aroma=feedback.aroma.includes(value) ? [] : [value]; return; }
    const values=feedback.aroma.filter(item=>!['没有明显香气','不确定'].includes(item));
    feedback.aroma=values.includes(value) ? values.filter(item=>item!==value) : values.length>=3 ? values : [...values,value];
    return;
  }
  if (field?.startsWith('advanced-')) { feedback.advanced[field.slice(9)]=value; return; }
  feedback[field] = field === 'score' ? Number(value) : value;
}
function saveBrewRecord() {
  const brew=ensureBrew(); const tea=getTea(brew.teaId); const actuals=brew.completed.length ? brew.completed : [{number:1,suggested:brew.plan.seconds,actual:brew.plan.seconds}];
  const suggestion=brew.feedback.strength==='偏淡'?'下次首泡可以延长约 2 秒':brew.feedback.strength==='偏浓'?'下次首泡可以缩短约 2 秒':'暂时保持本次参数';
  const record={ id:`record-${Date.now()}`, date:journalDate(), teaId:tea.id, infusions:structuredClone(actuals), plan:structuredClone(brew.plan), feedback:structuredClone(brew.feedback), suggestion, createdAt:'现在' };
  state.journalRecords.push(record); state.activeRecordId=record.id; tea.records=(tea.records||0)+1; tea.lastBrew='今天'; state.brew=null; saveState(); analyzeBrewRecord(record, tea);
}
function readPreferenceEvidence() { return GuanchaStores.preferenceEvidence.load({items:[]}).items; }
function savePreferenceEvidence(items) {
  const current = readPreferenceEvidence();
  const incoming = (Array.isArray(items) ? items : []).filter(item => item && item.source_brew_session_id && item.confidence === 'low');
  const existing = current.filter(item => !incoming.some(next => next.source_brew_session_id === item.source_brew_session_id));
  GuanchaStores.preferenceEvidence.save({items:[...existing, ...incoming]});
}
async function analyzeBrewRecord(record, tea) {
  if (!apiClient.isConfigured) return;
  const numeric=value=>Number(String(value||'').match(/[\d.]+/)?.[0])||null; const last=record.infusions.at(-1)||{}; const plan=record.plan||{};
  const payload={brew_session_id:record.id,tea_record_id:tea.id,extraction_version_id:tea.extraction_version_id||null,system_recommended_parameters:{tea_amount:numeric(plan.grams),water_volume:numeric(plan.water),water_temperature:numeric(plan.temp),steep_time:last.suggested||null,infusion_number:last.number||1},actual_brew_parameters:{tea_amount:numeric(plan.grams),water_volume:numeric(plan.water),water_temperature:null,steep_time:last.actual||null,infusion_number:last.number||1},structured_feedback:{aroma:(record.feedback.aroma||[]).join('、')||null,bitterness:record.feedback.strength==='偏浓'?'明显':null,mouthfeel:(record.feedback.tags||[]).join('、')||null,aftertaste:record.feedback.advanced?.['回甘']||null,salivation:record.feedback.advanced?.['生津']||null,finish:record.feedback.advanced?.['余韵']||null,overall_rating:record.feedback.score||null,free_text_note:record.feedback.impression||null},taste_card_snapshot:{o1:state.o1,o2:state.o2},recent_preference_evidence:readPreferenceEvidence(),client_feedback_id:GuanchaApi.createIdempotencyKey()};
  try { const result=await apiClient.analyzeBrewFeedback(payload,payload.client_feedback_id); record.feedbackAnalysis=result; record.feedbackAnalysisStatus='completed'; savePreferenceEvidence(result.preference_evidence||[]); saveState(); render(); } catch { record.feedbackAnalysis=null; record.feedbackAnalysisStatus='failed'; saveState(); render(); }
}
function copyText(value) { navigator.clipboard?.writeText(value).then(() => showToast('已复制，可直接发给商家')).catch(() => showToast('复制失败，请手动选择文字')); }
function slide(direction) { if (state.candidates.length < 2) return showToast('当前只有 1 款候选茶'); state.activeCandidate=(state.activeCandidate+direction+state.candidates.length)%state.candidates.length; state.activeCandidateId=candidateIdentity(currentCandidate()); saveState(); render(); }
function bindResultSwipe() {
  const stage = document.querySelector('#result-stage');
  if (!stage) return;
  let startX = 0, startY = 0, startedOnImage = false;
  stage.addEventListener('pointerdown', event => {
    startX = event.clientX; startY = event.clientY;
    startedOnImage = Boolean(event.target.closest('.result-image-gallery'));
  });
  stage.addEventListener('pointerup', event => {
    const dx = event.clientX - startX, dy = event.clientY - startY;
    if (startedOnImage || Math.abs(dy) > 18) return;
    if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy)) return slide(dx < 0 ? 1 : -1);
    // On desktop the visible white card edges are part of the stage's
    // decorative pseudo-elements. Treat a click there as a carousel control.
    if (event.target !== stage) return;
    const bounds = stage.getBoundingClientRect();
    if (event.clientX >= bounds.right - 48) return slide(1);
    if (event.clientX <= bounds.left + 48) return slide(-1);
  });
}
function loadPublicConfig() {
  if (!apiClient.isConfigured) return;
  apiClient.getPublicConfig().then((config) => {
    GuanchaPublicConfig.apply(config);
    state = normalizeState(state);
    render();
    resumeLiveBackendState();
  }).catch(() => { /* 静态演示与网络失败均使用已批准的本地默认配置。 */ });
}

render();
loadPublicConfig();
