(function (global) {
  'use strict';

  const SAFE_MIME_TYPES = new Set(['image/jpeg', 'image/png']);
  const DEFAULT = Object.freeze({
    maxCandidates: 5,
    maxImagesPerCandidate: 2,
    allowedImageMimeTypes: Object.freeze(['image/jpeg', 'image/png']),
    maxImageBytes: 5 * 1024 * 1024,
    pollInitialMs: 1000,
    pollAfterInitialMs: 2000,
    pollBackgroundMs: 5000,
    pollInitialWindowMs: 5000,
    auth: Object.freeze({ required: false, configured: false, provider: 'cloudbase', envId: null, region: 'ap-shanghai', publishableKey: null }),
  });
  let current = { ...DEFAULT, allowedImageMimeTypes: [...DEFAULT.allowedImageMimeTypes], auth: { ...DEFAULT.auth } };
  function integerInRange(value, minimum, maximum, fallback) {
    return Number.isInteger(value) && value >= minimum && value <= maximum ? value : fallback;
  }
  function secondsToMs(value, fallback) {
    return Number.isFinite(value) && value > 0 && value <= 60 ? Math.round(value * 1000) : fallback;
  }
  function allowedMimeTypes(value) {
    if (!Array.isArray(value) || !value.length || value.length > SAFE_MIME_TYPES.size) return [...DEFAULT.allowedImageMimeTypes];
    const types = [...new Set(value)];
    return types.every(type => typeof type === 'string' && SAFE_MIME_TYPES.has(type)) ? types : [...DEFAULT.allowedImageMimeTypes];
  }
  function authConfig(value) {
    const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const region = typeof input.region === 'string' ? input.region.trim().toLowerCase() : '';
    const envId = typeof input.env_id === 'string' && input.env_id.trim() ? input.env_id.trim() : null;
    const publishableKey = typeof input.publishable_key === 'string' && input.publishable_key.trim() ? input.publishable_key.trim() : null;
    const required = input.required === true;
    const validRegion = /^[a-z0-9]+(?:-[a-z0-9]+){1,4}$/.test(region);
    const validProvider = input.provider === 'cloudbase';
    return {
      required,
      configured: input.configured === true && validProvider && validRegion && Boolean(envId && publishableKey),
      provider: 'cloudbase',
      envId,
      region: validRegion ? region : DEFAULT.auth.region,
      publishableKey,
    };
  }
  function copyCurrent() { return { ...current, allowedImageMimeTypes: [...current.allowedImageMimeTypes], auth: { ...current.auth } }; }
  function apply(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return copyCurrent();
    const intervals = payload.poll_intervals_seconds;
    current = {
      // The product ceilings are the live UI bounds.  Historical Phase-2
      // rollout flags must not silently collapse the selection UI to 1/1.
      maxCandidates: integerInRange(payload.candidate_limit, 1, 5, DEFAULT.maxCandidates),
      maxImagesPerCandidate: integerInRange(payload.candidate_image_limit, 1, 2, DEFAULT.maxImagesPerCandidate),
      allowedImageMimeTypes: allowedMimeTypes(payload.allowed_image_mime_types),
      maxImageBytes: integerInRange(payload.max_image_bytes, 1, 5 * 1024 * 1024, DEFAULT.maxImageBytes),
      pollInitialMs: secondsToMs(intervals && intervals.initial, DEFAULT.pollInitialMs),
      pollAfterInitialMs: secondsToMs(intervals && intervals.after_initial, DEFAULT.pollAfterInitialMs),
      pollBackgroundMs: secondsToMs(intervals && intervals.background, DEFAULT.pollBackgroundMs),
      pollInitialWindowMs: DEFAULT.pollInitialWindowMs,
      auth: authConfig(payload.auth),
    };
    return copyCurrent();
  }
  global.GuanchaPublicConfig = { DEFAULT, get: copyCurrent, apply };
}(window));
