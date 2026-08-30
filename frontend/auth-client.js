(function (global) {
  'use strict';

  const ACCOUNT_MARKER_KEY = 'guancha.auth-user-id.v1';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const ALLOWED_REGIONS = new Set(['ap-shanghai', 'ap-guangzhou', 'ap-singapore']);

  function error(code, message) {
    const result = new Error(message);
    result.name = 'GuanchaAuthError'; result.code = code;
    return result;
  }
  function safeEmail(value) { return typeof value === 'string' && value.length <= 320 ? value : null; }
  function cleanConfig(value) {
    const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const envId = typeof input.envId === 'string' && input.envId.trim() ? input.envId.trim() : null;
    const region = typeof input.region === 'string' ? input.region.trim().toLowerCase() : '';
    const publishableKey = typeof input.publishableKey === 'string' && input.publishableKey.trim() ? input.publishableKey.trim() : null;
    const required = input.required === true;
    const configured = input.configured === true && input.provider === 'cloudbase' && Boolean(envId && publishableKey) && ALLOWED_REGIONS.has(region);
    return { required, configured, envId, region, publishableKey };
  }
  function sessionFrom(result) {
    const session = result && result.data && result.data.session;
    return session && typeof session === 'object' ? session : null;
  }
  function resultError(result) { return result && result.error ? result.error : null; }
  function tokenFrom(session) {
    const token = session && (session.access_token || session.accessToken);
    return typeof token === 'string' && token ? token : null;
  }
  function stateFor(session) {
    const email = safeEmail(session && session.user && session.user.email);
    return session && tokenFrom(session)
      ? { status: 'authenticated', email, errorCode: null }
      : { status: 'unauthenticated', email: null, errorCode: null };
  }
  function normalizeSdk(sdk) {
    if (!sdk || typeof sdk.init !== 'function') throw error('auth_sdk_unavailable', '登录服务暂不可用，请稍后重试。');
    return sdk;
  }
  function isAuthObject(value) {
    return Boolean(value) && typeof value === 'object' && ['getSession', 'signInWithPassword', 'signUp', 'signOut', 'onAuthStateChange']
      .every(method => typeof value[method] === 'function');
  }
  function authFromApp(app) {
    if (app && isAuthObject(app.auth)) return app.auth;
    if (app && typeof app.auth === 'function') {
      const compatibilityAuth = app.auth();
      if (isAuthObject(compatibilityAuth)) return compatibilityAuth;
    }
    throw error('auth_sdk_unavailable', '登录服务暂不可用，请稍后重试。');
  }

  function createAuthClient(config, options) {
    const settings = cleanConfig(config);
    const sdk = options && options.sdk || global.cloudbase;
    let auth = null;
    let verification = null;
    let state = { status: 'loading', email: null, errorCode: null };
    let subscription = null;
    const listeners = new Set();
    function notify() { listeners.forEach(listener => listener({ ...state })); }
    function setState(next) { state = next; notify(); return state; }
    function setError(code) { return setState({ status: 'error', email: null, errorCode: code }); }
    function requireAuth() {
      if (!auth) throw error('auth_not_initialized', '登录服务尚未准备好。');
      return auth;
    }
    function applySession(session) { return setState(stateFor(session)); }
    async function restoreSession() {
      const response = await requireAuth().getSession();
      if (resultError(response)) throw error('auth_restore_failed', '登录状态恢复失败，请重新登录。');
      return sessionFrom(response);
    }
    function subscribeLifecycle() {
      if (subscription || !auth || typeof auth.onAuthStateChange !== 'function') return;
      const result = auth.onAuthStateChange((event, session, info) => {
        if (info && info.error) { setError('auth_state_change_failed'); return; }
        if (event === 'TOKEN_REFRESHED') return;
        if (event === 'SIGNED_OUT') { applySession(null); return; }
        if (event === 'INITIAL_SESSION' || event === 'SIGNED_IN') applySession(session || null);
      });
      subscription = result && result.data && result.data.subscription || null;
    }
    async function initialize() {
      if (settings.required && !settings.configured) { setError('auth_not_configured'); return getState(); }
      if (!settings.configured) { applySession(null); return getState(); }
      try {
        const app = normalizeSdk(sdk).init({ env: settings.envId, region: settings.region, accessKey: settings.publishableKey });
        auth = authFromApp(app);
        subscribeLifecycle();
        applySession(await restoreSession());
      } catch (cause) {
        setError(cause && cause.code || 'auth_service_unavailable');
      }
      return getState();
    }
    async function signIn(email, password) {
      const response = await requireAuth().signInWithPassword({ email, password });
      if (resultError(response)) throw error('invalid_credentials', '邮箱或密码不正确。');
      const session = sessionFrom(response) || await restoreSession();
      if (!tokenFrom(session)) throw error('auth_session_missing', '登录状态不可用，请重新登录。');
      applySession(session);
      return getState();
    }
    async function startSignUp(email, password) {
      const response = await requireAuth().signUp({ email, password });
      if (resultError(response)) throw error('signup_unavailable', '注册暂时不可用，请稍后重试。');
      const verifyOtp = response && response.data && response.data.verifyOtp;
      if (typeof verifyOtp !== 'function') throw error('signup_challenge_missing', '注册验证暂时不可用，请稍后重试。');
      verification = verifyOtp;
      return { status: 'verification_required' };
    }
    async function verifySignUp(code) {
      if (typeof verification !== 'function') throw error('verification_not_started', '请先提交注册信息。');
      const response = await verification({ token: code });
      if (resultError(response)) throw error('verification_invalid', '验证码无效或已过期。');
      verification = null;
      const session = sessionFrom(response) || await restoreSession();
      if (!tokenFrom(session)) throw error('auth_session_missing', '验证完成后未能建立登录状态。');
      applySession(session);
      return getState();
    }
    async function getAccessToken() {
      if (!auth) return null;
      const session = await restoreSession();
      return tokenFrom(session);
    }
    async function signOut() {
      const response = await requireAuth().signOut();
      if (resultError(response)) throw error('signout_failed', '退出登录暂时不可用，请重试。');
      verification = null; applySession(null);
    }
    function getState() { return { ...state }; }
    function subscribe(listener) {
      if (typeof listener !== 'function') return () => {};
      listeners.add(listener); listener(getState());
      return () => listeners.delete(listener);
    }
    function destroy() {
      if (subscription && typeof subscription.unsubscribe === 'function') subscription.unsubscribe();
      subscription = null; listeners.clear(); verification = null; auth = null;
    }
    return Object.freeze({ initialize, getState, getAccessToken, signIn, startSignUp, verifySignUp, signOut, subscribe, destroy });
  }

  async function establishAccountBoundary({ userId, stores, storage = global.localStorage }) {
    if (!UUID.test(userId || '')) throw error('invalid_account_marker', '账号状态无效，请重新登录。');
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
