import assert from "node:assert/strict";
import test from "node:test";
import { watchIterationCompletion } from "../src/iterationCompletion.ts";

function fixture(origin = "http://localhost:5062") {
  const sent: string[] = [];
  let closed = 0, refreshed = 0, connections = 0, url = "";
  const socket = {
    onopen: () => {}, onmessage: (_event: { data: string }) => {}, onerror: () => {}, onclose: () => {},
    send: (data: string) => { sent.push(data); }, close: () => { closed++; }
  };
  const stop = watchIterationCompletion("session name", "0002", { client_id: "owner", token: "secret" },
    () => { refreshed++; }, origin, (value) => { connections++; url = value; return socket as unknown as WebSocket; });
  return { socket, stop, sent, state: () => ({ closed, refreshed, connections, url }) };
}

test("completion refreshes once, only for the subscribed iteration; identity stays out of URL", () => {
  const f = fixture("https://researcher.example");
  assert.equal(f.state().url, "wss://researcher.example/ui/ws/sessions/session%20name/iterations/0002/completion");
  f.socket.onopen();
  assert.deepEqual(JSON.parse(f.sent[0]), { client_id: "owner", token: "secret" });
  f.socket.onmessage({ data: '{"iteration_id":"0002","status":"complete"}' });
  f.socket.onmessage({ data: '{"iteration_id":"0002","status":"complete"}' });
  assert.equal(f.state().refreshed, 1);
  assert.equal(f.state().closed, 1);
  assert.equal(f.state().connections, 1);
});

test("malformed, stale and nonterminal messages never mutate UI state", () => {
  for (const data of ["not JSON", "null", '{"iteration_id":"0001","status":"complete"}', '{"iteration_id":"0002","status":"running"}']) {
    const f = fixture();
    f.socket.onmessage({ data });
    assert.equal(f.state().refreshed, 0);
    assert.equal(f.state().closed, 1);
  }
});

test("failure, disconnect and cleanup do not reconnect or refresh", () => {
  for (const action of ["error", "close", "cleanup"]) {
    const f = fixture();
    if (action === "error") f.socket.onerror();
    if (action === "close") f.socket.onclose();
    if (action === "cleanup") f.stop();
    f.socket.onopen();
    f.socket.onmessage({ data: '{"iteration_id":"0002","status":"complete"}' });
    assert.equal(f.state().refreshed, 0);
    assert.equal(f.state().connections, 1);
    assert.deepEqual(f.sent, []);
  }
  assert.doesNotThrow(() => watchIterationCompletion("s", "i", { client_id: "o", token: null },
    () => assert.fail("must not refresh"), "http://localhost", () => { throw new Error("WS unavailable"); })());
});
