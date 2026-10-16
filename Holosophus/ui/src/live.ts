export * from "./liveState";
import type { AgentEvent } from "./liveState";
import { getClientId } from "./api/clientId";
import { authHeaders } from "./api/auth";
import type { ResearchProfile } from "./launchConfig";
import { buildManagerRequestPayload } from "./runRequest";
import type { ManagerPayloadOptions } from "./runRequest";
import type { ApprovalPolicy } from "./approvalPolicy";
import { buildAnswerPayload } from "./agentContracts";
import type { AnswerAction } from "./agentContracts";

// Thrown by streamManagerRun so the caller can distinguish a 401 (login
// required/expired -> re-prompt) from a generic run failure.
export class AgentRunError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export type ComputeMode = "Auto" | "Local" | "GPU";

// SC1 (ui-claude-code-orchestrator): which backend a run is addressed to.
// Default "codearkt" keeps the existing path unchanged; the value is sent both
// in the POST body (the contract) and in the X-Holo-Orchestrator header (the
// nginx routing key) so a run and its cancel reach the same backend.
export type Orchestrator = "codearkt" | "claude_code";

// Operator-control (docs/plans/2026-08-13-ui-operator-control-cc.md), claude_code only.
export type SteerMode = "now" | "next";
export type ControlVerb = "pause" | "resume" | "stop" | "steer" | "inject";

export interface PendingQuestion {
  qid: string;
  question: string;
  context: string;
  stage: "sources" | "plan" | null;
  document_path?: string;
  document_digest?: string;
  asked_at: number;
  auto_approve_at: number | null;
}

export interface SessionDecision {
  status: string;
  stage?: "sources" | "plan" | null;
  document_path?: string;
  text?: string;
  qid?: string;
}

export interface AgentSessionMetadata {
  session_id: string;
  research_profile: ResearchProfile;
  model_name: string;
  model_label?: string;
  effort?: string | null;
  compute_mode: ComputeMode;
  approvals: ApprovalPolicy;
  active: boolean;
  can_resume: boolean;
  status: string;
  last_decision?: SessionDecision | null;
  requested_launch?: {
    model_name: string;
    model_label?: string;
    effort?: string | null;
    compute_mode: ComputeMode;
  } | null;
}

export async function streamManagerRun(
  options: ManagerPayloadOptions,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
  onAccepted?: () => void
) {
  const response = await fetch("/agents/manager", {
    method: "POST",
    headers: {
      Accept: "application/x-ndjson",
      "Content-Type": "application/json",
      // Direct fetch bypasses api/client.ts, so the owner header is set here too.
      "X-Holo-Client": getClientId(),
      // App-level auth: the bearer token the backend hard-verifies to admit a run.
      ...authHeaders(),
      // SC1: duplicate the orchestrator into the header nginx routes on.
      "X-Holo-Orchestrator": options.orchestrator
    },
    body: JSON.stringify(buildManagerRequestPayload(options)),
    signal
  });

  if (!response.ok) {
    throw new AgentRunError(response.status, `Agent request failed: ${response.status}`);
  }
  if (!response.body) {
    throw new AgentRunError(response.status, "Agent response has no stream body");
  }
  onAccepted?.();
  await readNdjson(response.body, onEvent);
}

// Re-attach to a run this browser did not start (page reload) or lost (dropped
// connection). The server replays the run's event history first, then follows
// live -- so the caller should rebuild its state from scratch and reduce every
// event; the result converges to what an unbroken stream would have shown.
// 404 means "no run to attach to" (unknown session, someone else's session, or
// a backend restarted since) -- callers fall back to polling.
export async function attachSessionEvents(
  sessionId: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
  orchestrator: Orchestrator = "codearkt"
) {
  const response = await fetch(`/agents/events/${encodeURIComponent(sessionId)}`, {
    headers: {
      Accept: "application/x-ndjson",
      "X-Holo-Client": getClientId(),
      ...authHeaders(),
      // Same routing header as the run itself, so the attach reaches the
      // backend that owns the run.
      "X-Holo-Orchestrator": orchestrator
    },
    signal
  });
  if (!response.ok) {
    throw new AgentRunError(response.status, `Attach failed: ${response.status}`);
  }
  if (!response.body) {
    throw new AgentRunError(response.status, "Attach response has no stream body");
  }
  await readNdjson(response.body, onEvent);
}

// One physical card as reported by nvidia-smi. Metrics are null when the driver
// wouldn't state them -- which is also why such a card is never `free`.
export interface GpuProcess {
  pid: number | null;
  name: string;
  memory_mib: number | null;
}

export interface GpuCard {
  index: number;
  name: string;
  memory_used_mib: number | null;
  memory_total_mib: number | null;
  utilization_pct: number | null;
  temperature_c: number | null;
  power_draw_w: number | null;
  power_limit_w: number | null;
  // Compute processes resident on the card, as nvidia-smi reports them.
  processes: GpuProcess[];
  free: boolean;
  // Whether this deployment is allowed to lease the card (the configured pool).
  allowed: boolean;
  // Session holding it, when Holosophus itself leased it.
  held_by?: string | null;
}

export interface GpuSide {
  cards: GpuCard[];
  // "not_connected" (never probed), "disabled" (local GPU off), or a probe error.
  error: string | null;
  updated_at: number;
  provider?: string;
  host?: string | null;
  devices?: string;
}

// Optional: absent on an older backend, and either side may be null until its
// first probe. Distinct from `gpu` above, which is Holosophus's own lease
// bookkeeping rather than real occupancy.
export interface GpuTelemetry {
  remote: GpuSide | null;
  local: GpuSide | null;
}

export interface CapacityResponse {
  sessions: { active: number; limit: number; queued: number };
  // App-level auth: the caller's own active/limit ("your runs: N/3"); null when
  // unauthenticated or when auth is off.
  yours?: { active: number; limit: number | null } | null;
  gpu?: { busy: number; waiting: number; slots: number };
  local_exec?: { busy: number; waiting: number; slots: number };
  gpu_telemetry?: GpuTelemetry;
  host: { load1: number; load5: number; load15: number; cpu_count: number; mem_used_pct: number | null };
}

// `forced` is a hand-pinned card index that bypasses the free-card rule
// entirely; null means automatic selection.
export interface GpuPool {
  indices: number[];
  forced: number | null;
}

export async function fetchGpuPool(): Promise<GpuPool> {
  const response = await fetch("/ui/gpu-pool", { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`GPU pool request failed: ${response.status}`);
  }
  const data = (await response.json()) as Partial<GpuPool>;
  return { indices: data.indices ?? [], forced: data.forced ?? null };
}

export async function saveGpuPool(pool: GpuPool): Promise<GpuPool> {
  const response = await fetch("/ui/gpu-pool", {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(pool)
  });
  if (!response.ok) {
    throw new Error(`GPU pool save failed: ${response.status}`);
  }
  const data = (await response.json()) as Partial<GpuPool>;
  return { indices: data.indices ?? [], forced: data.forced ?? null };
}

export async function fetchCapacity(): Promise<CapacityResponse> {
  // GET /capacity is proxied by nginx directly to app:5055. It reports
  // process-wide dispatcher/host state; the identity headers only scope the
  // optional `yours` block (this account's own slot usage) -- display-only.
  const response = await fetch("/capacity", {
    headers: {
      Accept: "application/json",
      "X-Holo-Client": getClientId(),
      ...authHeaders()
    }
  });
  if (!response.ok) {
    throw new Error(`Capacity request failed: ${response.status}`);
  }
  return (await response.json()) as CapacityResponse;
}

export async function cancelAgentRun(sessionId: string, orchestrator: Orchestrator = "codearkt") {
  const response = await fetch("/agents/cancel", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Holo-Client": getClientId(),
      ...authHeaders(),
      // SC1: cancel must reach the same backend the run started on, so it
      // carries the orchestrator routing header too.
      "X-Holo-Orchestrator": orchestrator
    },
    body: JSON.stringify({ session_id: sessionId })
  });
  if (!response.ok) {
    throw new Error(`Cancel request failed: ${response.status}`);
  }
}

// Operator control (claude_code only). Each mirrors cancelAgentRun: same identity +
// routing headers, so the control reaches the backend that owns the run. The
// control channel is file-based, so an unknown/finished session still lands the
// write (the run picks it up if live) — the endpoints never 404 on that.
function controlHeaders(orchestrator: Orchestrator): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-Holo-Client": getClientId(),
    ...authHeaders(),
    "X-Holo-Orchestrator": orchestrator
  };
}

export async function sendControl(
  sessionId: string,
  verb: Exclude<ControlVerb, "steer">,
  orchestrator: Orchestrator = "claude_code"
) {
  const response = await fetch("/agents/control", {
    method: "POST",
    headers: controlHeaders(orchestrator),
    body: JSON.stringify({ session_id: sessionId, verb })
  });
  if (!response.ok) {
    throw new Error(`Control '${verb}' failed: ${response.status}`);
  }
}

export async function sendSteer(
  sessionId: string,
  text: string,
  mode: SteerMode,
  orchestrator: Orchestrator = "claude_code"
) {
  const response = await fetch("/agents/control", {
    method: "POST",
    headers: controlHeaders(orchestrator),
    body: JSON.stringify({ session_id: sessionId, verb: "steer", text, mode })
  });
  if (!response.ok) {
    throw new Error(`Steer failed: ${response.status}`);
  }
}

export async function sendAnswer(
  sessionId: string,
  qid: string,
  action: AnswerAction,
  text?: string,
  orchestrator: Orchestrator = "claude_code"
) {
  const response = await fetch("/agents/answer", {
    method: "POST",
    headers: controlHeaders(orchestrator),
    body: JSON.stringify(buildAnswerPayload(sessionId, qid, action, text))
  });
  if (!response.ok) {
    throw new AgentRunError(response.status, `Answer failed: ${response.status}`);
  }
}

// Poll for a pending question. 204 means no question; other errors remain
// distinguishable so the caller can retain a card during a transient failure.
export async function fetchQuestion(
  sessionId: string,
  orchestrator: Orchestrator = "claude_code",
  signal?: AbortSignal
): Promise<PendingQuestion | null> {
  const response = await fetch(`/agents/question/${encodeURIComponent(sessionId)}`, {
    headers: {
      Accept: "application/json",
      "X-Holo-Client": getClientId(),
      ...authHeaders(),
      "X-Holo-Orchestrator": orchestrator
    },
    signal
  });
  if (response.status === 204) {
    return null;
  }
  if (!response.ok) {
    throw new AgentRunError(response.status, `Question request failed: ${response.status}`);
  }
  const data = (await response.json()) as Partial<PendingQuestion>;
  if (!data.qid) {
    return null;
  }
  return {
    qid: data.qid,
    question: data.question ?? "",
    context: data.context ?? "",
    stage: data.stage === "sources" || data.stage === "plan" ? data.stage : null,
    document_path: data.document_path,
    document_digest: data.document_digest,
    asked_at: typeof data.asked_at === "number" ? data.asked_at * 1000 : 0,
    auto_approve_at: typeof data.auto_approve_at === "number" ? data.auto_approve_at * 1000 : null
  };
}

export async function fetchAgentSession(
  sessionId: string,
  orchestrator: Orchestrator = "claude_code",
  signal?: AbortSignal
): Promise<AgentSessionMetadata> {
  const response = await fetch(`/agents/session/${encodeURIComponent(sessionId)}`, {
    headers: {
      Accept: "application/json",
      "X-Holo-Client": getClientId(),
      ...authHeaders(),
      "X-Holo-Orchestrator": orchestrator
    },
    signal
  });
  if (!response.ok) {
    throw new AgentRunError(response.status, `Session metadata request failed: ${response.status}`);
  }
  return (await response.json()) as AgentSessionMetadata;
}

async function readNdjson(stream: ReadableStream<Uint8Array>, onEvent: (event: AgentEvent) => void) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";
    lines.forEach((line) => emitLine(line, onEvent));
  }

  buffer += decoder.decode();
  emitLine(buffer, onEvent);
}

function emitLine(line: string, onEvent: (event: AgentEvent) => void) {
  const trimmed = line.trim();
  if (!trimmed) {
    return;
  }
  const parsed = JSON.parse(trimmed) as AgentEvent;
  onEvent(parsed);
}
