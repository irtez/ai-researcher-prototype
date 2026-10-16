import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_APPROVAL_DRAFT,
  approvalDraftFromPolicy,
  approvalPolicyFromDraft,
  isApprovalDraftValid,
  secondsUntilApproval
} from "../src/approvalPolicy.ts";

test("new runs start with both approval stages and automatic approval disabled", () => {
  assert.deepEqual(DEFAULT_APPROVAL_DRAFT, {
    sources: false,
    plan: false,
    autoApprove: false,
    autoApproveSeconds: "300"
  });
  assert.deepEqual(approvalPolicyFromDraft(DEFAULT_APPROVAL_DRAFT), {
    sources: false,
    plan: false,
    auto_approve_after_seconds: null
  });
});

test("automatic approval uses one positive integer timeout for the selected stages", () => {
  const draft = {
    sources: true,
    plan: false,
    autoApprove: true,
    autoApproveSeconds: "45"
  };

  assert.equal(isApprovalDraftValid(draft), true);
  assert.deepEqual(approvalPolicyFromDraft(draft), {
    sources: true,
    plan: false,
    auto_approve_after_seconds: 45
  });
});

test("automatic approval is invalid without a stage or a positive integer timeout", () => {
  assert.equal(
    isApprovalDraftValid({ sources: false, plan: false, autoApprove: true, autoApproveSeconds: "30" }),
    false
  );
  assert.equal(
    isApprovalDraftValid({ sources: true, plan: false, autoApprove: true, autoApproveSeconds: "0" }),
    false
  );
  assert.equal(
    isApprovalDraftValid({ sources: true, plan: true, autoApprove: true, autoApproveSeconds: "2.5" }),
    false
  );
});

test("saved policy restores editable controls and retains the seeded timeout when auto approval is off", () => {
  assert.deepEqual(
    approvalDraftFromPolicy({ sources: true, plan: true, auto_approve_after_seconds: 120 }),
    { sources: true, plan: true, autoApprove: true, autoApproveSeconds: "120" }
  );
  assert.deepEqual(
    approvalDraftFromPolicy({ sources: true, plan: false, auto_approve_after_seconds: null }),
    { sources: true, plan: false, autoApprove: false, autoApproveSeconds: "300" }
  );
});

test("countdown is derived from the server deadline and never becomes negative", () => {
  assert.equal(secondsUntilApproval(10_000, 7_100), 3);
  assert.equal(secondsUntilApproval(10_000, 10_001), 0);
  assert.equal(secondsUntilApproval(null, 7_100), null);
});
