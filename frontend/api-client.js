(function (global) {
  'use strict';

  const API_BASE = '/api/v1';
  const publicRoutes = Object.freeze({ health: '/health', publicConfig: '/config/public' });

  function createIdempotencyKey() {
    const bytes = new Uint8Array(16);
    if (global.crypto && typeof global.crypto.getRandomValues === 'function') global.crypto.getRandomValues(bytes);
    else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function ApiContractError(code, message, details) {
    this.name = 'ApiContractError'; this.code = code; this.message = message; this.details = details || null;
  }
  ApiContractError.prototype = Object.create(Error.prototype);

  function normalizeError(response, fallbackMessage) {
    const error = response && response.body && response.body.error;
    return new ApiContractError((error && error.code) || 'request_failed', (error && error.message) || fallbackMessage || '请求暂时无法完成', error || null);
  }
  function unconfiguredTransport() { return Promise.reject(new ApiContractError('api_not_configured', '服务端接口尚未配置；未执行识别、判断或复判。')); }
  function fetchTransport(baseUrl, timeoutMs) {
    return ({ method, path, payload, headers }) => {
      const controller = global.AbortController ? new global.AbortController() : null;
      const timer = controller ? global.setTimeout(() => controller.abort(), timeoutMs) : null;
      return global.fetch(`${baseUrl}${path}`, {
        method, headers: { Accept: 'application/json', ...headers }, body: payload === null ? undefined : payload,
        ...(controller ? { signal: controller.signal } : {}),
      }).then(async (response) => ({ ok: response.ok, body: await response.json().catch(() => null) }))
        .catch(() => ({ ok: false, body: { error: { code: 'network_unavailable', message: 'Network request failed.' } } }))
        .finally(() => { if (timer) global.clearTimeout(timer); });
    };
  }

  function createApiClient(options) {
    const baseUrl = options && options.baseUrl;
    const clientId = options && options.clientId;
    const timeoutMs = Number.isFinite(options && options.timeoutMs) ? options.timeoutMs : 15000;
    const getAccessToken = options && options.getAccessToken;
    const authRequired = options && options.authRequired === true;
    const transport = (options && options.transport) || (baseUrl ? fetchTransport(baseUrl.replace(/\/$/, ''), timeoutMs) : unconfiguredTransport);
    async function ownerHeaders(opts) {
      if (!opts.ownerScoped && !opts.bearerRequired) return clientId ? { 'X-Client-Id': clientId } : {};
      let token = null;
      if (typeof getAccessToken === 'function') {
        try { token = await getAccessToken(); }
        catch { throw new ApiContractError('authentication_required', '登录状态不可用，请重新登录。'); }
      }
      if (typeof token === 'string' && token.trim()) return { Authorization: `Bearer ${token.trim()}` };
      if (opts.bearerRequired || authRequired) throw new ApiContractError('authentication_required', '请先登录后再继续。');
      return clientId ? { 'X-Client-Id': clientId } : {};
    }
    async function request(method, path, payload, requestOptions) {
      const opts = requestOptions || {};
      const idempotencyKey = opts.idempotent ? (opts.idempotencyKey || createIdempotencyKey()) : null;
      const headers = {
        ...(await ownerHeaders(opts)),
        ...(global.GuanchaProductAnalytics ? { 'X-Analytics-Session-Id': global.GuanchaProductAnalytics.getSessionId() } : {}),
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
        ...(opts.headers || {}),
      };
      return transport({ method, path, payload: payload === undefined ? null : payload, headers })
        .then((response) => { if (!response || response.ok === false) throw normalizeError(response); return response.body; });
    }
    return Object.freeze({
      isConfigured: transport !== unconfiguredTransport,
      getHealth: () => request('GET', publicRoutes.health),
      getPublicConfig: () => request('GET', `${API_BASE}${publicRoutes.publicConfig}`),
      getMe: () => request('GET', `${API_BASE}/me`, null, { bearerRequired: true }),
      getMyPreferences: () => request('GET', `${API_BASE}/me/preferences`, null, { bearerRequired: true }),
      putMyPreferences: (profile, expectedRevision) => request('PUT', `${API_BASE}/me/preferences`, JSON.stringify({ profile, expected_revision: expectedRevision }), { bearerRequired: true, headers: { 'Content-Type': 'application/json' } }),
      getMyPreferenceEvidence: () => request('GET', `${API_BASE}/me/preference-evidence`, null, { bearerRequired: true }),
      persistMyPreferenceEvidence: (items) => request('PUT', `${API_BASE}/me/preference-evidence`, JSON.stringify({ items }), { bearerRequired: true, headers: { 'Content-Type': 'application/json' } }),
      listMySelectionSessions: (limit = 20) => request('GET', `${API_BASE}/me/selection-sessions?limit=${encodeURIComponent(limit)}`, null, { bearerRequired: true }),
      getMyWarehouse: () => request('GET', `${API_BASE}/me/warehouse`, null, { bearerRequired: true }),
      putMyWarehouseTea: (teaId, tea, expectedRevision) => request('PUT', `${API_BASE}/me/warehouse/${encodeURIComponent(teaId)}`, JSON.stringify({ tea, expected_revision: expectedRevision }), { bearerRequired: true, headers: { 'Content-Type': 'application/json' } }),
      getMyBrewJournal: () => request('GET', `${API_BASE}/me/brew-journal`, null, { bearerRequired: true }),
      putMyBrewJournalEntry: (entryId, entry, expectedRevision) => request('PUT', `${API_BASE}/me/brew-journal/${encodeURIComponent(entryId)}`, JSON.stringify({ entry, expected_revision: expectedRevision }), { bearerRequired: true, headers: { 'Content-Type': 'application/json' } }),
      createSelectionSession: (need, idempotencyKey, recentPreferenceEvidence = []) => request('POST', `${API_BASE}/selection-sessions`, JSON.stringify({ need, recent_preference_evidence: recentPreferenceEvidence }), { ownerScoped: true, idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getSelectionSession: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}`, null, { ownerScoped: true }),
      updateSelectionSession: (sessionId, need, recentPreferenceEvidence = []) => request('PATCH', `${API_BASE}/selection-sessions/${sessionId}`, JSON.stringify({ need, recent_preference_evidence: recentPreferenceEvidence }), { ownerScoped: true, headers: { 'Content-Type': 'application/json' } }),
      createCandidate: (sessionId, candidate, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/candidates`, JSON.stringify(candidate), { ownerScoped: true, idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      listCandidates: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/candidates`, null, { ownerScoped: true }),
      getSelectionSnapshot: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/snapshot`, null, { ownerScoped: true }),
      deleteCandidate: (candidateId) => request('DELETE', `${API_BASE}/candidates/${candidateId}`, null, { ownerScoped: true }),
      uploadCandidateImage: (candidateId, file, idempotencyKey) => { const form = new FormData(); form.append('file', file, file.name || 'product-image'); return request('POST', `${API_BASE}/candidates/${candidateId}/images`, form, { ownerScoped: true, idempotent: true, idempotencyKey }); },
      deleteCandidateImage: (imageId) => request('DELETE', `${API_BASE}/candidate-images/${imageId}`, null, { ownerScoped: true }),
      getJob: (jobId) => request('GET', `${API_BASE}/jobs/${jobId}`, null, { ownerScoped: true }),
      getExtractionVersion: (versionId) => request('GET', `${API_BASE}/extraction-versions/${versionId}`, null, { ownerScoped: true }),
      getCurrentExtraction: (candidateId) => request('GET', `${API_BASE}/candidates/${candidateId}/current-extraction`, null, { ownerScoped: true }),
      analyzeSelectionSession: (sessionId, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/analyze`, null, { ownerScoped: true, idempotent: true, idempotencyKey }),
      getDecisionVersion: (versionId) => request('GET', `${API_BASE}/decision-versions/${versionId}`, null, { ownerScoped: true }),
      generateDecisionQuestions: (versionId, idempotencyKey) => request('POST', `${API_BASE}/decision-versions/${versionId}/questions`, null, { ownerScoped: true, idempotent: true, idempotencyKey }),
      getDecisionQuestions: (versionId) => request('GET', `${API_BASE}/decision-versions/${versionId}/questions`, null, { ownerScoped: true }),
      createMerchantReply: (sessionId, reply, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/merchant-replies`, JSON.stringify(reply), { ownerScoped: true, idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getMerchantReply: (replyId) => request('GET', `${API_BASE}/merchant-replies/${replyId}`, null, { ownerScoped: true }),
      rejudgeMerchantReply: (sessionId, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/rejudge`, '{}', { ownerScoped: true, idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getDecisionDelta: (deltaId) => request('GET', `${API_BASE}/decision-deltas/${deltaId}`, null, { ownerScoped: true }),
      analyzeBrewFeedback: (payload, idempotencyKey) => request('POST', `${API_BASE}/brew-feedback/analyze`, JSON.stringify(payload), { idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getCurrentDecision: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/current-decision`, null, { ownerScoped: true }),
      getSelectionAnswer: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/answer`, null, { ownerScoped: true }),
      retryExtraction: (candidateId, idempotencyKey) => request('POST', `${API_BASE}/candidates/${candidateId}/extraction-jobs`, null, { ownerScoped: true, idempotent: true, idempotencyKey }),
      _request: request,
    });
  }

  function getOrCreateClientId() {
    const key = 'guancha.anonymous-client-id.v1';
    const existing = global.localStorage && global.localStorage.getItem(key);
    if (existing && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(existing)) return existing;
    const created = createIdempotencyKey();
    global.localStorage && global.localStorage.setItem(key, created);
    return created;
  }

  global.GuanchaApi = { API_BASE, publicRoutes, ApiContractError, createApiClient, createIdempotencyKey, getOrCreateClientId, normalizeError };
}(window));
