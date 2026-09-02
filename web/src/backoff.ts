/**
 * How long to wait before trying the telemetry socket again.
 *
 * Two failures are being balanced. Retrying instantly forever turns a
 * restarting engine into a console hammering a port that isn't listening yet;
 * backing off without a cap turns a console left open overnight into one that
 * takes twenty minutes to notice the engine came back. So the wait doubles from
 * near-zero and stops at {@link MAX_RETRY_MS} — a console is never more than
 * that far behind a healthy engine.
 *
 * The jitter is for the room. Several browsers watch the same booth (CONTEXT.md,
 * "Console clients"), and an engine restart drops all of them at the same
 * instant; without jitter they would then reconnect in lockstep forever, in one
 * thundering clump per retry.
 */

/** The first retry is fast: most drops are a blip, and a blip should not show. */
export const FIRST_RETRY_MS = 500;

/** The ceiling. A console that has given up on the engine still checks this often. */
export const MAX_RETRY_MS = 15_000;

/** How far either side of the base the jitter may pull a delay: ±25%. */
const JITTER = 0.25;

/**
 * The wait before consecutive failed attempt `attempt` (1-based) is retried.
 *
 * `random` is injected so the schedule is testable; it is `Math.random` in the
 * browser and takes the same [0, 1) contract.
 */
export function retryDelay(attempt: number, random: () => number = Math.random): number {
  const doublings = Math.max(0, attempt - 1);
  // Clamp the exponent before raising it: `2 ** 1000` is Infinity, and
  // `Infinity * jitter` is not a timeout anyone comes back from.
  const base = Math.min(FIRST_RETRY_MS * 2 ** Math.min(doublings, 32), MAX_RETRY_MS);
  return Math.round(base * (1 - JITTER + 2 * JITTER * random()));
}
