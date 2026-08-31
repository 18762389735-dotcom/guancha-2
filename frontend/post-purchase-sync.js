(function (global) {
  'use strict';

  function clone(value) {
    return global.structuredClone ? global.structuredClone(value) : JSON.parse(JSON.stringify(value));
  }

  function syncError(code = 'post_purchase_sync_failed') {
    const error = new Error(code);
    error.code = code;
    return error;
  }

  function isUuid(value) {
    return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  }

  function asArray(value) {
    if (!Array.isArray(value)) throw syncError();
    return value;
  }

  function localDate(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
  }

  function normalizeWarehouseTea(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || !isUuid(value.id) || typeof value.name !== 'string') throw syncError();
    return {
      id: value.id,
      name: value.name,
      product_name: value.name,
      type: value.tea_category || value.tea_subtype || '不确定',
      tea_category: value.tea_category || null,
      tea_subtype: value.tea_subtype || null,
      origin: value.origin || null,
      roast_or_style: value.roast_or_style || null,
      aroma: value.aroma || '不确定',
      status: ['drinking', 'paused', 'finished'].includes(value.status) ? value.status : 'drinking',
      source_type: ['manual', 'selection'].includes(value.source_type) ? value.source_type : 'manual',
      source: value.source_type === 'selection' ? '本次购入' : '手动入库',
      selection_session_id: isUuid(value.selection_session_id) ? value.selection_session_id : null,
      candidate_id: isUuid(value.candidate_id) ? value.candidate_id : null,
      extraction_version_id: isUuid(value.extraction_version_id) ? value.extraction_version_id : null,
      decision_version_id: isUuid(value.decision_version_id) ? value.decision_version_id : null,
      facts: Array.isArray(value.facts) ? value.facts.slice(0, 8) : [],
      risks: Array.isArray(value.risks) ? value.risks.slice(0, 8) : [],
      risk_flags: Array.isArray(value.risk_flags) ? value.risk_flags.slice(0, 8) : [],
      joined_at: value.joined_at || null,
      revision: Number.isInteger(value.revision) && value.revision >= 1 ? value.revision : 1,
      created_at: value.created_at || null,
      updated_at: value.updated_at || null,
      lastBrew: '还未泡过',
      records: 0,
      art: 'can',
    };
  }

  function normalizeJournalEntry(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || !isUuid(value.id) || !isUuid(value.tea_id)) throw syncError();
    const date = localDate(value.brewed_on);
    if (!date) throw syncError();
    return {
      id: value.id,
      date,
      teaId: value.tea_id,
      infusions: Array.isArray(value.infusions) ? value.infusions.slice(0, 20) : [],
      plan: value.plan && typeof value.plan === 'object' && !Array.isArray(value.plan) ? clone(value.plan) : {},
      feedback: value.feedback && typeof value.feedback === 'object' && !Array.isArray(value.feedback) ? clone(value.feedback) : {},
      suggestion: typeof value.suggestion === 'string' ? value.suggestion : '',
      createdAt: value.created_at || '现在',
      created_at: value.created_at || null,
      updated_at: value.updated_at || null,
      revision: Number.isInteger(value.revision) && value.revision >= 1 ? value.revision : 1,
    };
  }

  function refreshTeaDerived(warehouse, journalRecords) {
    const byTea = new Map();
    journalRecords.forEach(record => {
      const rows = byTea.get(record.teaId) || [];
      rows.push(record);
      byTea.set(record.teaId, rows);
    });
    warehouse.forEach(tea => {
      const records = byTea.get(tea.id) || [];
      const latest = records.slice().sort((left, right) => String(right.date).localeCompare(String(left.date)))[0];
      tea.records = records.length;
      tea.lastBrew = latest ? latest.date : '还未泡过';
    });
    return warehouse;
  }

  function toWarehousePayload(tea) {
    if (!tea || typeof tea !== 'object' || !isUuid(tea.id)) throw syncError('warehouse_invalid');
    return {
      name: tea.name,
      tea_category: tea.tea_category || tea.type || null,
      tea_subtype: tea.tea_subtype || null,
      origin: tea.origin || null,
      roast_or_style: tea.roast_or_style || null,
      aroma: tea.aroma || null,
      status: tea.status || 'drinking',
      source_type: tea.source_type || (tea.source === '本次购入' ? 'selection' : 'manual'),
      selection_session_id: tea.selection_session_id || null,
      candidate_id: tea.candidate_id || null,
      extraction_version_id: tea.extraction_version_id || null,
      decision_version_id: tea.decision_version_id || tea.sourceDecisionId || null,
      facts: Array.isArray(tea.facts) ? tea.facts.slice(0, 8) : [],
      risks: Array.isArray(tea.risks) ? tea.risks.slice(0, 8) : [],
      risk_flags: Array.isArray(tea.risk_flags) ? tea.risk_flags.slice(0, 8) : [],
    };
  }

  const JOURNAL_ADVANCED_KEYS = Object.freeze(['回甘', '生津', '余韵']);

  function toJournalString(value) {
    return typeof value === 'string' && value.length > 0 ? value : null;
  }

  function toJournalStringList(value, limit) {
    if (!Array.isArray(value)) return [];
    return value.filter(item => typeof item === 'string').slice(0, limit);
  }

  function toJournalInfusion(infusion) {
    const value = infusion && typeof infusion === 'object' && !Array.isArray(infusion) ? infusion : {};
    return {
      number: value.number,
      suggested: value.suggested,
      actual: value.actual,
    };
  }

  function toJournalFeedback(value) {
    const feedback = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const advancedSource = feedback.advanced && typeof feedback.advanced === 'object' && !Array.isArray(feedback.advanced)
      ? feedback.advanced
      : {};
    const advanced = {};
    JOURNAL_ADVANCED_KEYS.forEach(key => {
      if (typeof advancedSource[key] === 'string') advanced[key] = advancedSource[key];
    });
    const score = Number.isInteger(feedback.score) && feedback.score >= 1 && feedback.score <= 5
      ? feedback.score
      : null;
    return {
      taste: toJournalString(feedback.taste),
      strength: toJournalString(feedback.strength),
      tags: toJournalStringList(feedback.tags, 3),
      aroma: toJournalStringList(feedback.aroma, 3),
      impression: toJournalString(feedback.impression),
      score,
      repurchase: toJournalString(feedback.repurchase),
      advanced,
    };
  }

  function toJournalPayload(record) {
    if (!record || typeof record !== 'object' || !isUuid(record.id) || !isUuid(record.teaId)) throw syncError('journal_invalid');
    const plan = record.plan && typeof record.plan === 'object' && !Array.isArray(record.plan) ? record.plan : {};
    return {
      tea_id: record.teaId,
      brewed_on: record.date,
      infusions: Array.isArray(record.infusions) ? record.infusions.slice(0, 20).map(toJournalInfusion) : [],
      plan: {
        ware: plan.ware || null,
        water: plan.water || null,
        grams: plan.grams || null,
        temp: plan.temp || null,
      },
      feedback: toJournalFeedback(record.feedback),
      suggestion: record.suggestion || null,
    };
  }

  function applyCollections({ state, warehouse, journalRecords, saveLocal }) {
    state.warehouse = refreshTeaDerived(warehouse.map(normalizeWarehouseTea), journalRecords.map(normalizeJournalEntry));
    state.journalRecords = journalRecords.map(normalizeJournalEntry);
    if (state.selectedTeaId && !state.warehouse.some(tea => tea.id === state.selectedTeaId)) state.selectedTeaId = null;
    if (typeof saveLocal === 'function') saveLocal();
    return { warehouse: state.warehouse, journalRecords: state.journalRecords };
  }

  async function hydrate({ api, state, saveLocal, onError }) {
    try {
      const [warehouse, journal] = await Promise.all([api.getMyWarehouse(), api.getMyBrewJournal()]);
      if (!Array.isArray(warehouse) || !Array.isArray(journal)) throw syncError();
      applyCollections({ state, warehouse, journalRecords: journal, saveLocal });
      return { warehouseLoaded: true, journalLoaded: true };
    } catch (error) {
      if (typeof onError === 'function') onError(error.code || 'post_purchase_sync_failed');
      return { warehouseLoaded: false, journalLoaded: false, errorCode: error.code || 'post_purchase_sync_failed' };
    }
  }

  async function refreshAfterConflict({ api, state, saveLocal, onError }) {
    try {
      const [warehouse, journal] = await Promise.all([api.getMyWarehouse(), api.getMyBrewJournal()]);
      if (!Array.isArray(warehouse) || !Array.isArray(journal)) throw syncError();
      applyCollections({ state, warehouse, journalRecords: journal, saveLocal });
      return true;
    } catch (error) {
      if (typeof onError === 'function') onError(error.code || 'post_purchase_sync_failed');
      return false;
    }
  }

  async function persistWarehouseTea({ api, state, tea, expectedRevision, saveLocal, notify, onConflict, onError }) {
    try {
      const response = await api.putMyWarehouseTea(tea.id, toWarehousePayload(tea), expectedRevision);
      const canonical = normalizeWarehouseTea(response);
      const index = state.warehouse.findIndex(item => item.id === canonical.id);
      if (index >= 0) state.warehouse.splice(index, 1, canonical); else state.warehouse.unshift(canonical);
      refreshTeaDerived(state.warehouse, state.journalRecords);
      if (typeof saveLocal === 'function') saveLocal();
      if (typeof notify === 'function') notify('茶仓已同步');
      return canonical;
    } catch (error) {
      if (error && error.code === 'warehouse_revision_conflict') {
        await refreshAfterConflict({ api, state, saveLocal, onError });
        if (typeof onConflict === 'function') onConflict();
        return null;
      }
      if (typeof notify === 'function') notify('茶仓同步失败，已保留当前设置');
      if (typeof onError === 'function') onError(error.code || 'post_purchase_sync_failed');
      return null;
    }
  }

  async function persistJournalEntry({ api, state, record, expectedRevision, saveLocal, notify, onConflict, onError }) {
    try {
      const response = await api.putMyBrewJournalEntry(record.id, toJournalPayload(record), expectedRevision);
      const canonical = normalizeJournalEntry(response);
      const index = state.journalRecords.findIndex(item => item.id === canonical.id);
      if (index >= 0) state.journalRecords.splice(index, 1, canonical); else state.journalRecords.unshift(canonical);
      refreshTeaDerived(state.warehouse, state.journalRecords);
      if (typeof saveLocal === 'function') saveLocal();
      if (typeof notify === 'function') notify('泡茶记录已同步');
      return canonical;
    } catch (error) {
      if (error && error.code === 'brew_journal_revision_conflict') {
        await refreshAfterConflict({ api, state, saveLocal, onError });
        if (typeof onConflict === 'function') onConflict();
        return null;
      }
      if (typeof notify === 'function') notify('泡茶记录同步失败，已保留当前设置');
      if (typeof onError === 'function') onError(error.code || 'post_purchase_sync_failed');
      return null;
    }
  }

  global.GuanchaPostPurchaseSync = Object.freeze({
    normalizeWarehouseTea,
    normalizeJournalEntry,
    toWarehousePayload,
    toJournalPayload,
    applyCollections,
    hydrate,
    persistWarehouseTea,
    persistJournalEntry,
  });
}(window));
