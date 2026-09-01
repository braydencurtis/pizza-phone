/**
 * The cockpit: one screen, the live call in the middle of it.
 *
 * The layout follows CONTEXT.md ("Console layout") — Global Config in a top
 * bar, the current call dominating the centre, powers and the past-calls drawer
 * to come. Three things must be unmistakable from across a room: an idle booth,
 * a live call, and a console that has lost the engine — hence the connection
 * pill sitting apart from the call itself. Within a call, the terminal states
 * are held apart just as firmly: a Handed Off win is a different colour and a
 * different sentence from a hangup, because a panel that blurs them is a panel
 * the Operator stops believing.
 */

import { useEffect } from "react";
import { logout } from "./api";
import { formatElapsed, useElapsed } from "./elapsed";
import {
  MODE_LABELS,
  SNAPSHOT_SCHEMA_VERSION,
  STATE_COPY,
  TERMINAL_STATES,
} from "./snapshot";
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
 * The call, as it happens: who is on, which game they were given, how long they
 * have been on it, and what they are dialling.
 *
 * The state headline is the load-bearing part. Every terminal state gets its own
 * colour and its own sentence — above all Handed Off, which is a *win* and must
 * never be mistaken for the caller hanging up. The panel also says out loud
 * that a Handed Off call has left the engine, so nobody reads the silence that
 * follows as the story ending.
 */
function LiveCall({ call }: { call: CallView }) {
  const elapsed = useElapsed(call.started_at, call.ended_at);
  const over = TERMINAL_STATES.has(call.state);
  const { label, note } = STATE_COPY[call.state];

  return (
    <section className={`panel panel-call call-${call.state}`}>
      <p className="state-label">{label}</p>
      <h1 className="caller">{call.caller_id ?? "Number withheld"}</h1>
      <dl className="call-facts">
        <div>
          <dt>Mode</dt>
          <dd>{call.mode ? MODE_LABELS[call.mode] : "—"}</dd>
        </div>
        <div>
          <dt>{over ? "Lasted" : "Elapsed"}</dt>
          <dd className="elapsed">{formatElapsed(elapsed)}</dd>
        </div>
        {/* The engine only learns the attempt count when the mode handler
            returns, so a live call would show a permanent 0 and then jump — a
            stale display of exactly the kind ADR-0003 rejects deltas to avoid.
            The live counter arrives with the CallObserver seam. */}
        {over && (
          <div>
            <dt>Attempts</dt>
            <dd>{call.attempts}</dd>
          </div>
        )}
      </dl>
      <Digits digits={call.digits} />
      <p className="state-note">{note}</p>
      <p className="session">{call.session_id}</p>
    </section>
  );
}

/** The keys the caller is pressing, as they press them. */
function Digits({ digits }: { digits: string }) {
  return (
    <div className="digits" aria-label="Digits dialled">
      {digits === "" ? (
        <span className="digits-empty">nothing dialled yet</span>
      ) : (
        Array.from(digits).map((digit, index) => (
          <span className="digit" key={`${index}-${digit}`}>
            {digit}
          </span>
        ))
      )}
    </div>
  );
}
