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
    const transport = (options && options.transport) || (baseUrl ? fetchTransport(baseUrl.replace(/\/$/, ''), timeoutMs) : unconfiguredTransport);
    function request(method, path, payload, requestOptions) {
      const opts = requestOptions || {};
      const idempotencyKey = opts.idempotent ? (opts.idempotencyKey || createIdempotencyKey()) : null;
      const headers = {
        ...(clientId ? { 'X-Client-Id': clientId } : {}),
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
      createSelectionSession: (need, idempotencyKey, recentPreferenceEvidence = []) => request('POST', `${API_BASE}/selection-sessions`, JSON.stringify({ need, recent_preference_evidence: recentPreferenceEvidence }), { idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getSelectionSession: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}`),
      updateSelectionSession: (sessionId, need, recentPreferenceEvidence = []) => request('PATCH', `${API_BASE}/selection-sessions/${sessionId}`, JSON.stringify({ need, recent_preference_evidence: recentPreferenceEvidence }), { headers: { 'Content-Type': 'application/json' } }),
      createCandidate: (sessionId, candidate, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/candidates`, JSON.stringify(candidate), { idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      listCandidates: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/candidates`),
      getSelectionSnapshot: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/snapshot`),
      deleteCandidate: (candidateId) => request('DELETE', `${API_BASE}/candidates/${candidateId}`),
      uploadCandidateImage: (candidateId, file, idempotencyKey) => { const form = new FormData(); form.append('file', file, file.name || 'product-image'); return request('POST', `${API_BASE}/candidates/${candidateId}/images`, form, { idempotent: true, idempotencyKey }); },
      deleteCandidateImage: (imageId) => request('DELETE', `${API_BASE}/candidate-images/${imageId}`),
      getJob: (jobId) => request('GET', `${API_BASE}/jobs/${jobId}`),
      getCurrentExtraction: (candidateId) => request('GET', `${API_BASE}/candidates/${candidateId}/current-extraction`),
      analyzeSelectionSession: (sessionId, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/analyze`, null, { idempotent: true, idempotencyKey }),
      getDecisionVersion: (versionId) => request('GET', `${API_BASE}/decision-versions/${versionId}`),
      generateDecisionQuestions: (versionId, idempotencyKey) => request('POST', `${API_BASE}/decision-versions/${versionId}/questions`, null, { idempotent: true, idempotencyKey }),
      getDecisionQuestions: (versionId) => request('GET', `${API_BASE}/decision-versions/${versionId}/questions`),
      createMerchantReply: (sessionId, reply, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/merchant-replies`, JSON.stringify(reply), { idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getMerchantReply: (replyId) => request('GET', `${API_BASE}/merchant-replies/${replyId}`),
      rejudgeMerchantReply: (sessionId, idempotencyKey) => request('POST', `${API_BASE}/selection-sessions/${sessionId}/rejudge`, '{}', { idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getDecisionDelta: (deltaId) => request('GET', `${API_BASE}/decision-deltas/${deltaId}`),
      analyzeBrewFeedback: (payload, idempotencyKey) => request('POST', `${API_BASE}/brew-feedback/analyze`, JSON.stringify(payload), { idempotent: true, idempotencyKey, headers: { 'Content-Type': 'application/json' } }),
      getCurrentDecision: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/current-decision`),
      getSelectionAnswer: (sessionId) => request('GET', `${API_BASE}/selection-sessions/${sessionId}/answer`),
      retryExtraction: (candidateId, idempotencyKey) => request('POST', `${API_BASE}/candidates/${candidateId}/extraction-jobs`, null, { idempotent: true, idempotencyKey }),
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
