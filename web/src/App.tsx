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
 *
 * **Losing contact is the loudest thing this screen does** (#40). The failure
 * being designed against is not a console that goes blank — it is one that goes
 * on looking fine while the engine is gone, so a broken night reads as a quiet
 * one. So the moment the link is not live, three things happen at once: a red
 * banner says so and says what it is doing about it, everything the last
 * snapshot told us is dimmed and stamped with when we last heard it, and the
 * elapsed clock stops dead. Nothing on screen is left claiming to be current.
 */

import { useEffect } from "react";
import { logout } from "./api";
import { formatElapsed, useCountdown, useElapsed } from "./elapsed";
import {
  finalCountLabel,
  MODE_LABELS,
  SNAPSHOT_SCHEMA_VERSION,
  STATE_COPY,
  TERMINAL_STATES,
} from "./snapshot";
import type { CallView, ConfigView, NodeView } from "./snapshot";
import { useTelemetry } from "./telemetry";
import type { Connection, Telemetry } from "./telemetry";

export function App() {
  const telemetry = useTelemetry();
  const { connection, snapshot, stale } = telemetry;

  useEffect(() => {
    // The engine restarted (or the session expired): every Console Session
    // lives in engine memory, so there is nothing to reconnect to until
    // somebody types the password again.
    if (connection === "unauthorized") {
      window.location.assign("/login");
    }
  }, [connection]);

  return (
    <div className={stale ? "console is-stale" : "console"}>
      <Disconnected telemetry={telemetry} />
      <TopBar config={snapshot?.config ?? null} connection={connection} />
      <main className="stage">
        <Stage telemetry={telemetry} />
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

/**
 * The banner that makes a lost engine impossible to miss.
 *
 * It says three things, in the order an Operator mid-event needs them: contact
 * is gone, what is on screen is therefore old, and the Console is already
 * trying again — with the countdown to the next attempt, so nobody has to
 * wonder whether anything is happening. "Try now" is there for the case where
 * the Operator knows something the Console doesn't: they just restarted the
 * engine, or plugged the network back in.
 */
function Disconnected({ telemetry }: { telemetry: Telemetry }) {
  const { connection, retryAt, attempt, snapshot, retry } = telemetry;
  const seconds = useCountdown(connection === "lost" ? retryAt : null);

  if (connection === "live" || connection === "unauthorized") return null;
  if (connection === "connecting" && attempt === 0 && snapshot === null) return null;

  return (
    <div className="alarm">
      <span className="alarm-dot" aria-hidden="true" />
      {/* The headline is the live region, not the banner: a countdown inside an
          alert would re-announce itself to a screen reader once a second. */}
      <strong role="alert">
        {connection === "connecting" ? "Reconnecting…" : "Lost contact with the engine"}
      </strong>
      <span className="alarm-note">
        {connection === "connecting"
          ? "Nothing below is confirmed until the engine answers."
          : snapshot === null
            ? "This console has never heard from the engine. It is not an idle booth."
            : "Everything below is the last thing we heard, not what is happening now."}
      </span>
      {seconds !== null && (
        <span className="alarm-countdown">
          {seconds > 0 ? `Retrying in ${seconds}s` : "Retrying…"}
          {attempt > 1 && ` · attempt ${attempt}`}
        </span>
      )}
      <button className="ghost" onClick={retry}>
        Try now
      </button>
    </div>
  );
}

function Stage({ telemetry }: { telemetry: Telemetry }) {
  const { connection, snapshot, stale } = telemetry;

  // Nothing has ever arrived. An engine we cannot reach and an engine we have
  // not reached *yet* are different sentences, and neither is an idle booth.
  if (snapshot === null) {
    return connection === "lost" ? (
      <section className="panel panel-broken">
        <h1>No contact</h1>
        <p>
          This console has not heard from the engine. It is showing nothing —
          not an idle booth. It will keep trying.
        </p>
      </section>
    ) : (
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
      {snapshot.call === null ? (
        <Idle stale={stale} />
      ) : (
        <LiveCall call={snapshot.call} stale={stale} />
      )}
    </>
  );
}

function Idle({ stale }: { stale: boolean }) {
  return (
    <section className="panel panel-idle">
      <h1>Booth idle</h1>
      <p>
        {stale
          ? "The booth was idle when we lost the engine. Somebody may be on the phone now."
          : "Nobody is on the phone. The engine is up and watching the line."}
      </p>
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
 *
 * Off-air the same panel stops asserting. The clock freezes, the labels change
 * from what *is* to what *was*, and the whole thing is dimmed — because the one
 * reading an Operator must never take from this panel is a stale call mistaken
 * for a live one.
 */
function LiveCall({ call, stale }: { call: CallView; stale: boolean }) {
  const elapsed = useElapsed(call.started_at, call.ended_at, !stale);
  const over = TERMINAL_STATES.has(call.state);
  const { label, note } = STATE_COPY[call.state];

  return (
    <section className={`panel panel-call call-${call.state}`}>
      <p className="state-label">
        {stale && <span className="last-seen">Last seen · </span>}
        {label}
      </p>
      <h1 className="caller">{call.caller_id ?? "Number withheld"}</h1>
      <dl className="call-facts">
        <div>
          <dt>Mode</dt>
          <dd>{call.mode ? MODE_LABELS[call.mode] : "—"}</dd>
        </div>
        <div>
          <dt>{stale && !over ? "Ran to" : over ? "Lasted" : "Elapsed"}</dt>
          <dd className="elapsed">{formatElapsed(elapsed)}</dd>
        </div>
        <Attempts call={call} over={over} />
        {call.puzzle_id !== null && (
          <div>
            <dt>Puzzle</dt>
            <dd className="puzzle">{call.puzzle_id}</dd>
          </div>
        )}
      </dl>
      {call.node !== null && <Maze node={call.node} />}
      <Digits digits={call.digits} />
      <p className="state-note">
        {stale && !over
          ? "This call was in progress when the engine went out of contact. It may be long over."
          : note}
      </p>
      <p className="session">{call.session_id}</p>
    </section>
  );
}

/**
 * How close the caller is to Exile.
 *
 * The limit is the *call's* — off the Config Snapshot it picked up with — not
 * the top bar's, which is what the booth is set to now. An Operator who rotated
 * the Attempt Limit mid-call would otherwise read this panel as saying the
 * caller on the line is one wrong answer from Exile when they have three left.
 *
 * During the call it counts up; afterwards it settles to what the handler
 * actually returned, which is the number that was logged — and takes the Mode's
 * own name for it, since a finished maze call's number is rooms and not
 * attempts (`finalCountLabel`, #56). Only the live half has a limit to count
 * towards: the maze has none, so the live half of this simply does not render
 * for it, and the Maze line below carries the walk instead.
 */
function Attempts({ call, over }: { call: CallView; over: boolean }) {
  if (over) {
    return (
      <div>
        <dt>{finalCountLabel(call.mode)}</dt>
        <dd>{call.attempts}</dd>
      </div>
    );
  }
  if (call.attempt_limit === null) return null;

  const attempt = call.attempt;
  const last = attempt !== null && attempt >= call.attempt_limit;
  return (
    <div>
      <dt>Attempt</dt>
      <dd className={last ? "attempt attempt-last" : "attempt"}>
        {attempt ?? "—"} <span className="of">of {call.attempt_limit}</span>
      </dd>
    </div>
  );
}

/**
 * Where the caller is in the maze.
 *
 * Depth, not the node index: the tree is regenerated per Call Session, so the
 * index is a coordinate on a map only this call has, and the number that means
 * anything to a watching Operator is how many rooms deep they have walked. The
 * leaf gets its own treatment because it is the moment the Code is read aloud —
 * the Operator's cue that the caller is about to hang up and dial in.
 */
function Maze({ node }: { node: NodeView }) {
  return (
    <p className={node.terminal ? "maze maze-leaf" : "maze"}>
      {node.terminal ? (
        <>The voice is reading them the Code — room {node.depth} deep.</>
      ) : (
        <>
          {node.depth === 0 ? "At the mouth of the maze" : `${node.depth} rooms deep`}
          <span className="node-index"> · node {node.index}</span>
        </>
      )}
    </p>
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
