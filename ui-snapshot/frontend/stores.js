(function (global) {
  'use strict';

  function safeRead(key, fallback) {
    try { return JSON.parse(global.localStorage.getItem(key)) || fallback; } catch { return fallback; }
  }
  function safeWrite(key, value) {
    try { global.localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
  }
  function clone(value) { return global.structuredClone ? global.structuredClone(value) : JSON.parse(JSON.stringify(value)); }
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const replyStatus = new Set(['submitted', 'parsed', 'failed']);
  const processingStatus = new Set(['queued', 'processing', 'completed', 'failed']);
  const parseStatus = new Set(['answered', 'partially-answered', 'evasive', 'not-answered', 'conflicting']);
  const extractionStatus = new Set(['idle', 'empty', 'uploading', 'queued', 'processing', 'completed', 'failed', 'stale']);
  const decisionStatus = new Set(['not_requested', 'loading', 'ready', 'failed']);
  const questionStatus = new Set(['idle', 'loading', 'completed', 'ready', 'rejudging', 'not-needed', 'failed']);
  const deltaStatus = new Set(['idle', 'loading', 'ready', 'failed']);
  const screens = new Set(['home','candidates','o1','o2','analysis','result','rejudge','ownership','warehouse','warehouse-detail','warehouse-add','journal','journal-day','choose-tea','prepare','timer','infusion-done','feedback','advanced','brew-result','record-detail','settings']);
  const localCandidatePattern = /^local-candidate-\d{1,16}(?:-\d{1,2})?$/;
  const localImagePattern = /^(?:local-image-\d{1,16}-[0-9a-f]{1,20}|server-[0-9a-f-]{36})$/i;
  const extractionErrors = new Set(['network_error','result_unavailable','ai_timeout','ai_provider_error','ai_schema_invalid','worker_interrupted','temporary_image_cleanup_failed','unsafe_or_corrupt_image']);
  const preferenceValues = new Set(['绿茶','花香茶','乌龙茶','红茶','焙火茶','陈香茶','奶茶 / 果茶','美式 / 黑咖啡','拿铁','冷萃','浅烘手冲','深烘咖啡','纯牛奶','酸奶','豆浆','燕麦奶','椰奶','柑橘类果汁','苹果 / 梨汁','桃子 / 荔枝饮品','葡萄 / 莓果汁','热带水果汁','蔬菜汁','椰子水','茉莉花','兰花','桂花','玫瑰','水蜜桃','荔枝','梨','柑橘','桂圆','红枣','青梅','葡萄干','嫩叶','青草','竹叶','青豆','板栗','炒黄豆','烤花生','烤面包','蜂蜜','焦糖','糯米','陈皮']);
  function isIsoTimestamp(value) { return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)); }
  function sanitizedReply(reply) {
    if (!reply || typeof reply !== 'object' || Array.isArray(reply)) return null;
    const cleaned = {};
    for (const field of ['id', 'selection_session_id', 'decision_version_id', 'followup_question_id', 'candidate_id']) {
      if (typeof reply[field] === 'string' && uuidPattern.test(reply[field])) cleaned[field] = reply[field];
    }
    if (replyStatus.has(reply.status)) cleaned.status = reply.status;
    if (processingStatus.has(reply.processing_status)) cleaned.processing_status = reply.processing_status;
    if (parseStatus.has(reply.parse_status)) cleaned.parse_status = reply.parse_status;
    for (const field of ['created_at', 'updated_at']) if (isIsoTimestamp(reply[field])) cleaned[field] = reply[field];
    return Object.keys(cleaned).length ? cleaned : null;
  }
  function persistedMerchantReplies(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).flatMap(([questionId, reply]) => {
      const cleaned = uuidPattern.test(questionId) ? sanitizedReply(reply) : null;
      return cleaned ? [[questionId, cleaned]] : [];
    }));
  }
  function persistedMerchantReplyIds(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([questionId, replyId]) => uuidPattern.test(questionId) && typeof replyId === 'string' && uuidPattern.test(replyId)));
  }
  function safeUuid(value) { return typeof value === 'string' && uuidPattern.test(value) ? value : null; }
  function safeLocalId(value, pattern) { return typeof value === 'string' && pattern.test(value) ? value : null; }
  function imageAnchor(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const id = safeLocalId(value.id, localImagePattern);
    const serverImageId = safeUuid(value.serverImageId);
    if (!id && !serverImageId) return null;
    const result = {};
    if (id) result.id = id;
    if (serverImageId) result.serverImageId = serverImageId;
    if (extractionStatus.has(value.status)) result.status = value.status;
    if (typeof value.localOnly === 'boolean') result.localOnly = value.localOnly;
    return result;
  }
  function candidateAnchor(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const id = safeLocalId(value.id, localCandidatePattern);
    const serverCandidateId = safeUuid(value.serverCandidateId);
    if (!id && !serverCandidateId) return null;
    const result = {};
    if (id) result.id = id;
    if (serverCandidateId) result.serverCandidateId = serverCandidateId;
    if (/^[A-E]$/.test(value.letter)) result.letter = value.letter;
    if (extractionStatus.has(value.extractionStatus)) result.extractionStatus = value.extractionStatus;
    if (extractionErrors.has(value.jobError)) result.jobError = value.jobError;
    for (const field of ['jobId', 'extractionVersionId']) {
      const valid = safeUuid(value[field]);
      if (valid) result[field] = valid;
    }
    result.images = (Array.isArray(value.images) ? value.images : []).slice(0, 2).map(imageAnchor).filter(Boolean);
    return result;
  }
  function selectionBridgeStore() {
    const key = 'guancha.selection-bridge.v1';
    const version = 3;
    function sanitize(value) {
      const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      const payload = {
        sessionId: safeUuid(input.sessionId),
        candidates: (Array.isArray(input.candidates) ? input.candidates : []).slice(0, 5).map(candidateAnchor).filter(Boolean),
        merchantReplyIds: persistedMerchantReplyIds(input.merchantReplyIds),
        merchantReplies: persistedMerchantReplies(input.merchantReplies),
      };
      for (const field of ['decisionVersionId', 'decisionJobId', 'questionDecisionVersionId', 'rejudgeJobId']) {
        payload[field] = safeUuid(input[field]);
      }
      if (decisionStatus.has(input.decisionStatus)) payload.decisionStatus = input.decisionStatus;
      if (questionStatus.has(input.questionStatus)) payload.questionStatus = input.questionStatus;
      if (deltaStatus.has(input.deltaStatus)) payload.deltaStatus = input.deltaStatus;
      return { schemaVersion: version, ...payload };
    }
    return {
      key,
      load(fallback) {
        let raw;
        try { raw = JSON.parse(global.localStorage.getItem(key)); }
        catch { global.localStorage.removeItem(key); return clone(fallback); }
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) { global.localStorage.removeItem(key); return clone(fallback); }
        const cleaned = sanitize(raw);
        // Reading legacy state is itself a privacy migration: the backing
        // localStorage value must no longer retain merchant free text.
        safeWrite(key, cleaned);
        return { ...clone(fallback), ...cleaned };
      },
      save(value) { return safeWrite(key, sanitize(value)); },
    };
  }
  function uiSessionStore() {
    const key = 'guancha.ui-session.v1';
    function sanitize(value) {
      const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      const result = { schemaVersion: 2 };
      if (screens.has(input.screen)) result.screen = input.screen;
      if (typeof input.openDrink === 'string' && ['tea','coffee','milk','juice',''].includes(input.openDrink)) result.openDrink = input.openDrink;
      if (typeof input.activeSelectionFlow === 'boolean') result.activeSelectionFlow = input.activeSelectionFlow;
      if (['onboarding','edit'].includes(input.preferenceFlow)) result.preferenceFlow = input.preferenceFlow;
      if (['bought','owned'].includes(input.ownershipChoice)) result.ownershipChoice = input.ownershipChoice;
      const activeCandidateId = safeUuid(input.activeCandidateId) || safeLocalId(input.activeCandidateId, localCandidatePattern);
      if (activeCandidateId) result.activeCandidateId = activeCandidateId;
      const o1 = input.o1 && typeof input.o1 === 'object' && !Array.isArray(input.o1) ? input.o1 : {};
      result.o1 = Object.fromEntries(['tea','coffee','milk','juice'].map(keyName => [keyName, (Array.isArray(o1[keyName]) ? o1[keyName] : []).filter(item => preferenceValues.has(item)).slice(0, 8)]));
      const o2 = input.o2 && typeof input.o2 === 'object' && !Array.isArray(input.o2) ? input.o2 : {};
      result.o2 = { flavors: (Array.isArray(o2.flavors) ? o2.flavors : []).filter(item => preferenceValues.has(item)).slice(0, 5) };
      if (Number.isInteger(o2.sweetness) && o2.sweetness >= 0 && o2.sweetness <= 100) result.o2.sweetness = o2.sweetness;
      return result;
    }
    return {
      key,
      load(fallback) {
        let raw;
        try { raw = JSON.parse(global.localStorage.getItem(key)); }
        catch { global.localStorage.removeItem(key); return clone(fallback); }
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) { if (global.localStorage.getItem(key)) global.localStorage.removeItem(key); return clone(fallback); }
        const cleaned = sanitize(raw); safeWrite(key, cleaned);
        return { ...clone(fallback), ...cleaned };
      },
      save(value) { return safeWrite(key, sanitize(value)); },
    };
  }
  function boundedString(value, limit = 160) { return typeof value === 'string' && value.length <= limit ? value : null; }
  function boundedNumber(value, minimum, maximum) { return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum ? value : null; }
  const teaIdPattern = /^(?:tea-\d{1,16}|spring|peony|puer)$/;
  const recordIdPattern = /^(?:record-\d{1,16}|demo-\d{4})$/;
  const brewSourcePattern = /^(?:(?:record|brew)-[a-z0-9-]{1,40}|[0-9a-f]{8}-[0-9a-f-]{27})$/i;
  const preferenceTargetTypes = new Set(['tea-style','aroma','roast','bitterness','astringency','sweetness','mouthfeel','aftertaste','salivation','finish']);
  const preferencePolarities = new Set(['positive','negative']);
  const preferenceSources = new Set(['tea','brewing','uncertain']);
  const warehouseFactTokens = new Set(['轻火焙制','2026 年春茶','支持 10g 试饮装','茶类已确认：白茶','茶类已确认：黑茶','来自本次截图提取','待补充']);
  const warehouseRiskTokens = new Set(['具体产地仍待确认','年份与产地未记录','原始购买信息待补','产地与年份待补','产地与年份未记录']);
  const decisionRiskTokens = new Set(['season_claim_conflict','origin_claim_conflict','price_claim_conflict','价格或规格不支持可接受试错','不能从香型推导焙火程度','本次需求不应被长期偏好替代','营销词与可信度不存在等价关系','信息充分度不等同于商品真实性','冲突不能被正向信息抵消','试饮前仍需保留体验不确定性','未知价格不能视为符合预算']);
  function safeStringArray(value, limit = 8, itemLimit = 200) {
    return (Array.isArray(value) ? value : []).slice(0, limit).flatMap(item => {
      const safe = boundedString(item, itemLimit); return safe === null ? [] : [safe];
    });
  }
  function warehouseAnchor(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || !teaIdPattern.test(value.id || '')) return null;
    const result = { id: value.id };
    for (const field of ['name','product_name','type','tea_category','tea_subtype','origin','roast_or_style','aroma','source','lastBrew']) {
      const safe = boundedString(value[field], field === 'name' || field === 'product_name' ? 120 : 200); if (safe !== null) result[field] = safe;
    }
    if (['drinking','paused','finished'].includes(value.status)) result.status = value.status;
    if (['can','gaiwan','cup','bag'].includes(value.art)) result.art = value.art;
    if (Number.isInteger(value.records) && value.records >= 0 && value.records <= 10000) result.records = value.records;
    for (const field of ['extraction_version_id','candidate_id','sourceDecisionId']) { const safe = safeUuid(value[field]); if (safe) result[field] = safe; }
    if (isIsoTimestamp(value.joined_at)) result.joined_at = value.joined_at;
    result.facts = safeStringArray(value.facts, 8, 200).filter(item => warehouseFactTokens.has(item));
    result.risks = safeStringArray(value.risks, 8, 200).filter(item => warehouseRiskTokens.has(item) || decisionRiskTokens.has(item));
    result.risk_flags = safeStringArray(value.risk_flags, 8, 80).filter(item => decisionRiskTokens.has(item));
    return result;
  }
  function journalAnchor(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || !recordIdPattern.test(value.id || '') || !teaIdPattern.test(value.teaId || '') || !/^\d{4}-\d{2}-\d{2}$/.test(value.date || '')) return null;
    const result = { id: value.id, date: value.date, teaId: value.teaId };
    result.infusions = (Array.isArray(value.infusions) ? value.infusions : []).slice(0, 20).flatMap(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
      const infusion = {};
      for (const field of ['number','suggested','actual']) { const safe = boundedNumber(item[field], 0, 600); if (safe !== null) infusion[field] = safe; }
      return Object.keys(infusion).length ? [infusion] : [];
    });
    const plan = value.plan && typeof value.plan === 'object' && !Array.isArray(value.plan) ? value.plan : {};
    result.plan = Object.fromEntries(['ware','water','grams','temp'].flatMap(field => { const safe = boundedString(plan[field], 40); return safe === null ? [] : [[field, safe]]; }));
    const feedback = value.feedback && typeof value.feedback === 'object' && !Array.isArray(value.feedback) ? value.feedback : {};
    result.feedback = {};
    for (const field of ['taste','strength','impression','repurchase']) { const safe = boundedString(feedback[field], field === 'impression' ? 500 : 80); if (safe !== null) result.feedback[field] = safe; }
    result.feedback.tags = safeStringArray(feedback.tags, 3, 40);
    result.feedback.aroma = safeStringArray(feedback.aroma, 3, 40);
    const score = boundedNumber(feedback.score, 1, 5); if (score !== null) result.feedback.score = score;
    const advanced = feedback.advanced && typeof feedback.advanced === 'object' && !Array.isArray(feedback.advanced) ? feedback.advanced : {};
    result.feedback.advanced = Object.fromEntries(['回甘','生津','余韵'].flatMap(field => { const safe = boundedString(advanced[field], 80); return safe === null ? [] : [[field, safe]]; }));
    for (const field of ['suggestion','createdAt']) { const safe = boundedString(value[field], 200); if (safe !== null) result[field] = safe; }
    return result;
  }
  function historyAnchor(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || !/^\d{2}\.\d{2}$/.test(value.date || '')) return null;
    const result = { date: value.date };
    for (const field of ['recommended_candidate_id','selected_candidate_id']) {
      const safe = safeUuid(value[field]) || safeLocalId(value[field], localCandidatePattern); if (safe) result[field] = safe;
    }
    if (/^[A-E]$/.test(value.recommended_candidate_label)) result.recommended_candidate_label = value.recommended_candidate_label;
    if (/^[A-E]$/.test(value.selected_candidate_label)) result.selected_candidate_label = value.selected_candidate_label;
    else if (/^[A-E]$/.test(value.winner)) result.selected_candidate_label = value.winner;
    return Object.keys(result).length > 1 ? result : null;
  }
  function postPurchaseStore() {
    const key = 'guancha.local-post-purchase.v1';
    function sanitize(value) {
      const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      const selectedTeaId = teaIdPattern.test(input.selectedTeaId || '') ? input.selectedTeaId : null;
      return { schemaVersion: 2,
        warehouse: (Array.isArray(input.warehouse) ? input.warehouse : []).slice(0, 100).map(warehouseAnchor).filter(Boolean),
        journalRecords: (Array.isArray(input.journalRecords) ? input.journalRecords : []).slice(-365).map(journalAnchor).filter(Boolean),
        history: (Array.isArray(input.history) ? input.history : []).slice(0, 100).map(historyAnchor).filter(Boolean),
        selectedTeaId,
      };
    }
    return { key, load(fallback) { const raw = safeRead(key, null); if (!raw || typeof raw !== 'object' || Array.isArray(raw)) { if (global.localStorage.getItem(key)) global.localStorage.removeItem(key); return clone(fallback); } const cleaned = sanitize(raw); safeWrite(key, cleaned); return { ...clone(fallback), ...cleaned }; }, save(value) { return safeWrite(key, sanitize(value)); } };
  }
  function preferenceEvidenceStore() {
    const key = 'guancha.preference-evidence.v1';
    function itemAnchor(item) {
      if (!item || typeof item !== 'object' || Array.isArray(item) || item.confidence !== 'low' || !safeUuid(item.id) || !preferenceTargetTypes.has(item.target_type) || !/^[a-z0-9-]{1,64}$/.test(item.target_value || '') || !preferencePolarities.has(item.polarity) || !preferenceSources.has(item.issue_source) || !brewSourcePattern.test(item.source_brew_session_id || '') || !isIsoTimestamp(item.created_at)) return null;
      return { id: item.id, target_type: item.target_type, target_value: item.target_value, polarity: item.polarity, confidence: 'low', issue_source: item.issue_source, source_brew_session_id: item.source_brew_session_id, created_at: item.created_at };
    }
    function sanitize(value) {
      const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
      const cutoff = Date.now() - 90 * 24 * 60 * 60 * 1000; const seen = new Set();
      const items = (Array.isArray(input.items) ? input.items : []).flatMap(item => { const safe = itemAnchor(item); if (!safe || seen.has(safe.source_brew_session_id) || Date.parse(safe.created_at) < cutoff) return []; seen.add(safe.source_brew_session_id); return [safe]; }).slice(-12);
      return { schemaVersion: 2, items };
    }
    return { key, load(fallback) { const raw = safeRead(key, null); if (!raw || typeof raw !== 'object' || Array.isArray(raw)) { if (global.localStorage.getItem(key)) global.localStorage.removeItem(key); return clone(fallback); } const cleaned = sanitize(raw); safeWrite(key, cleaned); return { ...clone(fallback), ...cleaned }; }, save(value) { return safeWrite(key, sanitize(value)); } };
  }
  const pendingImageDatabase = 'guancha.pending-images.v1';
  const pendingImageStore = 'images';
  function withPendingImageStore(mode, callback) {
    if (!global.indexedDB) return Promise.resolve(null);
    return new Promise((resolve) => {
      const request = global.indexedDB.open(pendingImageDatabase, 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(pendingImageStore)) request.result.createObjectStore(pendingImageStore);
      };
      request.onerror = () => resolve(null);
      request.onsuccess = () => {
        const database = request.result;
        const transaction = database.transaction(pendingImageStore, mode);
        const store = transaction.objectStore(pendingImageStore);
        callback(store, resolve);
        transaction.oncomplete = () => database.close();
        transaction.onerror = () => { database.close(); resolve(null); };
      };
    });
  }
  const pendingImages = {
    save(id, file) { return withPendingImageStore('readwrite', (store, resolve) => { const request = store.put(file, id); request.onsuccess = () => resolve(true); request.onerror = () => resolve(false); }); },
    load(id) { return withPendingImageStore('readonly', (store, resolve) => { const request = store.get(id); request.onsuccess = () => resolve(request.result || null); request.onerror = () => resolve(null); }); },
    remove(id) { return withPendingImageStore('readwrite', (store, resolve) => { const request = store.delete(id); request.onsuccess = () => resolve(true); request.onerror = () => resolve(false); }); },
    clear() { return withPendingImageStore('readwrite', (store, resolve) => { const request = store.clear(); request.onsuccess = () => resolve(true); request.onerror = () => resolve(false); }); },
  };
  const stores = {
    uiSession: uiSessionStore(),
    selectionBridge: selectionBridgeStore(),
    localPostPurchase: postPurchaseStore(),
    preferenceEvidence: preferenceEvidenceStore(),
    pendingImages,
    legacy: { load: () => safeRead('guancha-prototype-v2', null), clear: () => global.localStorage.removeItem('guancha-prototype-v2') },
  };
  stores.migrateLegacy = () => {
    if (!global.localStorage.getItem('guancha-prototype-v2')) return false;
    try {
      const legacy = stores.legacy.load() || {};
      if (!global.localStorage.getItem(stores.uiSession.key)) stores.uiSession.save(legacy);
      if (!global.localStorage.getItem(stores.selectionBridge.key)) stores.selectionBridge.save(legacy);
      if (!global.localStorage.getItem(stores.localPostPurchase.key)) stores.localPostPurchase.save(legacy);
      return true;
    } finally {
      stores.legacy.clear();
    }
  };
  stores.clearAll = () => {
    [stores.uiSession.key, stores.selectionBridge.key, stores.localPostPurchase.key, stores.preferenceEvidence.key, 'guancha-prototype-v2'].forEach((key) => global.localStorage.removeItem(key));
    pendingImages.clear();
  };
  global.GuanchaStores = stores;
}(window));
