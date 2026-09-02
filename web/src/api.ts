/** The engine's HTTP surface, such as it is so far. */

/** What asking the engine about this browser's session can tell us. */
export type SessionCheck = "ok" | "unauthorized" | "unreachable";

export async function login(password: string): Promise<boolean> {
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  return response.ok;
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST" });
}

/**
 * Is this browser still logged in? The reconnection probe (#40).
 *
 * The WebSocket API reports a refused upgrade and an engine that is not
 * listening identically — a bare close with no detail — but the two want
 * opposite responses. Console Sessions live in engine memory, so a restart has
 * forgotten every one of them and the Operator needs the password box; an
 * engine that is merely down wants patience. Only HTTP can tell them apart, so
 * a socket that closes without ever having opened asks here first.
 */
export async function checkSession(): Promise<SessionCheck> {
  try {
    const response = await fetch("/api/session", { cache: "no-store" });
    if (response.status === 401) return "unauthorized";
    return response.ok ? "ok" : "unreachable";
  } catch {
    // Nothing answered: the engine is down, or this laptop is off the network.
    return "unreachable";
  }
}
