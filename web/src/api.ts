/** The engine's HTTP surface, such as it is in this ticket. */

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
