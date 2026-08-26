(function (global) {
  'use strict';
  const TERMINAL = new Set(['completed', 'failed', 'stale']);
  const activeByResource = new Map();
  function delayFor(config, elapsed, hidden) {
    if (hidden) return config.pollBackgroundMs;
    return elapsed < config.pollInitialWindowMs ? config.pollInitialMs : config.pollAfterInitialMs;
  }
  function start(options) {
    const { jobId, resourceId, versionId, fetchStatus, onUpdate, onTransportError, getCurrentVersion } = options;
    if (!jobId || !resourceId || !versionId) throw new Error('jobId, resourceId and versionId are required');
    activeByResource.get(resourceId)?.cancel();
    let cancelled = false;
    const startedAt = Date.now();
    const cancel = () => { cancelled = true; if (activeByResource.get(resourceId)?.jobId === jobId) activeByResource.delete(resourceId); };
    activeByResource.set(resourceId, { jobId, versionId, cancel });
    const isCurrent = () => !cancelled && activeByResource.get(resourceId)?.jobId === jobId && getCurrentVersion(resourceId) === versionId;
    const tick = () => {
      if (!isCurrent()) return cancel();
      fetchStatus(jobId).then((status) => {
        if (!isCurrent()) return cancel();
        onUpdate(status);
        if (TERMINAL.has(status.status)) return cancel();
        global.setTimeout(tick, delayFor(global.GuanchaPublicConfig.get(), Date.now() - startedAt, global.document.hidden));
      }).catch((error) => {
        // A browser transport problem is not a server Job transition.  Keep
        // polling the same version and let the caller show a retryable network
        // hint without overwriting queued/processing/completed server truth.
        if (!isCurrent()) return cancel();
        if (typeof onTransportError === 'function') onTransportError(error);
        global.setTimeout(tick, delayFor(global.GuanchaPublicConfig.get(), Date.now() - startedAt, global.document.hidden));
      });
    };
    tick();
    return cancel;
  }
  global.GuanchaJobPoller = { start, cancel: (resourceId) => activeByResource.get(resourceId)?.cancel(), activeCount: () => activeByResource.size };
}(window));
