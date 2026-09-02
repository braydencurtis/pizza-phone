/**
 * The elapsed-call clock, advanced here rather than on the engine.
 *
 * Snapshots carry `started_at`, never a duration (ADR-0003): a call on the line
 * would otherwise generate a message a second purely to tick a timer. So the
 * browser counts, and the engine only says when the call began — and, once it
 * is over, when it ended, so the clock stops where the call did instead of
 * running on forever.
 *
 * The clock is the *viewer's*, so a laptop with a badly wrong system time will
 * show a badly wrong elapsed. The console is a LAN dashboard on machines that
 * keep time; this is not worth a handshake to correct.
 *
 * It also stops when contact does (#40). A browser advancing the clock is only
 * telling the truth while it is being told the call is still up; on a dead
 * socket that same counter becomes the most convincing lie on the screen — a
 * call that ended two minutes ago, still ticking. So the clock freezes where
 * the last snapshot left it and the panel says as much.
 */

import { useEffect, useState } from "react";

/** Four ticks a second: the seconds digit turns over without a visible stall. */
const TICK_MS = 250;

export function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

/**
 * Milliseconds since the call was picked up.
 *
 * Frozen once `endedAt` arrives — and frozen too while `live` is false, because
 * a clock that keeps counting on a socket the engine is no longer on the other
 * end of is stale data wearing the costume of live data.
 */
export function useElapsed(startedAt: string, endedAt: string | null, live = true): number {
  const start = Date.parse(startedAt);
  const end = endedAt === null ? null : Date.parse(endedAt);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (end !== null || !live) return;
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [end, live]);

  return (end ?? now) - start;
}

/** Whole seconds until `target`, ticking down. `null` when there is no target. */
export function useCountdown(target: number | null): number | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (target === null) return;
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [target]);

  return target === null ? null : Math.max(0, Math.ceil((target - now) / 1000));
}
