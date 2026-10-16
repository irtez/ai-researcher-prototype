import assert from "node:assert/strict";
import test from "node:test";

import { buildAnswerPayload } from "../src/agentContracts.ts";

test("approval actions are bound to the displayed question id", () => {
  assert.deepEqual(buildAnswerPayload("session-1", "q-sources-7", "approve", ""), {
    session_id: "session-1",
    qid: "q-sources-7",
    action: "approve"
  });
  assert.deepEqual(buildAnswerPayload("session-1", "q-plan-8", "revise", "Add citations"), {
    session_id: "session-1",
    qid: "q-plan-8",
    action: "revise",
    text: "Add citations"
  });
});

test("ordinary answers keep their action and trimmed text", () => {
  assert.deepEqual(buildAnswerPayload("session-1", "q-9", "answer", "  Use dataset B  "), {
    session_id: "session-1",
    qid: "q-9",
    action: "answer",
    text: "Use dataset B"
  });
});
