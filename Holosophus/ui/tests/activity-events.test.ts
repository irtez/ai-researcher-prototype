import assert from "node:assert/strict";
import test from "node:test";
import { activityFromEvents, activityFromState } from "../src/activity/fromEvents.ts";
import { buildActivity } from "../src/activity/activity.ts";
import { iterationLiveState } from "../src/liveState.ts";

const event = (sequence, kind, fields = {}, text = null, role = "manager") => ({
  session_id: "test", iteration_id: "0001", sequence, received_at: `2026-09-09T18:39:${String(sequence).padStart(2, "0")}Z`,
  agent_name: role, event_type: "observation", content: text,
  activity: { version: 1, kind, ...fields }
});
test("persisted events keep replies with the exact call; reload and terminal states do not invent success", () => {
  const records = [
    event(1, "agent_start", { invocationId: "a", role: "mle_solver", task: "First", prompt: "Compute mass" }),
    event(2, "agent_start", { invocationId: "b", role: "mle_solver", task: "Second" }),
    { ...event(3, "message", { invocationId: "a", text: "Calculating" }), event_type: "output", content: "Calculating" },
    event(4, "tool_call", { invocationId: "a", callId: "c", name: "Bash", input: { command: "check" } }),
    event(5, "tool_result", { invocationId: "a", callId: "c", output: "Denied", isError: true }),
    event(6, "agent_update", { invocationId: "a", background: true }),
    event(7, "agent_end", { invocationId: "b", status: "complete", summary: "Second done" }),
  ];
  const live = iterationLiveState({ kind: "initial", session_id: "test", status: "complete", events: records });
  assert.equal(live.recordedEvents.length, records.length);
  const adapted = activityFromState(live);
  assert.equal(adapted.legacy, false);
  const { agents, roots } = buildActivity(adapted.events, live.status);
  assert.deepEqual(agents.map(a => a.id), ["a", "b"]);
  assert.equal(agents[0].status, "unknown");
  assert.equal(agents[0].prompt, "Compute mass");
  assert.equal(agents[0].background, true);
  assert.equal(agents[0].children[0].text, "Calculating");
  assert.equal(agents[0].children[1].output, "Denied");
  assert.equal(agents[0].children[1].status, "error");
  assert.equal(agents[1].summary, "Second done");
  assert.equal(roots.length, 2);
});

test("partial history exposes missing invocation/call metadata without dropping actual responses", () => {
  const { events } = activityFromEvents([
    event(1, "message", { invocationId: "absent", text: "Retained" }),
    event(2, "tool_result", { invocationId: "absent", callId: "lost-call", output: "Actual result", truncated: true }),
    event(3, "agent_end", { invocationId: "absent", status: "cancelled" }),
  ]);
  const { agents } = buildActivity(events);
  assert.equal(agents[0].incomplete, true);
  assert.equal(agents[0].status, "cancelled");
  assert.equal(agents[0].children[1].incomplete, true);
  assert.equal(agents[0].children[1].output, "Actual result");
  assert.equal(agents[0].children[1].truncated, true);
});

test("legacy repeated roles aggregate honestly, observations never become fabricated successful tool calls", () => {
  const records = ["agent_start", "output", "observation", "agent_end", "agent_start", "output"].map((kind, index) => ({
    session_id: "old", sequence: index, agent_name: "mle_solver", event_type: kind,
    content: kind === "output" ? `Message ${index}` : kind === "observation" ? "Bash: check" : null
  }));
  const { events, legacy } = activityFromEvents(records);
  const { agents } = buildActivity(events, "complete");
  assert.equal(legacy, true);
  assert.equal(agents.length, 1);
  assert.equal(agents[0].legacy, true);
  assert.equal(agents[0].status, "unknown");
  assert.deepEqual(agents[0].children.map(n => n.kind), ["message", "note", "message"]);
  assert.equal(agents[0].at, "");
});

test("duplicate delivery is ignored and an early child message stays with its later start record", () => {
  const early = event(1, "message", { invocationId: "a", text: "First" });
  const { events } = activityFromEvents([early, early, event(2, "agent_start", { invocationId: "a", role: "writer" })]);
  const { agents, roots } = buildActivity(events);
  assert.equal(agents.length, 1);
  assert.equal(roots.length, 1);
  assert.equal(agents[0].children.length, 1);
});

test("partial invocation tabs do not move when later starts arrive", () => {
  const records = [event(1, "message", { invocationId: "partial", text: "Retained" }),
    event(2, "agent_start", { invocationId: "later", role: "writer" })];
  assert.deepEqual(buildActivity(activityFromEvents(records.slice(0, 1)).events).agents.map(a => a.id), ["partial"]);
  assert.deepEqual(buildActivity(activityFromEvents(records).events).agents.map(a => a.id), ["partial", "later"]);
});

test("old browser snapshots retain their planning output", () => {
  const { events, legacy } = activityFromState({ sessionId: "old", rawPlan: "Saved planning output", transcriptByAgent: {}, toolMarkers: [] });
  assert.equal(legacy, true);
  assert.equal(events[0].kind, "note");
  assert.equal(events[0].text, "Saved planning output");
});

test("limited agents resume in the same tab with a chronological note and complete after new work", () => {
  const records = [
    event(1, "agent_start", { invocationId: "writer", role: "writer", task: "Write paper" }),
    event(2, "message", { invocationId: "writer", text: "Initial work" }),
    event(3, "agent_end", { invocationId: "writer", status: "limited", summary: "Turn limit; PDF still missing", truncated: true }),
    event(4, "agent_start", { invocationId: "reviewer", role: "reviewer" }),
    event(5, "agent_resume", { invocationId: "writer", background: true }),
    event(6, "message", { invocationId: "writer", text: "Continuing compilation" }),
    event(7, "tool_call", { invocationId: "writer", callId: "compile", name: "Bash", input: { command: "compile" } }),
    event(8, "tool_result", { invocationId: "writer", callId: "compile", output: "paper.pdf built" }),
    event(9, "agent_end", { invocationId: "writer", status: "complete", summary: "Paper built" }),
  ];
  const stopped = buildActivity(activityFromEvents(records.slice(0, 3)).events).agents[0];
  assert.equal(stopped.status, "limited");
  assert.equal(stopped.finishedAt, records[2].received_at);
  const resumed = buildActivity(activityFromEvents(records.slice(0, 7)).events);
  assert.deepEqual(resumed.agents.map(a => a.id), ["writer", "reviewer"]);
  const writer = resumed.agents[0];
  assert.equal(writer.status, "running");
  assert.equal(writer.background, true);
  assert.equal(writer.finishedAt, undefined);
  assert.equal(writer.summary, undefined);
  assert.equal(writer.truncated, undefined);
  assert.deepEqual(writer.children.map(n => n.kind), ["message", "note", "message", "tool"]);
  assert.equal(writer.children[1].text, "Turn limit; PDF still missing");
  assert.equal(writer.children[1].at, records[2].received_at);
  assert.equal(writer.children[3].status, "running");
  const done = buildActivity(activityFromEvents([...records, ...records]).events, "complete");
  assert.deepEqual(done.agents.map(a => a.id), ["writer", "reviewer"]);
  assert.equal(done.roots.length, 2);
  assert.equal(done.agents[0].status, "complete");
  assert.equal(done.agents[0].summary, "Paper built");
  assert.equal(done.agents[0].children.length, 4);
  assert.equal(done.agents[0].children[3].output, "paper.pdf built");
  assert.equal(done.agents[0].children[3].status, "complete");
  assert.deepEqual(buildActivity([...activityFromEvents(records).events, ...activityFromEvents(records).events], "complete"), done);
});

test("resuming does not turn unfinished tools from a stopped attempt back into running tools", () => {
  const { events } = activityFromEvents([
    event(1, "agent_start", { invocationId: "a", role: "writer" }),
    event(2, "tool_call", { invocationId: "a", callId: "old", name: "Bash" }),
    event(3, "agent_end", { invocationId: "a", status: "limited" }),
    event(4, "agent_resume", { invocationId: "a" }),
    event(5, "tool_call", { invocationId: "a", callId: "new", name: "Bash" }),
  ]);
  const agent = buildActivity(events).agents[0];
  assert.equal(agent.status, "running");
  assert.deepEqual(agent.children.map(node => node.status), ["unknown", "running"]);
});
