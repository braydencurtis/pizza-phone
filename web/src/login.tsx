/**
 * The password prompt — the one page served to a browser with no session.
 *
 * One shared password, so there is no username field and no account to name.
 * A wrong password says so and nothing more: there is no user to be specific
 * about.
 */

import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { login } from "./api";
import "./styles.css";

function Login() {
  const [password, setPassword] = useState("");
  const [refused, setRefused] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setRefused(false);
    const ok = await login(password);
    setBusy(false);
    if (ok) {
      window.location.assign("/");
    } else {
      setRefused(true);
      setPassword("");
    }
  }

  return (
    <main className="login">
      <form className="panel" onSubmit={submit}>
        <h1>Pizza Phone</h1>
        <p>Operator Console</p>
        <input
          type="password"
          value={password}
          autoFocus
          autoComplete="current-password"
          placeholder="Shared password"
          onChange={(event) => setPassword(event.target.value)}
        />
        <button type="submit" disabled={busy || password === ""}>
          {busy ? "Checking…" : "Enter"}
        </button>
        {refused && <p className="refused">That password is not it.</p>}
      </form>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Login />
  </StrictMode>,
);
