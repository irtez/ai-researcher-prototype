import assert from "node:assert/strict";
import test from "node:test";
import { documentLink, iterationDocuments, iterationStages, readIterationPages, selectedIterationId } from "../src/iterationHistory.ts";
import type { IterationDetail, IterationIndex } from "../src/iterationHistory.ts";
import { defaultStages, iterationLiveState, newLiveRun, optimisticStages, reduceAgentEvent } from "../src/liveState.ts";
import type { AgentEvent } from "../src/liveState.ts";

function event(type: AgentEvent["event_type"], agent = "manager", content?: string): AgentEvent {
  return { session_id: "session", agent_name: agent, event_type: type, content, received_at: "2026-09-09T10:00:00Z" };
}
function iteration(id: string, events: AgentEvent[], status = "complete"): IterationDetail {
  return { id, number: Number(id), session_id: "session", kind: id === "0001" ? "initial" : "feedback",
    status, user_message: "task", created_at: null, finished_at: null, error: null,
    events: events.map((event, n) => ({ ...event, iteration_id: id, sequence: n + 1 })),
    event_count: events.length, next_after: events.length, has_more: false, answer: null,
    result: null, result_content: null, legacy_live_state: null };
}

for (const role of ["mle_solver", "mle-solver", "mle_solver_remote", "mle-solver-remote"]) {
  test(`experiment stage follows ${role} live and when reopening history`, () => {
    const item = iteration("0001", [event("agent_start", role)], "running");
    const live = iterationLiveState(item);
    assert.equal(iterationStages(defaultStages(), item, live).find(s => s.id === "experiments")?.status, "running");
    assert.equal(optimisticStages(undefined, live).find(s => s.id === "experiments")?.status, "running");
    assert.deepEqual(live.activeAgents, [role]); // Activity identities remain distinct.
    item.events.push(event("agent_end", role));
    item.status = "complete";
    const stage = iterationStages(defaultStages(), item, iterationLiveState(item)).find(s => s.id === "experiments");
    assert.equal(stage?.status, "complete");
    assert.equal(stage?.agent, role.replaceAll("-", "_"));
  });
}

test("local and remote experimenters share a stage without hiding unfinished work", () => {
  const item = iteration("0001", [event("agent_start", "mle_solver_remote"), event("agent_start", "mle_solver"), event("agent_end", "mle_solver")], "running");
  const live = iterationLiveState(item);
  assert.equal(iterationStages(defaultStages(), item, live).find(s => s.id === "experiments")?.status, "running");
  assert.equal(optimisticStages(undefined, live).find(s => s.id === "experiments")?.status, "running");
  item.status = "cancelled";
  assert.equal(iterationStages(defaultStages(), item, iterationLiveState(item)).find(s => s.id === "experiments")?.status, "cancelled");
  item.events.push(event("agent_end", "mle_solver_remote"));
  assert.equal(iterationStages(defaultStages(), item, iterationLiveState(item)).find(s => s.id === "experiments")?.status, "complete");
});

test("revision documents include one current plan and explicitly reusable earlier sources", () => {
  const item = iteration("0002", []);
  item.plans = [{ document_path: "plans/0002/round.md", document_text: "Final plan", snapshot_path: "snapshot" }];
  item.approval_state = { stages: {
    plan: { status: "approved", iteration_id: "0002", document_path: "plans/0002/round.md", document_text: "Final plan" },
    sources: { status: "approved", iteration_id: "0001", document_path: "sources/0001/input.md", document_text: "Same inputs" }
  }, decisions: [{ stage: "plan", status: "changes_requested", document_path: "plans/0001/old.md", document_text: "Earlier draft" }] };
  const docs = iterationDocuments(item);
  assert.equal(docs.length, 2);
  assert.deepEqual(docs.map(doc => doc.document_path), ["sources/0001/input.md", "plans/0002/round.md"]);
  assert.equal(docs[0].reusedFrom, "0001");
  assert.equal(docs[1].reusedFrom, undefined);
  assert.equal(docs[1].document_text, "Final plan");
  item.approval_state.stages.sources.status = "invalidated";
  assert.equal(iterationDocuments(item)[0].reusedFrom, undefined);
  item.plans.push({ document_path: "plans/0002/draft.md", document_text: "Draft", snapshot_path: "draft-snapshot" });
  assert.deepEqual(iterationDocuments(item).filter(doc => doc.stage === "plan" && !doc.additional).map(doc => doc.document_path), ["plans/0002/round.md"]);
  delete item.approval_state.stages.plan;
  assert.equal(iterationDocuments(item).filter(doc => doc.stage === "plan" && !doc.additional).length, 0);
  assert.equal(iterationDocuments(item).filter(doc => doc.additional).length, 2);
});

test("pending plan is visible before final snapshot; saved unapproved plans remain available", () => {
  const item = iteration("0002", [], "running");
  item.approval_state = { stages: { plan: { status: "pending", iteration_id: "0002", document_path: "plans/0002/plan.md", document_text: "Draft" } }, decisions: [] };
  assert.equal(iterationDocuments(item)[0].document_text, "Draft");
  item.approval_state = { stages: { plan: { status: "approved", iteration_id: "0001", document_path: "plans/0001/old.md", document_text: "Old plan" } }, decisions: [] };
  item.plans = [{ document_path: "plans/0002/actions.md", document_text: "New plan without approvals", snapshot_path: "snapshot" }];
  assert.deepEqual(iterationDocuments(item).map(doc => doc.document_path), ["plans/0002/actions.md"]);
  assert.equal(iterationDocuments(item)[0].approval, undefined);
});

test("manager messages keep boundaries, identical text and reception time", () => {
  const view = iterationLiveState(iteration("0001", [event("output", "manager", "Проверяю."), event("output", "manager", "Готово."), event("output", "manager", "Готово.")]));
  assert.deepEqual(view.messages?.map((item) => item.content), ["Проверяю.", "Готово.", "Готово."]);
  assert.equal(view.messages?.[0].received_at, "2026-09-09T10:00:00Z");
});

test("identified deltas join inside one message, subsequent messages stay separate", () => {
  let state = newLiveRun("session", "message");
  for (const content of ["При", "вет"]) state = reduceAgentEvent(state, { ...event("output", "manager", content), message_id: "m1", output_mode: "delta" });
  state = reduceAgentEvent(state, { ...event("output", "manager", "Далее"), message_id: "m2", output_mode: "message" });
  assert.deepEqual(state.messages?.map((item) => item.content), ["Привет", "Далее"]);
});

test("legacy flattened text is preserved without inventing messages", () => {
  const legacy = { ...iteration("legacy", []), kind: "legacy" as const,
    legacy_live_state: { ...newLiveRun("session"), messages: undefined, transcriptByAgent: { manager: "Проверяю.Готово." } } };
  const view = iterationLiveState(legacy);
  assert.equal(view.transcriptByAgent.manager, "Проверяю.Готово.");
  assert.equal(view.messages, undefined);
});

test("writer unused initially becomes running and complete only in the revision", () => {
  const initial = iteration("0001", [event("agent_start"), event("agent_end")]);
  const revision = iteration("0002", [event("agent_start"), event("agent_start", "writer")], "running");
  const stage = (item: IterationDetail, name: string) => iterationStages(defaultStages(), item, iterationLiveState(item)).find((row) => row.agent === name)?.status;
  assert.equal(stage(initial, "writer"), "not_run");
  assert.equal(stage(revision, "writer"), "running");
  revision.events.push(event("agent_end", "writer"));
  revision.status = "complete";
  assert.equal(stage(revision, "writer"), "complete");
  assert.equal(stage(initial, "writer"), "not_run");
});

test("old artifacts do not mark a new experiment or a failed result successful", () => {
  const oldStages = defaultStages().map((stage) => ({ ...stage, status: "complete" as const, artifact_count: 9 }));
  for (const status of ["running", "error", "cancelled"]) {
    const item = iteration("0002", [event("agent_start"), event("agent_start", "mle_solver")], status);
    const stages = iterationStages(oldStages, item, iterationLiveState(item));
    assert.equal(stages.find((s) => s.id === "experiments")?.status, status);
    assert.equal(stages.find((s) => s.id === "paper")?.status, status === "running" ? "pending" : "not_run");
  }
});

test("repeated and parallel delegation does not inherit a previous completed status", () => {
  const item = iteration("0002", [event("agent_start", "mle_solver"), event("agent_end", "mle_solver"), event("agent_start", "mle_solver"), event("agent_start", "writer")], "running");
  const view = iterationLiveState(item);
  assert.equal(view.endedAgents.includes("mle_solver"), false);
  assert.equal(iterationStages(defaultStages(), item, view).filter((stage) => stage.status === "running").length, 2);
});

test("selection defaults to current work, explicit history selection survives new work", () => {
  const index: IterationIndex = { session_id: "session", iterations: [iteration("0001", []), iteration("0002", [], "running")], active_iteration_id: "0002", latest_result_iteration_id: "0001" };
  assert.equal(selectedIterationId(index), "0002");
  assert.equal(selectedIterationId(index, "0001"), "0001");
  assert.equal(selectedIterationId(index, "missing"), "0002");
  index.active_iteration_id = null;
  assert.equal(selectedIterationId(index), "0002");
});

test("parallel calls of the same role remain running until both finish", () => {
  const item = iteration("0002", [event("agent_start", "writer"), event("agent_start", "writer"), event("agent_end", "writer")], "running");
  let state = iterationLiveState(item);
  assert.equal(iterationStages(defaultStages(), item, state).find((stage) => stage.id === "paper")?.status, "running");
  item.events.push(event("agent_end", "writer"));
  state = iterationLiveState(item);
  assert.equal(iterationStages(defaultStages(), item, state).find((stage) => stage.id === "paper")?.status, "complete");
});

test("limited and resumed invocation statuses stay honest in live and saved stages", () => {
  const events: AgentEvent[] = [
    { ...event("agent_start", "writer"), activity: { version: 1, kind: "agent_start", invocationId: "w1", role: "writer" } },
    { ...event("agent_end", "writer"), activity: { version: 1, kind: "agent_end", invocationId: "w1", status: "limited" } },
    { ...event("observation", "writer"), activity: { version: 1, kind: "agent_resume", invocationId: "w1" } },
    { ...event("agent_end", "writer"), activity: { version: 1, kind: "agent_end", invocationId: "w1", status: "complete" } },
  ];
  for (const [length, expected] of [[2, "limited"], [3, "running"], [4, "complete"]] as const) {
    const item = iteration("0001", events.slice(0, length), "running");
    const state = iterationLiveState(item);
    assert.equal(iterationStages(defaultStages(), item, state).find(stage => stage.id === "paper")?.status, expected);
    assert.equal(optimisticStages(undefined, state).find(stage => stage.id === "paper")?.status, expected);
    assert.equal(state.activeAgents.includes("writer"), expected === "running");
    assert.deepEqual(item.events.reduce(reduceAgentEvent, state), state);
  }
  const finishedWithLimit = iteration("0001", events.slice(0, 2), "complete");
  assert.equal(iterationStages(defaultStages(), finishedWithLimit, iterationLiveState(finishedWithLimit)).find(stage => stage.id === "paper")?.status, "limited");
});

test("pagination and a replayed tail preserve all messages exactly once", async () => {
  const source = iteration("0001", Array.from({ length: 1005 }, () => event("output", "manager", "same")));
  const cursors: number[] = [];
  const readPage = async (after: number) => {
    cursors.push(after);
    const next = Math.min(after + 1000, source.events.length);
    return { ...source, events: source.events.slice(after, next), next_after: next, has_more: next < source.events.length };
  };
  const all = await readIterationPages(readPage);
  assert.equal(all.events.length, 1005);
  assert.deepEqual(cursors, [0, 1000]);
  const replay = await readIterationPages(async () => ({ ...source, events: source.events.slice(-2) }), all);
  assert.equal(replay.events.length, 1005);
});

test("historical timelines retain early events beyond the live cache limit", () => {
  const item = iteration("0001", Array.from({ length: 205 }, (_, n) => event("observation", "manager", `tool ${n}`)));
  const state = iterationLiveState(item);
  assert.equal(state.events.length, 205);
  assert.equal(state.toolMarkers.length, 205);
  assert.equal(state.toolMarkers[0].content, "tool 0");
});

test("snapshot links resolve against original document path and reject unsafe schemes", () => {
  assert.equal(documentLink("session", "results/0002/result.md", "../../mle/data.csv"), "/ui/artifacts/session/raw?path=mle%2Fdata.csv");
  assert.equal(documentLink("session", "results/0002/result.md", "https://example.org/paper"), "https://example.org/paper");
  for (const href of ["javascript:alert(1)", "data:text/html,hi", "//evil.example/file"]) assert.equal(documentLink("session", "result.md", href), "#");
});

test("explicit workspace directory links have no download target", () => {
  for (const href of ["../../mle/structures/", "../../mle/structures%2F", "./", "../", ".", ".."]) {
    assert.equal(documentLink("session", "results/0001/result.md", href), "", href);
  }
  assert.equal(documentLink("session", "results/0001/result.md", "https://example.org/library/"), "https://example.org/library/");
  assert.equal(documentLink("session", "results/0001/result.md", "../../mle/structures/parabens.sdf"), "/ui/artifacts/session/raw?path=mle%2Fstructures%2Fparabens.sdf");
});
