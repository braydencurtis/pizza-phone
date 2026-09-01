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
 * Milliseconds since the call was picked up — frozen once `endedAt` arrives.
 */
export function useElapsed(startedAt: string, endedAt: string | null): number {
  const start = Date.parse(startedAt);
  const end = endedAt === null ? null : Date.parse(endedAt);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (end !== null) return;
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [end]);

  return (end ?? now) - start;
}
