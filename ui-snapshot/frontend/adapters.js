(function (global) {
  'use strict';
  const actionLabels = {
    'currently-selectable': '当前可选',
    'ask-before-buying': '先问清再买',
    'sample-first': '建议先试小样',
    'not-recommended-now': '暂不建议',
    'insufficient-information': '信息不足，无法判断',
  };
  const clientRiskFlags = new Set(['season_claim_conflict','origin_claim_conflict','price_claim_conflict','价格或规格不支持可接受试错','不能从香型推导焙火程度','本次需求不应被长期偏好替代','营销词与可信度不存在等价关系','信息充分度不等同于商品真实性','冲突不能被正向信息抵消','试饮前仍需保留体验不确定性','未知价格不能视为符合预算']);
  function safeExtractionRiskFlags(extraction = {}) {
    return (Array.isArray(extraction.evidence_items) ? extraction.evidence_items : [])
      .filter(item => item?.field_name === 'risk_flag' && clientRiskFlags.has(item.normalized_value))
      .map(item => item.normalized_value).slice(0, 3);
  }
  function jobToCandidateStatus(job) {
    const status = job && job.status;
    return ['queued', 'processing', 'completed', 'failed', 'stale'].includes(status) ? status : 'empty';
  }
  function candidateToViewModel(candidate) {
    return {
      id: candidate.id,
      label: candidate.letter,
      displayName: candidate.name || '待提取商品信息',
      images: (candidate.images || []).map((image) => ({ id: image.id, status: image.status, errorCode: image.errorCode })),
      extractionVersionId: candidate.extractionVersionId || null,
      extractionStatus: candidate.extractionStatus || 'empty',
    };
  }
  function sensoryNeedMatch(decision = {}) {
    const components = decision.score_components || {};
    return Number(components.explicit_sensory_need_match || 0) + Number(components.need_match || 0);
  }
  function invalidateDecisionState(state = {}) {
    return {
      decisionVersionId: null,
      decisionJobId: null,
      decisionStatus: 'not_requested',
      selectionAnswer: null,
      followupQuestions: [],
      questionStatus: 'idle',
      questionDecisionVersionId: null,
      merchantReplyIds: {},
      merchantReplies: {},
      rejudgeJobId: null,
      lastDecisionDelta: null,
      deltaStatus: 'idle',
      candidates: (state.candidates || []).map((candidate) => ({ ...candidate, decision: null, riskFlags: [] })),
    };
  }
  async function prepareNeedUpdate({ state = {}, nextNeed = {}, isApiConfigured = false, updateRemote }) {
    if (state.sessionId) {
      if (!isApiConfigured) {
        const error = new Error('API is not configured for the existing selection session');
        error.code = 'api_not_configured';
        throw error;
      }
      await updateRemote();
    }
    return { ...invalidateDecisionState(state), need: nextNeed };
  }
  function activeRecoveryScreen(snapshot = {}) {
    if (snapshot.decision_delta) return 'rejudge';
    if (['queued', 'processing'].includes(snapshot.rejudge_job?.status)) return 'rejudge';
    const decisionJobStatus = snapshot.session_decision_job?.status;
    if (['queued', 'processing'].includes(decisionJobStatus)) return 'analysis';
    if (decisionJobStatus === 'completed' && snapshot.current_decision_id) return 'result';
    if (['failed', 'stale'].includes(decisionJobStatus)) return 'candidates';
    const hasActiveAnalysis = (snapshot.candidates || []).some((candidate) => {
      if (['queued', 'processing'].includes(candidate.current_extraction?.status)) return true;
      return (candidate.images || []).some((image) => ['queued', 'processing'].includes(image.current_job_status || image.status));
    });
    if (hasActiveAnalysis) return 'analysis';
    return snapshot.current_decision_id ? 'result' : 'candidates';
  }
  function candidateIdentity(candidate) { return candidate?.serverCandidateId || candidate?.id || null; }
  function resolveActiveCandidateIndex(candidates = [], activeCandidateId = null, fallbackIndex = 0) {
    const exact = candidates.findIndex(candidate => candidateIdentity(candidate) === activeCandidateId);
    if (exact >= 0) return exact;
    return Math.max(0, Math.min(Number(fallbackIndex) || 0, Math.max(0, candidates.length - 1)));
  }
  function buildSelectionHistoryIdentity({ candidates = [], selectedCandidate = null } = {}) {
    const recommended = candidates.find(item => Number(item.decision?.overall_order) === 1) || null;
    return {
      recommended_candidate_id: candidateIdentity(recommended),
      recommended_candidate_label: /^[A-E]$/.test(recommended?.letter) ? recommended.letter : null,
      selected_candidate_id: candidateIdentity(selectedCandidate),
      selected_candidate_label: /^[A-E]$/.test(selectedCandidate?.letter) ? selectedCandidate.letter : null,
    };
  }
  function buildPreferenceReference({ o1 = {}, o2 = {}, onboardingStatus = 'completed' } = {}) {
    if (onboardingStatus === 'skipped') return [];
    const references = [];
    const selectedDrinks = Object.values(o1).flat().filter(Boolean).slice(0, 2);
    if (selectedDrinks.length) {
      references.push({
        source: 'o1', source_value: selectedDrinks.join('、'),
        text: `你在偏好设置中选过${selectedDrinks.join('、')}。`,
      });
    }
    const flavors = Array.isArray(o2.flavors) ? o2.flavors.filter(Boolean).slice(0, 2) : [];
    if (flavors.length) {
      references.push({
        source: 'o2', source_value: flavors.join('、'),
        text: `你关注的风味里有${flavors.join('、')}。`,
      });
    }
    return references.slice(0, 2);
  }
  function buildPersonalFitPresentation({ need = {}, sensoryInterpretations = [], preferenceReference = [] } = {}) {
    const explicitNeed = [need.taste, need.purpose].filter(Boolean).join('、');
    const lines = [];
    if (explicitNeed) lines.push(`这次你明确想找${explicitNeed}，本次判断会优先按这个方向。`);
    if (sensoryInterpretations.length) {
      const sensoryText = sensoryInterpretations.map((item) => item.text || '').join(' ');
      const seeksFresh = /清爽|清鲜|花香|火味不要/.test(String(need.taste || ''));
      if (seeksFresh && /清鲜、轻扬|火味存在感通常较低/.test(sensoryText)) {
        lines.push('这款目前的清香或低火味线索，更接近你这次想找的清爽花香方向。');
      } else if (seeksFresh && /熟香、醇厚方向|焙火存在感通常会更明显/.test(sensoryText)) {
        lines.push('这款目前更偏熟香或焙火方向，和你这次想找的清爽花香相比，可能更偏另一种风格。');
      } else {
        lines.push('这款目前能确认的风格线索，会作为判断它是否接近你这次需求的依据。');
      }
    } else {
      lines.push('目前能确认的信息还不足以判断它是否符合你这次的口味方向。');
    }
    if (preferenceReference.length) lines.push(`${preferenceReference.map((item) => item.text).join('')}这只作为低置信口味参考，不会覆盖你这次的需求。`);
    return { lines, preferenceReference };
  }
  global.GuanchaAdapters = { actionLabels, safeExtractionRiskFlags, jobToCandidateStatus, candidateToViewModel, sensoryNeedMatch, invalidateDecisionState, prepareNeedUpdate, activeRecoveryScreen, candidateIdentity, resolveActiveCandidateIndex, buildSelectionHistoryIdentity, buildPreferenceReference, buildPersonalFitPresentation };
}(window));
