// Browser-scoped identity for owner isolation of the session list.
//
// When the user is logged in (app-level team auth), the identity IS the
// authenticated username, so "my sessions" is durable across browsers and keyed
// on the real login. Logged out, we fall back to an anonymous random client id
// minted on first visit and persisted in localStorage. Either way it is sent as
// the `X-Holo-Client` header so ui-api scopes the session list to this owner.
// (The hard boundary -- who may start a run + the per-account quota -- is the
// verified bearer token, not this header; see api/auth.ts.)

import { getUsername } from "./auth";

const CLIENT_ID_STORAGE_KEY = "holosophus.ui.clientId";
// Mirrored into a cookie because artifacts are opened *by URL* -- <iframe src>
// for a PDF, <img src> for a figure, a download link. Those are browser-driven
// loads that cannot carry a custom header, so the identity has to travel
// somewhere the browser sends on its own. ui-api falls back to this cookie when
// X-Holo-Client is absent; same value, same ownership check.
const CLIENT_ID_COOKIE = "holo_client";

let cachedClientId: string | null = null;

function syncClientCookie(clientId: string): void {
  try {
    // Session cookie (no Max-Age) with SameSite=Strict: it is a same-origin
    // ownership tag, never a credential, and must not ride cross-site requests.
    document.cookie = `${CLIENT_ID_COOKIE}=${encodeURIComponent(clientId)}; path=/; SameSite=Strict`;
  } catch {
    // Non-browser or cookie-less context: header-based access still works.
  }
}

function generateClientId(): string {
  const cryptoObj = typeof crypto !== "undefined" ? crypto : undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID (older/embedded webviews).
  return `cid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function getClientId(): string {
  // A logged-in user's identity is their login -- checked live (not cached) so
  // logging in/out immediately re-scopes the session list without a reload.
  const username = getUsername();
  if (username) {
    // Re-synced every call, not just on mint: logging in/out changes the
    // identity, and a stale cookie would point at the previous owner.
    syncClientCookie(username);
    return username;
  }
  if (cachedClientId) {
    syncClientCookie(cachedClientId);
    return cachedClientId;
  }
  try {
    const stored = window.localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    if (stored) {
      cachedClientId = stored;
      syncClientCookie(stored);
      return stored;
    }
    const generated = generateClientId();
    window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, generated);
    cachedClientId = generated;
    syncClientCookie(generated);
    return generated;
  } catch {
    // localStorage unavailable (private mode / SSR): keep a stable in-memory id
    // for this page session so the header is still consistent across requests.
    if (!cachedClientId) {
      cachedClientId = generateClientId();
    }
    syncClientCookie(cachedClientId);
    return cachedClientId;
  }
}
