export interface ApprovalPolicy {
  sources: boolean;
  plan: boolean;
  auto_approve_after_seconds: number | null;
}

export interface ApprovalDraft {
  sources: boolean;
  plan: boolean;
  autoApprove: boolean;
  autoApproveSeconds: string;
}

export const DEFAULT_APPROVAL_DRAFT: ApprovalDraft = {
  sources: false,
  plan: false,
  autoApprove: false,
  autoApproveSeconds: "300"
};

export function isApprovalDraftValid(draft: ApprovalDraft): boolean {
  if (!draft.autoApprove) {
    return true;
  }
  const seconds = Number(draft.autoApproveSeconds);
  return (draft.sources || draft.plan) && Number.isInteger(seconds) && seconds > 0;
}

export function approvalPolicyFromDraft(draft: ApprovalDraft): ApprovalPolicy {
  const seconds = Number(draft.autoApproveSeconds);
  return {
    sources: draft.sources,
    plan: draft.plan,
    auto_approve_after_seconds:
      draft.autoApprove && (draft.sources || draft.plan) && Number.isInteger(seconds) && seconds > 0
        ? seconds
        : null
  };
}

export function approvalDraftFromPolicy(policy?: ApprovalPolicy | null): ApprovalDraft {
  if (!policy) {
    return { ...DEFAULT_APPROVAL_DRAFT };
  }
  const seconds = policy.auto_approve_after_seconds;
  return {
    sources: Boolean(policy.sources),
    plan: Boolean(policy.plan),
    autoApprove: typeof seconds === "number" && Number.isInteger(seconds) && seconds > 0,
    autoApproveSeconds:
      typeof seconds === "number" && Number.isInteger(seconds) && seconds > 0 ? String(seconds) : "300"
  };
}

export function secondsUntilApproval(deadlineMs: number | null, nowMs = Date.now()): number | null {
  if (deadlineMs === null) {
    return null;
  }
  return Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000));
}
