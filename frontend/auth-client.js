(function (global) {
  'use strict';

  const ACCOUNT_MARKER_KEY = 'guancha.auth-user-id.v1';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const ALLOWED_REGIONS = new Set(['ap-shanghai', 'ap-guangzhou', 'ap-singapore']);
  const SAFE_ERROR_CODES = new Set([
    'invalid_credentials', 'verification_invalid', 'verification_expired',
    'verification_rate_limited', 'registration_conflict', 'captcha_required',
    'auth_provider_unavailable', 'session_expired', 'authentication_required',
    'auth_not_configured', 'network_unavailable', 'signup_challenge_missing',
    'verification_not_started', 'signout_failed',
  ]);
  const ERROR_MESSAGES = Object.freeze({
    invalid_credentials: '邮箱或密码不正确。',
    verification_invalid: '验证码无效，请重试。',
    verification_expired: '验证码已过期，请重新获取。',
    verification_rate_limited: '验证码请求过于频繁，请稍后重试。',
    registration_conflict: '该邮箱已注册，请直接登录。',
    captcha_required: '认证服务需要额外验证。',
    auth_provider_unavailable: '认证服务暂时不可用，请稍后重试。',
    session_expired: '登录状态已过期，请重新登录。',
    authentication_required: '请先登录后再继续。',
    auth_not_configured: '登录服务暂未配置。',
    network_unavailable: '网络暂时不可用，请稍后重试。',
    signup_challenge_missing: '注册验证暂时不可用，请稍后重试。',
    verification_not_started: '请先提交注册信息。',
    signout_failed: '退出登录暂时不可用，请重试。',
  });

  function error(code) {
    const safeCode = SAFE_ERROR_CODES.has(code) ? code : 'auth_provider_unavailable';
    const result = new Error(ERROR_MESSAGES[safeCode] || ERROR_MESSAGES.auth_provider_unavailable);
    result.name = 'GuanchaAuthError'; result.code = safeCode;
    return result;
  }

  function safeEmail(value) {
    return typeof value === 'string' && value.length <= 320 ? value : null;
  }

  function cleanConfig(value) {
    const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const envIdValue = input.envId ?? input.env_id;
    const envId = typeof envIdValue === 'string' && envIdValue.trim() ? envIdValue.trim() : null;
    const region = typeof input.region === 'string' ? input.region.trim().toLowerCase() : '';
    const required = input.required === true;
    const configured = input.configured === true && input.provider === 'cloudbase'
      && Boolean(envId) && ALLOWED_REGIONS.has(region);
    return { required, configured, envId, region };
  }

  function safeResponseCode(response) {
    const body = response && response.body;
    const candidate = body && body.error && body.error.code;
    return typeof candidate === 'string' && SAFE_ERROR_CODES.has(candidate) ? candidate : null;
  }

  function defaultTransport({ method, path, payload, headers }) {
    if (typeof global.fetch !== 'function') return Promise.reject(error('network_unavailable'));
    const requestHeaders = { Accept: 'application/json', ...headers };
    if (payload !== null && payload !== undefined && !requestHeaders['Content-Type']) requestHeaders['Content-Type'] = 'application/json';
    return global.fetch(path, {
      method,
      credentials: 'same-origin',
      headers: requestHeaders,
      body: payload === null || payload === undefined ? undefined : JSON.stringify(payload),
    }).then(async response => ({ ok: response.ok, status: response.status, body: await response.json().catch(() => null) }))
      .catch(() => ({ ok: false, status: 0, body: { error: { code: 'network_unavailable' } } }));
  }

  function createAuthClient(config, options) {
    const settings = cleanConfig(config);
    const transport = options && typeof options.transport === 'function' ? options.transport : defaultTransport;
    let state = { status: 'loading', email: null, errorCode: null };
    let accessToken = null;
    let expiresAt = 0;
    let verificationId = null;
    let verificationEmail = null;
    let refreshPromise = null;
    let destroyed = false;
    const listeners = new Set();

    function notify() {
      if (destroyed) return;
      listeners.forEach(listener => listener({ ...state }));
    }
    function setState(next) { state = next; notify(); return state; }
    function setError(code) { return setState({ status: 'error', email: null, errorCode: SAFE_ERROR_CODES.has(code) ? code : 'auth_provider_unavailable' }); }
    function clearSession() { accessToken = null; expiresAt = 0; state = { status: 'unauthenticated', email: null, errorCode: null }; }
    function unauthenticated() { return setState({ status: 'unauthenticated', email: null, errorCode: null }); }
    function tokenPayload(payload) {
      if (!payload || typeof payload !== 'object') throw error('auth_provider_unavailable');
      const token = typeof payload.access_token === 'string' ? payload.access_token : null;
      const expiresIn = Number(payload.expires_in);
      const sub = typeof payload.sub === 'string' && payload.sub.trim() ? payload.sub : null;
      if (!token || !sub || !Number.isFinite(expiresIn) || expiresIn <= 0) throw error('auth_provider_unavailable');
      return { token, expiresIn };
    }
    function applyToken(payload, fallbackEmail) {
      const parsed = tokenPayload(payload);
      accessToken = parsed.token;
      expiresAt = Date.now() + parsed.expiresIn * 1000;
      return setState({ status: 'authenticated', email: safeEmail(fallbackEmail) || state.email, errorCode: null });
    }
    async function request(path, payload, headers = {}) {
      let response;
      try { response = await transport({ method: 'POST', path, payload, headers }); }
      catch { throw error('network_unavailable'); }
      if (!response || response.ok === false) throw error(safeResponseCode(response) || (response && response.status === 0 ? 'network_unavailable' : 'auth_provider_unavailable'));
      return response.body;
    }
    async function restoreSession() {
      if (!settings.configured) {
        if (settings.required) throw error('auth_not_configured');
        unauthenticated();
        return null;
      }
      if (refreshPromise) return refreshPromise;
      refreshPromise = (async () => {
        try {
          const payload = await request('/api/v1/auth/refresh', null);
          return applyToken(payload, state.email);
        } catch (cause) {
          clearSession();
          if (cause && ['session_expired', 'authentication_required'].includes(cause.code)) unauthenticated();
          else setError(cause && cause.code);
          throw cause;
        } finally {
          refreshPromise = null;
        }
      })();
      return refreshPromise;
    }
    async function initialize() {
      if (settings.required && !settings.configured) { setError('auth_not_configured'); return getState(); }
      if (!settings.configured) { unauthenticated(); return getState(); }
      try { await restoreSession(); }
      catch (cause) {
        if (cause && ['session_expired', 'authentication_required'].includes(cause.code)) unauthenticated();
      }
      return getState();
    }
    async function signIn(email, password) {
      const payload = await request('/api/v1/auth/sign-in', { username: email, password });
      applyToken(payload, email);
      return getState();
    }
    async function startSignUp(email) {
      const payload = await request('/api/v1/auth/register/start', { email });
      const id = payload && payload.verification_id;
      if (typeof id !== 'string' || !id) throw error('signup_challenge_missing');
      verificationId = id;
      verificationEmail = email;
      return { status: 'verification_required', expiresIn: payload.expires_in };
    }
    async function verifySignUp(code, password) {
      if (!verificationId || !verificationEmail) throw error('verification_not_started');
      const email = verificationEmail;
      const payload = await request('/api/v1/auth/register/complete', {
        email,
        verification_id: verificationId,
        verification_code: code,
        password,
      });
      verificationId = null; verificationEmail = null;
      applyToken(payload, email);
      return getState();
    }
    async function getAccessToken() {
      if (accessToken && expiresAt > Date.now() + 30000) return accessToken;
      try {
        await restoreSession();
        return accessToken;
      } catch (cause) {
        if (!cause || !['session_expired', 'authentication_required'].includes(cause.code)) setError(cause && cause.code);
        throw error('authentication_required');
      }
    }
    async function signOut() {
      const token = accessToken;
      verificationId = null; verificationEmail = null;
      if (!token) { clearSession(); return unauthenticated(); }
      try {
        await request('/api/v1/auth/sign-out', null, { Authorization: `Bearer ${token}` });
      } catch (cause) {
        clearSession();
        setError(cause && cause.code === 'session_expired' ? 'session_expired' : 'signout_failed');
        throw cause;
      }
      clearSession();
      return unauthenticated();
    }
    function getState() { return { ...state }; }
    function subscribe(listener) {
      if (typeof listener !== 'function') return () => {};
      listeners.add(listener); listener(getState());
      return () => listeners.delete(listener);
    }
    function destroy() { destroyed = true; listeners.clear(); verificationId = null; verificationEmail = null; accessToken = null; expiresAt = 0; refreshPromise = null; }
    return Object.freeze({ initialize, getState, getAccessToken, signIn, startSignUp, verifySignUp, signOut, subscribe, destroy });
  }

  async function establishAccountBoundary({ userId, stores, storage = global.localStorage }) {
    if (!UUID.test(userId || '')) throw error('auth_provider_unavailable');
    const existing = storage && storage.getItem(ACCOUNT_MARKER_KEY);
    const changed = existing !== userId;
    if (changed && stores && typeof stores.clearAll === 'function') await stores.clearAll();
    if (storage) storage.setItem(ACCOUNT_MARKER_KEY, userId);
    return changed;
  }
  async function clearAccountBoundary({ stores, storage = global.localStorage }) {
    if (stores && typeof stores.clearAll === 'function') await stores.clearAll();
    if (storage) storage.removeItem(ACCOUNT_MARKER_KEY);
  }

  global.GuanchaAuth = { ACCOUNT_MARKER_KEY, createAuthClient, establishAccountBoundary, clearAccountBoundary };
}(window));
