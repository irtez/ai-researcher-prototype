/** Display model shared by live delivery and persisted research events. */
export type ActivityStatus = "running" | "complete" | "limited" | "error" | "cancelled" | "unknown";
type BaseEvent = { id: string; at: string };
export type ActivityEvent = BaseEvent & (
  | { kind: "message"; invocationId?: string; text: string }
  | { kind: "note"; invocationId?: string; text: string }
  | { kind: "agent_start"; invocationId: string; parentInvocationId?: string;
      role: string; label: string; task: string; prompt?: string; background?: boolean; legacy?: boolean; truncated?: boolean }
  | { kind: "agent_update"; invocationId: string; background: boolean }
  | { kind: "agent_resume"; invocationId: string; background?: boolean }
  | { kind: "agent_end"; invocationId: string; status: Exclude<ActivityStatus, "running">; summary?: string; truncated?: boolean }
  | { kind: "tool_call"; invocationId?: string; callId: string; name: string; summary: string; input: unknown; truncated?: boolean }
  | { kind: "tool_result"; invocationId?: string; callId: string; output: string; isError?: boolean; truncated?: boolean }
);
export type MessageActivity = { kind: "message" | "note"; id: string; at: string; text: string; status?: ActivityStatus; truncated?: boolean };
export type ToolActivity = { kind: "tool"; id: string; at: string; name: string; summary: string;
  input: unknown; output?: string; finishedAt?: string; status: ActivityStatus; incomplete?: boolean; truncated?: boolean };
export type AgentActivity = { kind: "agent"; id: string; at: string; role: string; label: string;
  task: string; prompt?: string; background: boolean; status: ActivityStatus; finishedAt?: string; children: ActivityNode[];
  summary?: string; incomplete?: boolean; legacy?: boolean; truncated?: boolean };
export type ActivityNode = MessageActivity | ToolActivity | AgentActivity;

export function buildActivity(events: ActivityEvent[], terminal?: string): { roots: ActivityNode[]; agents: AgentActivity[] } {
  const roots: ActivityNode[] = [];
  const agents = new Map<string, AgentActivity>();
  const tools = new Map<string, ToolActivity>();
  const inserted = new Set<string>();
  const processed = new Set<string>();
  const firstSeen = new Map<string, number>();
  // Index known calls first: partial/reordered pages can contain their children earlier.
  for (const event of events) {
    for (const id of ["parentInvocationId" in event ? event.parentInvocationId : undefined, "invocationId" in event ? event.invocationId : undefined]) {
      if (id && !firstSeen.has(id)) firstSeen.set(id, firstSeen.size);
    }
    if (event.kind === "agent_start" && !agents.has(event.invocationId)) {
      agents.set(event.invocationId, { kind: "agent", id: event.invocationId, at: event.at,
        role: event.role, label: event.label, task: event.task, prompt: event.prompt,
        background: !!event.background, status: event.legacy ? "unknown" : "running",
        legacy: event.legacy, truncated: event.truncated, children: [] });
    }
    if (event.kind === "tool_call" && !tools.has(event.callId)) {
      tools.set(event.callId, { kind: "tool", id: event.callId, at: event.at,
        name: event.name, summary: event.summary, input: event.input, status: "running", truncated: event.truncated });
    }
  }
  const owner = (id: string, at: string): AgentActivity => {
    let agent = agents.get(id);
    if (!agent) {
      agent = { kind: "agent", id, at, role: "subagent", label: "", task: "", background: false,
        status: "unknown", incomplete: true, children: [] };
      agents.set(id, agent);
      roots.push(agent);
      inserted.add(`agent:${id}`);
    }
    return agent;
  };
  const append = (node: ActivityNode, parent?: string) => {
    const key = `${node.kind}:${node.id}`;
    if (inserted.has(key)) return;
    inserted.add(key);
    // A corrupt parent cycle must not recurse forever during rendering.
    const contains = (items: ActivityNode[], target: string): boolean => items.some(item =>
      item.kind === "agent" && (item.id === target || contains(item.children, target)));
    const cycle = node.kind === "agent" && parent && (parent === node.id || contains(node.children, parent));
    (parent && !cycle ? owner(parent, node.at).children : roots).push(node);
  };
  for (const event of events) {
    if (processed.has(event.id)) continue;
    processed.add(event.id);
    switch (event.kind) {
      case "message": case "note":
        append({ kind: event.kind, id: event.id, at: event.at, text: event.text }, event.invocationId);
        break;
      case "agent_start":
        append(agents.get(event.invocationId)!, event.parentInvocationId);
        break;
      case "agent_update":
        owner(event.invocationId, event.at).background = event.background;
        break;
      case "agent_resume": {
        const agent = owner(event.invocationId, event.at);
        if (agent.summary) append({ kind: "note", id: `${event.id}:previous-summary`,
          at: agent.finishedAt ?? event.at, text: agent.summary, status: agent.status,
          truncated: agent.truncated }, event.invocationId);
        agent.status = "running";
        if (event.background !== undefined) agent.background = event.background;
        delete agent.finishedAt;
        delete agent.summary;
        delete agent.truncated;
        break;
      }
      case "agent_end": {
        const agent = owner(event.invocationId, event.at);
        agent.status = event.status;
        agent.finishedAt = event.at;
        agent.summary = event.summary;
        agent.truncated ||= event.truncated;
        for (const child of agent.children) {
          if (child.kind === "tool" && child.status === "running") child.status = "unknown";
        }
        break;
      }
      case "tool_call":
        append(tools.get(event.callId)!, event.invocationId);
        break;
      case "tool_result": {
        let tool = tools.get(event.callId);
        if (!tool) {
          tool = { kind: "tool", id: event.callId, at: event.at, name: "", summary: "",
            input: undefined, status: "unknown", incomplete: true };
          tools.set(tool.id, tool);
          append(tool, event.invocationId);
        }
        tool.output = event.output;
        tool.status = event.isError ? "error" : "complete";
        tool.finishedAt = event.at;
        tool.truncated ||= event.truncated;
        break;
      }
    }
  }
  // Completion of the run is not proof of a missing tool response or child completion.
  const closed = ["complete", "error", "cancelled"].includes(terminal ?? "");
  for (const agent of agents.values()) {
    if (closed && agent.status === "running") agent.status = terminal === "complete" ? "unknown" : "cancelled";
    for (const node of agent.children) {
      if (node.kind === "tool" && node.status === "running" && (closed || agent.finishedAt)) node.status = "unknown";
    }
  }
  if (closed) for (const tool of tools.values()) if (tool.status === "running") tool.status = "unknown";
  // The start records, not role/completion order, determine the tab order.
  return { roots, agents: [...agents.values()].sort((a, b) => (firstSeen.get(a.id) ?? Infinity) - (firstSeen.get(b.id) ?? Infinity)) };
}

export function buildActivityTree(events: ActivityEvent[]): ActivityNode[] {
  return buildActivity(events).roots;
}
