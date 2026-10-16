// App-level team login (docs/plans/2026-07-05-ui-login-and-per-account-queue.md).
//
// Anonymous users can browse the UI; starting a run requires a login. On a
// successful POST /agents/login the server returns a short-lived HMAC-signed
// bearer token, which we persist in localStorage and send as
// `Authorization: Bearer` on the run/cancel calls the backend hard-verifies.
// The username also becomes the identity (`X-Holo-Client`, via clientId.ts) so
// the session list / owner isolation key on the login and are durable across
// browsers -- this is the real `user_id` the anonymous client-id was a seam for.

const TOKEN_STORAGE_KEY = "holosophus.ui.authToken";
const USERNAME_STORAGE_KEY = "holosophus.ui.username";

let cachedToken: string | null | undefined;
let cachedUsername: string | null | undefined;

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // localStorage unavailable (private mode): keep only the in-memory cache.
  }
}

export function getAuthToken(): string | null {
  if (cachedToken === undefined) {
    cachedToken = readStorage(TOKEN_STORAGE_KEY);
  }
  return cachedToken ?? null;
}

export function getUsername(): string | null {
  if (cachedUsername === undefined) {
    cachedUsername = readStorage(USERNAME_STORAGE_KEY);
  }
  return cachedUsername ?? null;
}

export function isLoggedIn(): boolean {
  return Boolean(getAuthToken() && getUsername());
}

interface LoginResponse {
  token: string;
  username: string;
  expires_in?: number;
}

export async function login(username: string, password: string): Promise<string> {
  // POST /agents/login is served by the CodeArkt app (default orchestrator);
  // nginx already proxies /agents/*, so no routing header is needed here.
  const response = await fetch("/agents/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) {
    let message = "Неверный логин или пароль";
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail || message;
    } catch {
      // Keep the default message.
    }
    throw new Error(message);
  }
  const data = (await response.json()) as LoginResponse;
  cachedToken = data.token;
  cachedUsername = data.username;
  writeStorage(TOKEN_STORAGE_KEY, data.token);
  writeStorage(USERNAME_STORAGE_KEY, data.username);
  return data.username;
}

export function logout(): void {
  cachedToken = null;
  cachedUsername = null;
  writeStorage(TOKEN_STORAGE_KEY, null);
  writeStorage(USERNAME_STORAGE_KEY, null);
}

// Bearer header for the run/cancel calls; empty object when logged out so the
// request still goes through (the backend only rejects if a login is required).
export function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
