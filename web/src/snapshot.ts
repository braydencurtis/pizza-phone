/**
 * The wire shape the telemetry socket pushes — the mirror of engine/snapshot.py.
 *
 * The console is fed whole-state snapshots, never deltas: every message is the
 * complete truth, so there is no reducer here to drift out of step with the
 * engine. `schema` is how a console notices it is older than the engine it is
 * talking to instead of quietly rendering nonsense.
 */

/** 3: the call view gained its live progress — attempt, node, puzzle (#37). */
export const SNAPSHOT_SCHEMA_VERSION = 3;

export type Mode = "tweeted" | "puzzle" | "roguelike";
/**
 * How a Call Session ended, as the store records it.
 *
 * `dropped` is the one no mode handler returns: the engine ended the call
 * itself after an exception, with the caller still on the line. It is kept out
 * of `hangup` so a failure of ours is never counted as a caller walking away
 * (#50).
 */
export type Outcome = "succeed" | "fail" | "exile" | "hangup" | "dropped";

/**
 * Where a Call Session has got to.
 *
 * The terminal states are kept apart deliberately. `handed_off` is a **win**:
 * the channel has left the Call Engine for the success dialplan, so everything
 * after it — the Upstairs Phone ringing, the Operator answering, the pizza —
 * is invisible from here. If a win rendered like a hangup the Operator would
 * learn to distrust the panel, so the two never share a look.
 */
export type CallState =
  | "answering"
  | "in_mode"
  | "handed_off"
  | "exiled"
  | "hung_up"
  | "dropped";

export const TERMINAL_STATES: ReadonlySet<CallState> = new Set<CallState>([
  "handed_off",
  "exiled",
  "hung_up",
  "dropped",
]);

/** What the booth is set to right now (Global Config). */
export interface ConfigView {
  mode: Mode;
  code: string;
  /**
   * The booth's Attempt Limit *now*. Not the one the live caller is judged
   * against — that is `CallView.attempt_limit`, off their frozen Config
   * Snapshot, and the two differ for the length of any call in progress when
   * an Operator changes the setting.
   */
  attempt_limit: number;
  upstream_extension: string;
}

/** Where the caller has got to in the Roguelike Phone-Tree. */
export interface NodeView {
  /** Index into a tree regenerated for this Call Session — meaningless elsewhere. */
  index: number;
  /** Rooms walked through. The readable half of a position. */
  depth: number;
  /** The leaf: where the Code is read aloud. */
  terminal: boolean;
}

/** The live Call Session, as much of it as the engine knows so far. */
export interface CallView {
  session_id: string;
  state: CallState;
  mode: Mode | null;
  caller_id: string | null;
  /** When the call was picked up. The browser advances the clock from here. */
  started_at: string;
  /** `null` while the call is live; the moment it ended once it is over. */
  ended_at: string | null;
  /** The most recent digits dialled, oldest dropped past the engine's cap. */
  digits: string;
  /**
   * Which attempt the caller is on right now, or `null` before the first.
   *
   * Distinct from `attempts`, which is the *final* count and stays 0 until the
   * mode handler returns. One is shown during the call and the other after it.
   */
  attempt: number | null;
  /** The Attempt Limit **this call** is judged against, off its Config Snapshot. */
  attempt_limit: number | null;
  /** `null` unless this is a Roguelike Phone-Tree session. */
  node: NodeView | null;
  /** The riddle drawn from the Puzzle Pool; `null` outside Audio Puzzle Mode. */
  puzzle_id: string | null;
  attempts: number;
  outcome: Outcome | null;
}

export interface Snapshot {
  schema: number;
  config: ConfigView;
  /** `null` is the idle marker: the booth is up, nobody is on the phone. */
  call: CallView | null;
}

export const MODE_LABELS: Record<Mode, string> = {
  tweeted: "Tweeted",
  puzzle: "Audio Puzzle",
  roguelike: "Roguelike Phone-Tree",
};

/** How each state reads on the panel: its headline, and what it actually means. */
export const STATE_COPY: Record<CallState, { label: string; note: string }> = {
  answering: {
    label: "Answering",
    note: "The channel is up. Taking the Config Snapshot this call is judged against.",
  },
  in_mode: {
    label: "On the line",
    note: "The caller is in the game.",
  },
  handed_off: {
    label: "Handed Off",
    note:
      "They won. The channel has left the Call Engine for the success dialplan — " +
      "the Upstairs Phone is ringing, and everything after that is off this panel.",
  },
  exiled: {
    label: "Exiled",
    note: "They burned the Attempt Limit and heard the Exile message.",
  },
  hung_up: {
    label: "Hung up",
    note: "The call ended without a win. Nobody is on the line.",
  },
  dropped: {
    label: "Engine dropped the call",
    note: "The engine ended this call itself — not the caller. Check the engine log.",
  },
};
