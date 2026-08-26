(function (global) {
  'use strict';

  const CLIENT_EVENTS = new Set([
    'app_open', 'start_selection', 'onboarding_started', 'onboarding_completed',
    'onboarding_skipped', 'need_started', 'candidate_result_viewed',
    'merchant_question_viewed', 'merchant_question_copied', 'merchant_reply_started',
    'candidate_selected', 'tea_stock_added', 'flow_abandoned',
  ]);
  const METADATA_FIELDS = new Set([
    'candidate_count', 'image_count', 'has_budget', 'has_sensory_need',
    'question_field', 'question_count', 'action_bucket', 'processing_mode',
    'failure_category', 'onboarding_status', 'source', 'screen',
  ]);
  const SESSION_KEY = 'guancha.analytics-session.v1';
  const MAX_STRING = 64;
  const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const STRING_VALUES = Object.freeze({
    question_field: new Set(['roast_level','aroma_style','season','sample_available','return_policy','origin_text','tea_subtype','price','weight_grams','year_or_batch','process_text','unknown']),
    action_bucket: new Set(['currently-selectable','ask-before-buying','sample-first','not-recommended-now','insufficient-information']),
    processing_mode: new Set(['fake-provider','openai-vision','test-fixture','live-ai','cache-fallback']),
    onboarding_status: new Set(['not_started','completed','skipped']),
    source: new Set(['selection','settings','manual','copy_all']),
    screen: new Set(['home','candidates','o1','o2','analysis','result','rejudge','ownership','warehouse','warehouse-detail','warehouse-add','journal','journal-day','choose-tea','prepare','timer','infusion-done','feedback','advanced','brew-result','record-detail','settings','stub']),
    failure_category: new Set(['EXTRACTION_MISS','EXTRACTION_HALLUCINATION','EVIDENCE_SOURCE_ERROR','MARKETING_CLAIM_LEAK','SENSORY_OVERCLAIM','SENSORY_MISSING','NEED_PRIORITY_ERROR','BUDGET_PARSE_ERROR','DECISION_ANSWER_MISMATCH','QUESTION_DUPLICATE','QUESTION_LOW_VALUE','MERCHANT_REPLY_PARSE_ERROR','MERCHANT_CONFLICT_FALSE_POSITIVE','REJUDGE_INCONSISTENT','DECISION_STATE_STALE','STATE_RECOVERY_ERROR','COLD_START_ERROR','MOBILE_UI_BLOCKER','PROVIDER_ERROR','DATABASE_ERROR']),
  });
  const STAGES = new Set([...STRING_VALUES.screen, 'queued','claimed','provider','persisting','cleaning','completed','failed']);
  const ERROR_CATEGORIES = new Set(['validation_error','not_found','method_not_allowed','missing_client_id','invalid_client_id','missing_idempotency_key','invalid_idempotency_key','resource_not_owned','selection_session_not_found','candidate_not_found','candidate_image_not_found','candidate_limit_exceeded','candidate_image_limit_exceeded','invalid_image_type','image_too_large','unsafe_or_corrupt_image','image_too_low_resolution','image_pixel_limit_exceeded','idempotency_conflict','candidate_extraction_in_progress','candidate_extraction_not_retryable','ai_timeout','ai_provider_error','ai_schema_invalid','worker_interrupted','temporary_image_cleanup_failed','current_decision_not_available','decision_stale','questions_not_available','merchant_reply_not_found','question_not_available','decision_delta_not_found','brew_feedback_invalid','brew_session_not_found','tea_record_not_found','insufficient_feedback','feedback_analysis_failed','feedback_duplicate','contract_not_implemented','internal_error']);

  function uuid() {
    if (global.crypto && typeof global.crypto.randomUUID === 'function') return global.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    global.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function sessionId() {
    try {
      const existing = global.sessionStorage.getItem(SESSION_KEY);
      if (existing && UUID_PATTERN.test(existing)) return existing;
      const created = uuid(); global.sessionStorage.setItem(SESSION_KEY, created); return created;
    } catch { return uuid(); }
  }
  function safeMetadata(input) {
    if (!input || typeof input !== 'object' || Array.isArray(input)) return {};
    const result = {};
    for (const [key, value] of Object.entries(input)) {
      if (!METADATA_FIELDS.has(key)) continue;
      if (typeof value === 'boolean') result[key] = value;
      else if (typeof value === 'number' && Number.isFinite(value)) result[key] = Math.max(0, Math.min(10000, value));
      else if (typeof value === 'string' && value.length <= MAX_STRING && STRING_VALUES[key]?.has(value)) result[key] = value;
    }
    return result;
  }
  function create(options) {
    const endpoint = (options && options.endpoint) || '/api/v1/events';
    const transport = options && options.transport;
    let flowId = null;
    function startFlow() { flowId = uuid(); return flowId; }
    function endFlow() { flowId = null; }
    function track(eventName, fields) {
      if (!CLIENT_EVENTS.has(eventName)) return false;
      const values = fields || {};
      const event = {
        event_id: uuid(), event_name: eventName, anonymous_session_id: sessionId(),
        occurred_at: new Date().toISOString(), ...((UUID_PATTERN.test(values.flow_id || '') || UUID_PATTERN.test(flowId || '')) ? { flow_id: UUID_PATTERN.test(values.flow_id || '') ? values.flow_id : flowId } : {}),
        metadata: safeMetadata(values.metadata),
      };
      for (const key of ['candidate_id', 'decision_version_id', 'stage', 'duration_ms', 'error_category']) {
        const value = values[key];
        if (['candidate_id', 'decision_version_id'].includes(key) && typeof value === 'string' && UUID_PATTERN.test(value)) event[key] = value;
        else if (key === 'stage' && STAGES.has(value)) event[key] = value;
        else if (key === 'error_category' && ERROR_CATEGORIES.has(value)) event[key] = value;
        else if (key === 'duration_ms' && typeof value === 'number' && Number.isFinite(value)) event[key] = Math.max(0, Math.min(86400000, value));
      }
      try {
        const body = JSON.stringify(event);
        if (transport) Promise.resolve().then(() => transport(event)).catch(() => {});
        else if (global.fetch) global.fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
        return true;
      } catch { return false; }
    }
    return Object.freeze({ track, startFlow, endFlow, currentFlowId: () => flowId, safeMetadata });
  }

  global.GuanchaProductAnalytics = Object.freeze({ create, getSessionId: sessionId, CLIENT_EVENTS, METADATA_FIELDS });
}(window));
