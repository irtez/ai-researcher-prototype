/** An optional notification, never a source of UI state or an execution command. */
export function watchIterationCompletion(
  sessionId: string,
  iterationId: string,
  identity: { client_id: string; token: string | null },
  onComplete: () => void,
  origin = window.location.origin,
  connect: (url: string) => WebSocket = (url) => new WebSocket(url)
): () => void {
  let socket: WebSocket;
  try {
    const url = new URL(`/ui/ws/sessions/${encodeURIComponent(sessionId)}/iterations/${encodeURIComponent(iterationId)}/completion`, origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    socket = connect(url.toString());
  } catch {
    return () => {}; // HTTP polling already runs independently.
  }
  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    socket.close();
  };
  socket.onopen = () => {
    if (stopped) return;
    try { socket.send(JSON.stringify(identity)); } catch { stop(); }
  };
  socket.onmessage = (event) => {
    if (stopped) return;
    let complete = false;
    try {
      const data = JSON.parse(event.data);
      complete = data?.iteration_id === iterationId && ["complete", "error", "cancelled", "unknown"].includes(data?.status);
    } catch { /* Malformed notifications also leave HTTP polling in charge. */ }
    stop();
    if (complete) onComplete();
  };
  socket.onerror = stop;
  socket.onclose = () => { stopped = true; };
  return stop;
}
