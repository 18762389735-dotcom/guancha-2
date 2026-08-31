(function (global) {
  'use strict';

  const emptyProfile = () => ({
    o1: { tea: [], coffee: [], milk: [], juice: [] },
    o2: { sweetness: 50, flavors: [] },
  });

  function clone(value) {
    return global.structuredClone ? global.structuredClone(value) : JSON.parse(JSON.stringify(value));
  }

  function syncError(code = 'preference_sync_failed') {
    const error = new Error(code);
    error.code = code;
    return error;
  }

  function normalizedResponse(response) {
    if (!response || typeof response !== 'object' || Array.isArray(response)) throw syncError();
    const profile = response.profile;
    if (!profile || typeof profile !== 'object' || Array.isArray(profile)) throw syncError();
    const o1 = profile.o1;
    const o2 = profile.o2;
    if (!o1 || typeof o1 !== 'object' || Array.isArray(o1) || !o2 || typeof o2 !== 'object' || Array.isArray(o2)) throw syncError();
    const revision = Number(response.revision);
    if (!Number.isInteger(revision) || revision < 0) throw syncError();
    if (!Number.isInteger(o2.sweetness) || o2.sweetness < 0 || o2.sweetness > 100 || !Array.isArray(o2.flavors)) throw syncError();
    if (!['tea', 'coffee', 'milk', 'juice'].every(key => Array.isArray(o1[key]))) throw syncError();
    return { profile: clone({ o1, o2 }), revision };
  }

  function applyServerPreferences({ state, response, saveLocal }) {
    const normalized = normalizedResponse(response);
    state.o1 = normalized.profile.o1;
    state.o2 = normalized.profile.o2;
    if (typeof saveLocal === 'function') saveLocal();
    return normalized.revision;
  }

  async function hydrate({ api, state, saveLocal, saveEvidence, onError }) {
    let preferenceLoaded = false;
    let evidenceLoaded = false;
    try {
      const response = await api.getMyPreferences();
      state.preferenceRevision = applyServerPreferences({ state, response, saveLocal });
      preferenceLoaded = true;
    } catch (error) {
      if (typeof onError === 'function') onError(error.code || 'preference_sync_failed');
    }
    try {
      const evidence = await api.getMyPreferenceEvidence();
      if (!Array.isArray(evidence)) throw syncError();
      if (typeof saveEvidence === 'function') saveEvidence(evidence);
      evidenceLoaded = true;
    } catch (error) {
      if (typeof onError === 'function') onError(error.code || 'preference_sync_failed');
    }
    return { preferenceLoaded, evidenceLoaded };
  }

  async function persistProfile({ api, state, saveLocal, notify, onConflict }) {
    const expectedRevision = Number.isInteger(state.preferenceRevision) ? state.preferenceRevision : 0;
    try {
      const response = await api.putMyPreferences({ o1: state.o1, o2: state.o2 }, expectedRevision);
      state.preferenceRevision = applyServerPreferences({ state, response, saveLocal });
      if (typeof notify === 'function') notify('偏好已同步');
      return true;
    } catch (error) {
      if (error && error.code === 'preferences_revision_conflict') {
        try {
          const latest = await api.getMyPreferences();
          state.preferenceRevision = applyServerPreferences({ state, response: latest, saveLocal });
          if (typeof onConflict === 'function') onConflict();
        } catch (refreshError) {
          if (typeof notify === 'function') notify('偏好同步失败，已保留当前设置');
          return false;
        }
        return false;
      }
      if (typeof notify === 'function') notify('偏好同步失败，已保留当前设置');
      return false;
    }
  }

  async function persistEvidence({ api, items, saveEvidence, notify }) {
    try {
      const response = await api.persistMyPreferenceEvidence(items);
      if (!Array.isArray(response)) throw syncError();
      if (typeof saveEvidence === 'function') saveEvidence(response);
      return true;
    } catch (error) {
      if (typeof notify === 'function') notify('偏好证据暂未同步，已保留本地设置');
      return false;
    }
  }

  global.GuanchaPreferenceSync = Object.freeze({
    emptyProfile,
    normalizedResponse,
    applyServerPreferences,
    hydrate,
    persistProfile,
    persistEvidence,
  });
}(window));
