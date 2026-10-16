import type { AgentEvent, LiveRunState } from "../liveState";
import type { ActivityEvent } from "./activity";

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const string = (value: unknown) => typeof value === "string" ? value : "";
const optional = (value: unknown) => string(value) || undefined;

/** Legacy records are grouped by role explicitly, never inferred to be individual calls. */
export function activityFromEvents(records: AgentEvent[]): { events: ActivityEvent[]; legacy: boolean } {
  const events: ActivityEvent[] = [];
  const roles = new Set<string>();
  const seen = new Set<string>();
  let legacy = false;
  records.forEach((record, index) => {
    const id = `${record.iteration_id ?? "live"}:${record.sequence ?? index}`;
    if (seen.has(id)) return;
    seen.add(id);
    const data = record.activity;
    const at = record.received_at ?? "";
    if (object(data) && data.version === 1) {
      const base = { id, at: string(data.at) || at, invocationId: optional(data.invocationId) };
      const callId = string(data.callId);
      switch (data.kind) {
        case "message":
          events.push({ ...base, kind: "message", text: string(data.text) }); return;
        case "agent_start":
          if (base.invocationId) events.push({ ...base, kind: "agent_start", invocationId: base.invocationId,
            parentInvocationId: optional(data.parentInvocationId), role: string(data.role), label: string(data.role).replace(/_/g, "-"),
            task: string(data.task), prompt: optional(data.prompt), background: data.background === true, truncated: data.truncated === true });
          return;
        case "agent_update":
          if (base.invocationId) events.push({ ...base, kind: "agent_update", invocationId: base.invocationId, background: data.background === true });
          return;
        case "agent_resume":
          if (base.invocationId) events.push({ ...base, kind: "agent_resume", invocationId: base.invocationId,
            background: typeof data.background === "boolean" ? data.background : undefined });
          return;
        case "agent_end":
          if (base.invocationId) events.push({ ...base, kind: "agent_end", invocationId: base.invocationId,
            status: data.status === "complete" || data.status === "limited" || data.status === "error" || data.status === "cancelled" ? data.status : "unknown",
            summary: optional(data.summary), truncated: data.truncated === true });
          return;
        case "tool_call":
          if (callId) events.push({ ...base, kind: "tool_call", callId, name: string(data.name), summary: string(data.summary), input: data.input, truncated: data.truncated === true });
          return;
        case "tool_result":
          if (callId) events.push({ ...base, kind: "tool_result", callId, output: string(data.output), isError: data.isError === true, truncated: data.truncated === true });
          return;
        case "manager_turn_end": return;
      }
    }
    // Manager/session lifecycle has its own status UI; it is not a tool call.
    if (record.event_type.startsWith("session_") || record.agent_name === "manager" && ["agent_start", "agent_end", "agent_interrupted"].includes(record.event_type)) return;
    if (!record.content && record.event_type !== "agent_start") return;
    if (record.event_type === "observation") {
      try { if (JSON.parse(record.content ?? "").kind === "rate_limit_wait") return; } catch { /* Ordinary text. */ }
    }
    legacy = true;
    const role = record.agent_name || "subagent";
    const invocationId = role === "manager" ? undefined : `legacy:${role}`;
    if (invocationId && !roles.has(role)) {
      roles.add(role);
      events.push({ kind: "agent_start", id: `role:${id}`, at, invocationId, role, label: role.replace(/_/g, "-"), task: "", legacy: true });
    }
    if (record.content) events.push({ kind: record.event_type === "output" ? "message" : "note", id, at, invocationId, text: record.content });
  });
  return { events, legacy };
}

export function activityFromState(state: LiveRunState) {
  if (state.recordedEvents?.length) return activityFromEvents(state.recordedEvents);
  // Old browser snapshots kept messages and observations separately. Retain their
  // timestamps when present; do not try to pair observations with guessed calls.
  const common = { session_id: state.sessionId ?? "" };
  const messages = state.messages?.length ? state.messages : Object.entries(state.transcriptByAgent).map(([agent, content]) =>
    ({ agent, content, received_at: undefined }));
  const records: AgentEvent[] = [
    ...(state.rawPlan ? [{ ...common, agent_name: "manager", event_type: "planning_output" as const, content: state.rawPlan }] : []),
    ...messages.map(message => ({ ...common, agent_name: message.agent, event_type: "output" as const, content: message.content, received_at: message.received_at })),
    ...state.toolMarkers.map(marker => ({ ...common, agent_name: marker.agent, event_type: "observation" as const, content: marker.content, received_at: marker.received_at }))
  ];
  records.sort((a, b) => a.received_at && b.received_at ? a.received_at.localeCompare(b.received_at) : 0);
  return activityFromEvents(records);
}
