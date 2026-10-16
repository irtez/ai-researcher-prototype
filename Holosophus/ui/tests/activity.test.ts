import assert from "node:assert/strict";
import test from "node:test";
import { buildActivity, buildActivityTree, type ActivityEvent } from "../src/activity/activity.ts";

test("background messages stay in the correct invocation while manager continues outside", () => {
  const events: ActivityEvent[] = [
    { kind: "agent_start", id: "e1", at: "21:39:00", invocationId: "a1", role: "mle_solver", label: "First", task: "Mass" },
    { kind: "message", id: "m1", at: "21:39:01", text: "Manager continues" },
    { kind: "agent_start", id: "e2", at: "21:39:02", invocationId: "a2", role: "mle_solver", label: "Second", task: "Check" },
    { kind: "message", id: "m2", at: "21:39:03", invocationId: "a1", text: "First still working" },
    { kind: "message", id: "m3", at: "21:39:04", invocationId: "a2", text: "Second working" },
    { kind: "agent_end", id: "e3", at: "21:40:00", invocationId: "a1", status: "complete" }
  ];
  const tree = buildActivityTree(events);
  assert.deepEqual(tree.map(n => n.id), ["a1", "m1", "a2"]);
  const [first, manager, second] = tree;
  assert.equal(first.kind, "agent");
  assert.equal(second.kind, "agent");
  if (first.kind !== "agent" || second.kind !== "agent") return;
  assert.deepEqual(first.children.map(n => n.id), ["m2"]);
  assert.deepEqual(second.children.map(n => n.id), ["m3"]);
  assert.equal(first.status, "complete");
  assert.equal(second.status, "running");
  assert.equal(manager.kind, "message");
  const reverseCompletion: ActivityEvent[] = [
    ...events.slice(0, -1),
    { kind: "agent_end", id: "e4", at: "21:39:40", invocationId: "a2", status: "complete" }
  ];
  const views = buildActivity(reverseCompletion).agents;
  assert.deepEqual(views.map(view => view.id), ["a1", "a2"]);
  assert.deepEqual(views.map(view => view.status), ["running", "complete"]);
});

test("an out-of-order tool result updates its call and keeps the real error visible", () => {
  const events: ActivityEvent[] = [
    { kind: "tool_call", id: "e1", at: "21:39:00", callId: "c1", name: "Bash", summary: "First", input: {} },
    { kind: "tool_call", id: "e2", at: "21:39:01", callId: "c2", name: "Read", summary: "Second", input: {} },
    { kind: "tool_result", id: "e3", at: "21:39:02", callId: "c2", output: "File data" },
    { kind: "tool_result", id: "e4", at: "21:39:03", callId: "c1", output: "Permission denied", isError: true }
  ];
  const tree = buildActivityTree(events);
  assert.equal(tree.length, 2);
  assert.equal(tree[0].kind, "tool");
  assert.equal(tree[1].kind, "tool");
  if (tree[0].kind !== "tool" || tree[1].kind !== "tool") return;
  assert.equal(tree[0].status, "error");
  assert.equal(tree[0].output, "Permission denied");
  assert.equal(tree[1].output, "File data");
});
