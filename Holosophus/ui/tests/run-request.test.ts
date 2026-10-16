import assert from "node:assert/strict";
import test from "node:test";

import { buildManagerRequestPayload, uploadThenStart, prepareFeedbackAttachments } from "../src/runRequest.ts";
import type { FeedbackAttachments } from "../src/runRequest.ts";

test("manager payload carries the profile and uploaded relative paths", () => {
  assert.deepEqual(
    buildManagerRequestPayload({
      sessionId: "research__chemistry__opus__20260907120000",
      prompt: "Compare the attached compounds",
      computeMode: "Local",
      modelName: "sonnet",
      orchestrator: "claude_code",
      researchProfile: "chemistry",
      inputFiles: ["inputs/compounds.csv", "inputs/chart.png"],
      approvals: { sources: true, plan: false, auto_approve_after_seconds: 300 },
      resume: false
    }),
    {
      messages: [{ role: "user", content: "Compare the attached compounds" }],
      session_id: "research__chemistry__opus__20260907120000",
      compute_mode: "Local",
      model_name: "sonnet",
      orchestrator: "claude_code",
      research_profile: "chemistry",
      input_files: ["inputs/compounds.csv", "inputs/chart.png"],
      approvals: { sources: true, plan: false, auto_approve_after_seconds: 300 },
      resume: false,
      stream: true
    }
  );
});

test("feedback resumes the same session without replacing its saved launch configuration", () => {
  assert.deepEqual(
    buildManagerRequestPayload({
      sessionId: "session-1",
      prompt: "Expand the comparison and explain the outlier",
      orchestrator: "claude_code",
      approvals: { sources: true, plan: true, auto_approve_after_seconds: null },
      resume: true
    }),
    {
      messages: [{ role: "user", content: "Expand the comparison and explain the outlier" }],
      session_id: "session-1",
      orchestrator: "claude_code",
      approvals: { sources: true, plan: true, auto_approve_after_seconds: null },
      resume: true,
      stream: true
    }
  );
});

test("upload completes before manager starts", async () => {
  const calls: string[] = [];
  const selected = [new File(["CCO"], "compounds.csv", { type: "text/csv" })];

  await uploadThenStart(
    "session-1",
    selected,
    async () => {
      calls.push("upload");
      return { files: [{ name: "compounds.csv", path: "inputs/compounds.csv", size: 3 }] };
    },
    async (inputFiles) => {
      calls.push(`manager:${inputFiles.join(",")}`);
    }
  );

  assert.deepEqual(calls, ["upload", "manager:inputs/compounds.csv"]);
});

test("upload failure prevents manager start", async () => {
  let managerCalled = false;

  await assert.rejects(
    uploadThenStart(
      "session-1",
      [new File(["bad"], "bad.csv")],
      async () => {
        throw new Error("upload rejected");
      },
      async () => {
        managerCalled = true;
      }
    ),
    /upload rejected/
  );
  assert.equal(managerCalled, false);
});

test("feedback upload retries reuse identity and successful paths across login or start failures", async () => {
  const draft: FeedbackAttachments = { files: [new File(["updated"], "data.csv")], uploadId: "a".repeat(32) };
  const attempts: string[] = [];
  const upload = async (_sid: string, _files: File[], id: string) => {
    attempts.push(id);
    if (attempts.length === 1) throw new Error("response lost after server saved files");
    return { files: [{ name: "data.csv", path: "inputs/data--version.csv", size: 7 }] };
  };
  await assert.rejects(prepareFeedbackAttachments("session", draft, upload));
  const paths = await prepareFeedbackAttachments("session", draft, upload);
  assert.deepEqual(await prepareFeedbackAttachments("session", draft, upload), paths);
  assert.deepEqual(attempts, [draft.uploadId, draft.uploadId]);
  const payload = buildManagerRequestPayload({ sessionId: "session", prompt: "Use corrected data",
    orchestrator: "claude_code", approvals: { sources: true, plan: true, auto_approve_after_seconds: null },
    resume: true, inputFiles: paths, feedbackUploadId: draft.uploadId });
  assert.deepEqual(payload.input_files, paths);
  assert.equal(payload.feedback_upload_id, draft.uploadId);
  assert.equal(payload.resume, true);
});
