import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

// Local-dev parity with the nginx orchestrator routing (docker/nginx-ui.conf):
// /agents/* is dispatched by the X-Holo-Orchestrator header. A claude_code run
// goes to the host-side cc_runner (:5061); everything else (default/codearkt)
// goes to the CodeArkt server (:5055). vite's built-in proxy binds a target once
// at setup and can't switch it per request header, so we reuse the SAME bundled
// http-proxy instance (captured in `configure`) and pick the target per request.
// vite already wires an `error` handler on that instance, so when the runner is
// down the client gets a non-200 -- the same failure the UI surfaces as an
// errored run (SC8), mirroring nginx.
const CODEARKT_TARGET = "http://127.0.0.1:5055";
const CC_RUNNER_TARGET = "http://127.0.0.1:5061";

// HttpProxy.Server, derived from the exported ProxyOptions so no @types/node is
// needed (that package is only an uninstalled optional peer of vite).
type AgentsProxy = Parameters<NonNullable<ProxyOptions["configure"]>>[0];

let agentsProxy: AgentsProxy | undefined;

// Minimal view of the request fields we read. Node's http types aren't installed
// (@types/node is an uninstalled optional peer), so we narrow locally instead of
// depending on them.
interface AgentsRequestView {
  url?: string;
  headers: Record<string, string | string[] | undefined>;
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: "holo-orchestrator-agents-proxy",
      configureServer(server) {
        // Runs before vite's internal proxy middleware; intercept /agents and
        // dispatch through the captured proxy with a per-request target. Falls
        // through to the default /agents proxy below if the instance isn't ready.
        server.middlewares.use((req, res, next) => {
          const view = req as unknown as AgentsRequestView;
          if (!view.url || view.url.indexOf("/agents") !== 0 || !agentsProxy) {
            next();
            return;
          }
          const orchestrator = view.headers["x-holo-orchestrator"];
          const target = orchestrator === "claude_code" ? CC_RUNNER_TARGET : CODEARKT_TARGET;
          agentsProxy.web(req, res, { target, changeOrigin: true });
        });
      }
    }
  ],
  server: {
    port: 5173,
    proxy: {
      "/agents": {
        target: CODEARKT_TARGET,
        changeOrigin: true,
        // Capture the bundled http-proxy instance for the header-aware dispatch
        // above; this entry also stays the safe fallback for the default backend.
        configure: (proxy) => {
          agentsProxy = proxy;
        }
      },
      "/ui/ws/": { target: "http://127.0.0.1:5058", ws: true },
      "/ui": "http://127.0.0.1:5058"
    }
  }
});
