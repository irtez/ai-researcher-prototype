import type { Stage } from "./types";
import type { IterationDetail } from "./iterationHistory";
import { buildActivity } from "./activity/activity.ts";
import { activityFromEvents } from "./activity/fromEvents.ts";

export type AgentEventType =
  | "agent_start"
  | "planning_output"
  | "output"
  | "observation"
  | "agent_end"
  | "agent_interrupted"
  | "session_queued"
  | "session_running"
  | "session_position";

export interface AgentEvent {
  session_id: string;
  agent_name: string;
  event_type: AgentEventType;
  content?: string | null;
  received_at?: string;
  iteration_id?: string;
  sequence?: number;
  message_id?: string;
  output_mode?: "message" | "delta";
  activity?: Record<string, unknown>;
}

export interface ToolMarker {
  agent: string;
  content: string;
  received_at?: string;
}

export interface AgentMessage {
  id: string;
  agent: string;
  content: string;
  received_at?: string;
}

export interface LiveRunState {
  sessionId: string | null;
  // "waiting_rate_limit" is stream-derived (the runner's rate-limit heartbeat); "paused" and
  // "waiting_for_human" are UI-overlaid (operator pause flag / question poll) since the driver
  // emits no event for them.
  status:
    | "idle"
    | "running"
    | "queued"
    | "complete"
    | "cancelled"
    | "error"
    | "paused"
    | "waiting_for_human"
    | "waiting_rate_limit";
  activeAgents: string[];
  activeAgentCounts?: Record<string, number>;
  currentAgent?: string;
  events: AgentEvent[];
  recordedEvents?: AgentEvent[];
  rawPlan: string;
  transcriptByAgent: Record<string, string>;
  messages?: AgentMessage[];
  messageMode?: "message" | "delta";
  fullHistory?: boolean;
  observationCount: number;
  toolMarkers: ToolMarker[];
  startedAgents: string[];
  endedAgents: string[];
  queuePosition?: number;
  queueDepth?: number;
  // Seconds until the subscription window resets, from the latest rate-limit heartbeat.
  rateLimitResetsInS?: number;
  error?: string;
}

const EMPTY_LIVE_STATE: LiveRunState = {
  sessionId: null,
  status: "idle",
  activeAgents: [],
  events: [],
  rawPlan: "",
  transcriptByAgent: {},
  observationCount: 0,
  toolMarkers: [],
  startedAgents: [],
  endedAgents: []
};

export function emptyLiveState(): LiveRunState {
  return { ...EMPTY_LIVE_STATE };
}

export function newLiveRun(sessionId: string, messageMode: "message" | "delta" = "delta"): LiveRunState {
  return {
    ...EMPTY_LIVE_STATE,
    sessionId,
    messageMode,
    messages: [],
    status: "running",
    activeAgents: [],
    activeAgentCounts: {},
    events: [],
    recordedEvents: [],
    transcriptByAgent: {},
    toolMarkers: [],
    startedAgents: [],
    endedAgents: []
  };
}

export function reduceAgentEvent(state: LiveRunState, event: AgentEvent): LiveRunState {
  if (event.sequence !== undefined && state.recordedEvents?.some(prior =>
    prior.sequence === event.sequence && prior.iteration_id === event.iteration_id)) return state;
  const stampedEvent = event.received_at ? event : { ...event, received_at: new Date().toISOString() };
  // Rate-limit heartbeat: the runner emits it as an `observation` carrying
  // {"kind":"rate_limit_wait","resets_in_s":N} ~every 60s while auto-waiting a
  // usage limit. It drives the waiting status but is not a transcript event.
  const rateLimitWaitS = parseRateLimitWaitSeconds(stampedEvent);
  const timelineEvent = isTimelineEvent(stampedEvent) ? stampedEvent : undefined;
  // A real event arriving after a rate-limit hold means the run resumed.
  let status = state.status === "idle" ? "running" : state.status;
  if (status === "waiting_rate_limit" && rateLimitWaitS === null) {
    status = "running";
  }
  const next: LiveRunState = {
    ...state,
    sessionId: stampedEvent.session_id || state.sessionId,
    status,
    events: timelineEvent ? [...state.events, timelineEvent].slice(state.fullHistory ? 0 : -200) : state.events
  };

  if (rateLimitWaitS !== null) {
    // Heartbeat: set the waiting status + countdown; do not treat as a tool observation.
    next.status = "waiting_rate_limit";
    next.rateLimitResetsInS = rateLimitWaitS;
    return next;
  }
  if (state.messageMode === "message" || stampedEvent.activity) {
    next.recordedEvents = [...(state.recordedEvents ?? []), stampedEvent];
  }

  if (stampedEvent.event_type === "agent_start" ||
      stampedEvent.activity?.version === 1 && stampedEvent.activity.kind === "agent_resume") {
    next.activeAgentCounts = { ...state.activeAgentCounts,
      [stampedEvent.agent_name]: (state.activeAgentCounts?.[stampedEvent.agent_name] ?? 0) + 1 };
    next.activeAgents = unique([...state.activeAgents, stampedEvent.agent_name]);
    next.endedAgents = state.endedAgents.filter((agent) => agent !== stampedEvent.agent_name);
    next.currentAgent = stampedEvent.agent_name;
    next.startedAgents = unique([...state.startedAgents, stampedEvent.agent_name]);
  }

  if (stampedEvent.event_type === "agent_end") {
    const remaining = Math.max(0, (state.activeAgentCounts?.[stampedEvent.agent_name] ?? 1) - 1);
    next.activeAgentCounts = { ...state.activeAgentCounts, [stampedEvent.agent_name]: remaining };
    next.activeAgents = remaining ? state.activeAgents : state.activeAgents.filter((agent) => agent !== stampedEvent.agent_name);
    next.currentAgent = next.activeAgents[next.activeAgents.length - 1];
    next.endedAgents = remaining ? state.endedAgents : unique([...state.endedAgents, stampedEvent.agent_name]);
  }

  if (stampedEvent.event_type === "agent_interrupted") {
    next.status = "cancelled";
    next.activeAgents = [];
    next.currentAgent = undefined;
  }

  if (stampedEvent.event_type === "session_queued") {
    const payload = parseResourceWaitPayload(stampedEvent.content);
    next.status = "queued";
    next.queuePosition = payload?.position;
    next.queueDepth = payload?.depth;
  }

  if (stampedEvent.event_type === "session_running") {
    // Slot freed up: back to running (unless the run was already cancelled/errored).
    next.status = state.status === "cancelled" || state.status === "error" ? state.status : "running";
    next.queuePosition = undefined;
    next.queueDepth = undefined;
  }

  if (stampedEvent.event_type === "session_position") {
    const payload = parseResourceWaitPayload(stampedEvent.content);
    if (payload) {
      next.queuePosition = payload.position;
      next.queueDepth = payload.depth;
    }
  }

  if (stampedEvent.event_type === "planning_output" && stampedEvent.content) {
    next.rawPlan = appendText(state.rawPlan, stampedEvent.content);
  }

  if (stampedEvent.event_type === "output" && stampedEvent.content) {
    const messages = state.messages ?? [];
    const mode = stampedEvent.output_mode ?? state.messageMode ?? "delta";
    const id = stampedEvent.message_id ?? (mode === "message"
      ? `${stampedEvent.iteration_id ?? "live"}:${stampedEvent.sequence ?? messages.length}`
      : `legacy:${stampedEvent.agent_name}`);
    const index = messages.findIndex((message) => message.id === id && message.agent === stampedEvent.agent_name);
    const message = { id, agent: stampedEvent.agent_name, content: stampedEvent.content, received_at: stampedEvent.received_at };
    next.messages = index < 0 ? [...messages, message] : messages.map((prior, i) => i === index
      ? { ...prior, content: mode === "delta" ? prior.content + stampedEvent.content : stampedEvent.content! }
      : prior);
    const prior = state.transcriptByAgent[stampedEvent.agent_name] ?? "";
    next.transcriptByAgent = {
      ...state.transcriptByAgent,
      [stampedEvent.agent_name]: appendText(prior, stampedEvent.content)
    };
  }

  if (stampedEvent.event_type === "observation") {
    next.observationCount = state.observationCount + 1;
    next.toolMarkers = [
      ...state.toolMarkers,
      {
        agent: stampedEvent.agent_name,
        content: stampedEvent.content ?? "",
        received_at: stampedEvent.received_at
      }
    ].slice(state.fullHistory ? 0 : -200);
  }

  return next;
}

export function completeLiveRun(state: LiveRunState): LiveRunState {
  if (state.status === "cancelled" || state.status === "error") {
    return state;
  }
  return { ...state, status: "complete", activeAgents: [], currentAgent: undefined, queuePosition: undefined, queueDepth: undefined };
}

export function failLiveRun(state: LiveRunState, error: unknown): LiveRunState {
  if (state.status === "cancelled") {
    return state;
  }
  const message = error instanceof Error ? error.message : String(error);
  return {
    ...state,
    status: "error",
    activeAgents: [],
    currentAgent: undefined,
    queuePosition: undefined,
    queueDepth: undefined,
    error: message
  };
}

export function cancelLiveRun(state: LiveRunState): LiveRunState {
  return { ...state, status: "cancelled", activeAgents: [], currentAgent: undefined, queuePosition: undefined, queueDepth: undefined };
}

export function iterationLiveState(iteration: IterationDetail): LiveRunState {
  if (iteration.kind === "legacy") {
    return { ...newLiveRun(iteration.session_id), ...iteration.legacy_live_state };
  }
  const state = iteration.events.reduce(reduceAgentEvent, { ...newLiveRun(iteration.session_id, "message"), fullHistory: true });
  const terminal = ["complete", "error", "cancelled"].includes(iteration.status);
  return {
    ...state,
    status: iteration.status as LiveRunState["status"],
    error: iteration.error ?? undefined,
    activeAgents: terminal ? [] : state.activeAgents,
    currentAgent: terminal ? undefined : state.currentAgent
  };
}

// content is a JSON string like {"resource":"session","position":2,"depth":3};
// malformed/absent payloads are kept safe (the caller just skips the update).
function parseResourceWaitPayload(content?: string | null): { resource?: string; position?: number; depth?: number } | undefined {
  if (!content) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(content) as unknown;
    if (parsed && typeof parsed === "object") {
      return parsed as { resource?: string; position?: number; depth?: number };
    }
  } catch {
    // Malformed payload; ignore and leave queue state untouched.
  }
  return undefined;
}

// The rate-limit heartbeat arrives as an `observation` whose content is
// {"kind":"rate_limit_wait","resets_in_s":N}. Returns N (>=0) for a heartbeat,
// or null for any other observation / malformed payload.
function parseRateLimitWaitSeconds(event: AgentEvent): number | null {
  if (event.event_type !== "observation" || !event.content) {
    return null;
  }
  try {
    const parsed = JSON.parse(event.content) as unknown;
    if (parsed && typeof parsed === "object") {
      const obj = parsed as { kind?: string; resets_in_s?: number };
      if (obj.kind === "rate_limit_wait") {
        return typeof obj.resets_in_s === "number" ? obj.resets_in_s : 0;
      }
    }
  } catch {
    // Not JSON -> an ordinary observation; leave it alone.
  }
  return null;
}

export function optimisticStages(baseStages: Stage[] | undefined, liveState: LiveRunState | undefined): Stage[] {
  const stages = baseStages?.length ? baseStages : defaultStages();
  if (!liveState || liveState.status === "idle") {
    return stages;
  }
  const activeStageIds = new Set<string | undefined>([...liveState.activeAgents, liveState.currentAgent].map(agentToStage));
  const startedStageIds = new Set<string>(liveState.startedAgents.map(agentToStage).filter(Boolean) as string[]);
  const endedStageIds = new Set<string>(liveState.endedAgents.map(agentToStage).filter(Boolean) as string[]);
  const invocationStatuses = invocationStageStatuses(liveState);

  return stages.map((stage) => {
    const invocationStatus = invocationStatuses.get(stage.id);
    if (invocationStatus) return { ...stage, status: invocationStatus };
    if (activeStageIds.has(stage.id) && liveState.status === "running") {
      return { ...stage, status: "running" };
    }
    if (endedStageIds.has(stage.id) || startedStageIds.has(stage.id)) {
      return { ...stage, status: "complete" };
    }
    return stage;
  });
}

// Explicit invocation outcomes take precedence over legacy role-level counters.
export function invocationStageStatuses(state: LiveRunState): Map<string, Stage["status"]> {
  const { agents } = buildActivity(activityFromEvents(state.recordedEvents ?? []).events, state.status);
  const statuses = new Map<string, Stage["status"][]>();
  for (const agent of agents) {
    const stage = agentToStage(agent.role);
    if (!stage || agent.legacy) continue;
    statuses.set(stage, [...(statuses.get(stage) ?? []), agent.status]);
  }
  const priority: Stage["status"][] = ["running", "limited", "error", "cancelled", "unknown", "complete"];
  return new Map([...statuses].map(([stage, values]) => [stage, priority.find(status => values.includes(status))!]));
}

export function defaultStages(): Stage[] {
  return [
    { id: "prompt", label: "Prompt", agent: "manager", status: "pending", artifact_count: 0 },
    { id: "literature", label: "Literature overview", agent: "librarian", status: "pending", artifact_count: 0 },
    { id: "proposals", label: "Proposals", agent: "proposer", status: "pending", artifact_count: 0 },
    { id: "experiments", label: "Experiments", agent: "mle_solver", status: "pending", artifact_count: 0 },
    { id: "paper", label: "Paper", agent: "writer", status: "pending", artifact_count: 0 },
    { id: "review", label: "Review", agent: "reviewer", status: "pending", artifact_count: 0 }
  ];
}

export function agentToStage(agent?: string) {
  if (!agent) {
    return undefined;
  }
  const normalized = agent.toLowerCase().replace(/-/g, "_");
  if (normalized === "manager") {
    return "prompt";
  }
  if (normalized === "librarian") {
    return "literature";
  }
  if (normalized === "proposer") {
    return "proposals";
  }
  if (normalized === "mle_solver" || normalized === "mle_solver_remote") {
    return "experiments";
  }
  if (normalized === "writer") {
    return "paper";
  }
  if (normalized === "reviewer") {
    return "review";
  }
  return undefined;
}

function unique(values: string[]) {
  return Array.from(new Set(values));
}

function appendText(prior: string, next: string) {
  return `${prior}${next}`;
}

function isTimelineEvent(event: AgentEvent) {
  if (event.event_type === "output" || event.event_type === "planning_output") {
    return false;
  }
  if (
    event.event_type === "session_queued" ||
    event.event_type === "session_running" ||
    event.event_type === "session_position"
  ) {
    // Surfaced via the dedicated queued banner, not the per-agent transcript timeline.
    return false;
  }
  if (event.event_type !== "observation") {
    return true;
  }
  // The rate-limit heartbeat rides on `observation`; it drives status, not the timeline.
  if (parseRateLimitWaitSeconds(event) !== null) {
    return false;
  }
  const content = event.content?.trim();
  return Boolean(content) && !isToolNoise(content ?? "");
}

function isToolNoise(content: string) {
  const trimmed = content.trim();
  return (
    /^\[tool-start\]\s+/i.test(trimmed) ||
    /^\[tool-end\]\s+executor call completed/i.test(trimmed) ||
    /^Output:\s*$/i.test(trimmed) ||
    /^Last expression:\s*$/i.test(trimmed)
  );
}
