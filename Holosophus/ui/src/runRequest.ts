import type { ComputeMode, Orchestrator } from "./live";
import type { ResearchProfile } from "./launchConfig";
import type { ApprovalPolicy } from "./approvalPolicy";

export interface UploadedInput {
  name: string;
  path: string;
  size: number;
}

export interface UploadInputsResponse {
  files: UploadedInput[];
}

interface BaseManagerPayloadOptions {
  sessionId: string;
  prompt: string;
  orchestrator: Orchestrator;
  approvals: ApprovalPolicy;
}

interface NewManagerPayloadOptions extends BaseManagerPayloadOptions {
  resume: false;
  computeMode: ComputeMode;
  modelName: string;
  effort?: string;
  researchProfile: ResearchProfile;
  inputFiles: string[];
}

interface ResumeManagerPayloadOptions extends BaseManagerPayloadOptions {
  resume: true;
  inputFiles?: string[];
  feedbackUploadId?: string;
}

export type ManagerPayloadOptions = NewManagerPayloadOptions | ResumeManagerPayloadOptions;

export function buildManagerRequestPayload(options: ManagerPayloadOptions) {
  if (options.resume) {
    return {
      messages: [{ role: "user", content: options.prompt }],
      session_id: options.sessionId,
      orchestrator: options.orchestrator,
      approvals: options.approvals,
      ...(options.inputFiles?.length ? { input_files: options.inputFiles, feedback_upload_id: options.feedbackUploadId } : {}),
      resume: true,
      stream: true
    };
  }
  return {
    messages: [{ role: "user", content: options.prompt }],
    session_id: options.sessionId,
    compute_mode: options.computeMode,
    ...(options.orchestrator === "codearkt" ? { model_configuration: true } : {}),
    ...(options.modelName ? { model_name: options.modelName } : {}),
    ...(options.effort ? { effort: options.effort } : {}),
    orchestrator: options.orchestrator,
    research_profile: options.researchProfile,
    ...(options.inputFiles.length ? { input_files: options.inputFiles } : {}),
    approvals: options.approvals,
    resume: false,
    stream: true
  };
}

export interface FeedbackAttachments {
  files: File[];
  uploadId?: string;
  inputFiles?: string[];
}

// Keep the same object through auth/retry. A lost upload response can be retried
// with its stable ID; a successful upload never needs to be sent again.
export async function prepareFeedbackAttachments(
  sessionId: string,
  draft: FeedbackAttachments,
  upload: (sessionId: string, files: File[], uploadId: string) => Promise<UploadInputsResponse>
): Promise<string[]> {
  if (draft.inputFiles) return draft.inputFiles;
  if (!draft.files.length) return [];
  if (!draft.uploadId) throw new Error("Missing feedback upload ID");
  draft.inputFiles = (await upload(sessionId, draft.files, draft.uploadId)).files.map(file => file.path);
  return draft.inputFiles;
}

export async function uploadThenStart(
  sessionId: string,
  files: File[],
  upload: (sessionId: string, files: File[]) => Promise<UploadInputsResponse>,
  start: (inputFiles: string[]) => Promise<void>
): Promise<void> {
  const inputFiles = files.length
    ? (await upload(sessionId, files)).files.map((file) => file.path)
    : [];
  await start(inputFiles);
}
