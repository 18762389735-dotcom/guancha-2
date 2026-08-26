(function (global) {
  'use strict';

  const STATUS_KEY = 'guancha_onboarding_status';
  const VALID_STATUSES = new Set(['not_started', 'completed', 'skipped']);

  function hasPreferenceChoices(value) {
    if (!value || typeof value !== 'object') return false;
    const o1 = value.o1;
    const o2 = value.o2;
    return Boolean(
      o1 && typeof o1 === 'object' && Object.values(o1).some((items) => Array.isArray(items) && items.length > 0)
      || o2 && Array.isArray(o2.flavors) && o2.flavors.length > 0
    );
  }

  function resolveStatus(storage, savedPreferences) {
    const stored = storage.getItem(STATUS_KEY);
    if (VALID_STATUSES.has(stored)) return stored;
    const migrated = savedPreferences?.preferencesSkipped || savedPreferences?.onboardingSkipped
      ? 'skipped'
      : hasPreferenceChoices(savedPreferences) ? 'completed' : 'not_started';
    if (migrated !== 'not_started') storage.setItem(STATUS_KEY, migrated);
    return migrated;
  }

  function markStatus(storage, status) {
    if (!VALID_STATUSES.has(status) || status === 'not_started') return false;
    storage.setItem(STATUS_KEY, status);
    return true;
  }

  function isReload(performanceObject) {
    const navigation = performanceObject?.getEntriesByType?.('navigation')?.[0];
    return navigation?.type === 'reload';
  }

  function hasValidActiveFlow(state) {
    if (!state || state.activeSelectionFlow === false) return false;
    const candidates = Array.isArray(state.candidates) ? state.candidates : [];
    const hasCandidateContext = candidates.some((candidate) => candidate && (
      candidate.serverCandidateId || candidate.jobId || candidate.extractionVersionId
      || (Array.isArray(candidate.images) && candidate.images.length > 0)
    ));
    return Boolean(hasCandidateContext || state.decisionVersionId || state.decisionJobId || state.rejudgeJobId);
  }

  function initialScreen({ reload, state }) {
    return reload && hasValidActiveFlow(state) ? state.screen : 'home';
  }

  global.GuanchaOnboarding = {
    STATUS_KEY,
    resolveStatus,
    markStatus,
    isReload,
    hasValidActiveFlow,
    initialScreen,
  };
}(window));
