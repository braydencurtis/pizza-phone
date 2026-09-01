/**
 * The cockpit: one screen, the live call in the middle of it.
 *
 * The layout follows CONTEXT.md ("Console layout") — Global Config in a top
 * bar, the current call dominating the centre, powers and the past-calls drawer
 * to come. This ticket fills in the two states the engine can currently
 * report: a booth with a call on it, and an idle one. Both must be
 * unmistakable, and neither may be confusable with a console that has lost the
 * engine — hence the connection pill sitting apart from the call itself.
 */

import { useEffect } from "react";
import { logout } from "./api";
import { MODE_LABELS, SNAPSHOT_SCHEMA_VERSION } from "./snapshot";
import type { CallView, ConfigView, Snapshot } from "./snapshot";
import { useTelemetry } from "./telemetry";
import type { Connection } from "./telemetry";

export function App() {
  const { connection, snapshot } = useTelemetry();

  useEffect(() => {
    if (connection === "unauthorized") {
      window.location.assign("/login");
    }
  }, [connection]);

  return (
    <div className="console">
      <TopBar config={snapshot?.config ?? null} connection={connection} />
      <main className="stage">
        <Stage connection={connection} snapshot={snapshot} />
      </main>
    </div>
  );
}

function TopBar({
  config,
  connection,
}: {
  config: ConfigView | null;
  connection: Connection;
}) {
  return (
    <header className="topbar">
      <span className="brand">Pizza Phone</span>
      <dl className="globals">
        <div>
          <dt>Mode</dt>
          <dd>{config ? MODE_LABELS[config.mode] : "—"}</dd>
        </div>
        <div>
          <dt>Code</dt>
          <dd className="code">{config ? config.code : "————"}</dd>
        </div>
        <div>
          <dt>Attempt Limit</dt>
          <dd>{config ? config.attempt_limit : "—"}</dd>
        </div>
      </dl>
      <ConnectionPill connection={connection} />
      <button
        className="ghost"
        onClick={() => logout().then(() => window.location.assign("/login"))}
      >
        Log out
      </button>
    </header>
  );
}

const CONNECTION_LABELS: Record<Connection, string> = {
  connecting: "Connecting…",
  live: "Engine live",
  lost: "Engine lost",
  unauthorized: "Signed out",
};

function ConnectionPill({ connection }: { connection: Connection }) {
  return (
    <span className={`pill pill-${connection}`}>
      <span className="dot" aria-hidden="true" />
      {CONNECTION_LABELS[connection]}
    </span>
  );
}

function Stage({
  connection,
  snapshot,
}: {
  connection: Connection;
  snapshot: Snapshot | null;
}) {
  if (connection === "lost") {
    return (
      <section className="panel panel-broken">
        <h1>Engine lost</h1>
        <p>
          The telemetry socket closed. This console is showing nothing, not an
          idle booth — reload to reattach.
        </p>
        <button onClick={() => window.location.reload()}>Reload</button>
      </section>
    );
  }

  if (snapshot === null) {
    return (
      <section className="panel panel-waiting">
        <h1>Attaching…</h1>
        <p>Waiting for the engine's first snapshot.</p>
      </section>
    );
  }

  return (
    <>
      {snapshot.schema !== SNAPSHOT_SCHEMA_VERSION && (
        <p className="schema-warning">
          The engine is speaking snapshot schema {snapshot.schema}; this console
          knows {SNAPSHOT_SCHEMA_VERSION}. Some of what follows may be wrong —
          redeploy the console bundle.
        </p>
      )}
      {snapshot.call === null ? <Idle /> : <LiveCall call={snapshot.call} />}
    </>
  );
}

function Idle() {
  return (
    <section className="panel panel-idle">
      <h1>Booth idle</h1>
      <p>Nobody is on the phone. The engine is up and watching the line.</p>
    </section>
  );
}

/**
 * A call is live — and that is deliberately all this says.
 *
 * The facts an Operator wants about a call (caller, elapsed time, attempt and
 * node as they happen) arrive with the state vocabulary in #36, and belong in
 * one shape with it rather than a preview here that #36 would have to unpick.
 * Until then the console refuses to imply the booth is idle when it isn't.
 */
function LiveCall({ call }: { call: CallView }) {
  return (
    <section className="panel panel-live">
      <h1>Call in progress</h1>
      <p>Live telemetry lands here next.</p>
      <p className="session">{call.session_id}</p>
    </section>
  );
}
