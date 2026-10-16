import type { AgentEvent, LiveRunState } from "./liveState";
import { invocationStageStatuses } from "./liveState.ts";
import type { Stage } from "./types";

export interface ApprovalRecord {
  stage?: string | null;
  status: string;
  qid?: string;
  text?: string;
  resolution?: string;
  resolved_at?: number;
  document_path?: string;
  document_text?: string;
  iteration_id?: string;
}

export interface Iteration {
  id: string;
  number: number | null;
  kind: "initial" | "feedback" | "legacy";
  status: string;
  user_message: string | null;
  created_at: string | null;
  finished_at: string | null;
  event_count: number;
  error: string | null;
  result: { source_path: string; snapshot_path: string | null } | null;
  settings?: { input_files?: string[]; input_attachments?: Array<{ name: string; path: string; size: number }> };
  plans?: Array<{ document_path: string; document_text: string; snapshot_path: string }>;
  approval_state?: { stages: Record<string, ApprovalRecord>; decisions: ApprovalRecord[] };
}

export interface IterationIndex {
  session_id: string;
  iterations: Iteration[];
  active_iteration_id: string | null;
  latest_result_iteration_id: string | null;
}

export interface IterationDetail extends Iteration {
  session_id: string;
  events: AgentEvent[];
  next_after: number;
  has_more: boolean;
  answer: string | null;
  result_content: string | null;
  legacy_live_state: LiveRunState | null;
}

export interface IterationDocument {
  stage: "sources" | "plan";
  document_path: string;
  document_text: string;
  approval?: ApprovalRecord;
  reusedFrom?: string;
  additional?: boolean;
}

// Main documents are separate from the decision log. A saved plan and its
// approval refer to one document, not two cards; old plans never grant permission.
export function iterationDocuments(iteration: Iteration): IterationDocument[] {
  const documents: IterationDocument[] = [];
  const stages = iteration.approval_state?.stages ?? {};
  const sources = stages.sources;
  if (sources?.document_path && sources.document_text) {
    documents.push({ stage: "sources", document_path: sources.document_path,
      document_text: sources.document_text, approval: sources,
      reusedFrom: ["approved", "auto_approved"].includes(sources.status) && sources.iteration_id && sources.iteration_id !== iteration.id ? sources.iteration_id : undefined });
  }
  const plan = !stages.plan?.iteration_id || stages.plan.iteration_id === iteration.id ? stages.plan : undefined;
  const savedPlans = iteration.plans ?? [];
  const primaryPath = plan?.document_path ?? (savedPlans.length === 1 ? savedPlans[0].document_path : undefined);
  for (const saved of savedPlans) {
    documents.push({ stage: "plan", document_path: saved.document_path,
      document_text: saved.document_text,
      approval: plan?.document_path === saved.document_path ? plan : undefined,
      additional: saved.document_path !== primaryPath });
  }
  if (plan?.document_path && plan.document_text && !documents.some(doc => doc.document_path === plan.document_path)) {
    documents.push({ stage: "plan", document_path: plan.document_path, document_text: plan.document_text, approval: plan });
  }
  return documents;
}

export function selectedIterationId(index?: IterationIndex, selection = "latest"): string | undefined {
  if (!index) return undefined;
  if (index.iterations.some((item) => item.id === selection)) return selection;
  return index.active_iteration_id ?? index.iterations[index.iterations.length - 1]?.id;
}

export function feedbackNumber(index: IterationIndex, id: string): number {
  return index.iterations.filter((item) => item.kind === "feedback").findIndex((item) => item.id === id) + 1;
}

// A response page can be replayed after a reconnect. Sequence, not text or time,
// identifies events: repeated identical messages are still distinct messages.
export function mergeIterationEvents(prior: AgentEvent[], incoming: AgentEvent[]): AgentEvent[] {
  const events = new Map<number, AgentEvent>();
  for (const event of [...prior, ...incoming]) {
    if (event.sequence !== undefined) events.set(event.sequence, event);
  }
  return [...events.values()].sort((a, b) => a.sequence! - b.sequence!);
}

export async function readIterationPages(
  readPage: (after: number) => Promise<IterationDetail>,
  previous?: IterationDetail
): Promise<IterationDetail> {
  let page = await readPage(previous?.next_after ?? 0);
  let events = previous?.events ?? [];
  const target = page.event_count;
  while (true) {
    events = mergeIterationEvents(events, page.events);
    if (!page.has_more || page.next_after >= target) return { ...page, events };
    const cursor = page.next_after;
    page = await readPage(cursor);
    if (page.next_after <= cursor) throw new Error("Iteration event cursor did not advance");
  }
}

export function iterationStages(stages: Stage[], iteration: Iteration, state: LiveRunState): Stage[] {
  if (iteration.kind === "legacy") return stages;
  const terminal = ["complete", "error", "cancelled", "unknown"].includes(iteration.status);
  const normalize = (agent: string) => agent.replace(/-/g, "_").toLowerCase();
  const started = new Set(state.startedAgents.map(normalize));
  const ended = new Set(state.endedAgents.map(normalize));
  const active = new Set(state.activeAgents.map(normalize));
  const invocationStatuses = invocationStageStatuses(state);
  return stages.map((stage) => {
    const agent = normalize(stage.agent);
    // Aggregate the experiment stage, while preserving distinct roles/calls in
    // the activity feed. Auto mode may use both local and remote experimenters.
    const agents = [...new Set([...started, ...ended, ...active])].filter(name =>
      name === agent || (stage.id === "experiments" && ["mle_solver", "mle_solver_remote"].includes(name))
    );
    const allEnded = agents.length > 0 && agents.every(name => ended.has(name));
    let status: Stage["status"] = terminal ? "not_run" : "pending";
    if (agents.length) {
      if (terminal) {
        status = iteration.status === "complete" || (allEnded && agent !== "manager")
          ? "complete" : iteration.status === "cancelled" ? "cancelled" : "error";
      } else {
        status = agents.some(name => active.has(name)) ? "running" : allEnded ? "complete" : "running";
      }
    }
    return { ...stage, agent: agents.length ? agents.join(", ") : stage.agent,
      status: invocationStatuses.get(stage.id) ?? status, artifact_count: 0 };
  });
}

export function iterationSelectionKey(sessionId: string): string {
  return `holosophus.ui.iteration.${sessionId}`;
}

export function documentLink(sessionId: string, sourcePath: string, href: string): string {
  if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) return href;
  if (!href || href.startsWith("#")) return href;
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("/") || href.includes("\\")) return "#";
  try {
    const path = decodeURIComponent(new URL(href, `https://artifact.invalid/${sourcePath}`).pathname.slice(1));
    // The raw endpoint serves files only. Keep explicit folder references as text.
    if (!path || path.endsWith("/")) return "";
    return `/ui/artifacts/${encodeURIComponent(sessionId)}/raw?path=${encodeURIComponent(path)}`;
  } catch {
    return "#";
  }
}
