/**
 * The wire shape the telemetry socket pushes — the mirror of engine/snapshot.py.
 *
 * The console is fed whole-state snapshots, never deltas: every message is the
 * complete truth, so there is no reducer here to drift out of step with the
 * engine. `schema` is how a console notices it is older than the engine it is
 * talking to instead of quietly rendering nonsense.
 */

export const SNAPSHOT_SCHEMA_VERSION = 1;

export type Mode = "tweeted" | "puzzle" | "roguelike";
export type Outcome = "succeed" | "fail" | "exile" | "hangup";

/** What the booth is set to right now (Global Config). */
export interface ConfigView {
  mode: Mode;
  code: string;
  attempt_limit: number;
  upstream_extension: string;
}

/** The live Call Session, as much of it as the engine knows so far. */
export interface CallView {
  session_id: string;
  mode: Mode | null;
  caller_id: string | null;
  started_at: string;
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
