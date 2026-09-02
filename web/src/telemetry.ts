/**
 * The telemetry socket, as a hook.
 *
 * The work is all in {@link TelemetryLink}, which is framework-free and has its
 * socket, clock and session probe injected so it can be tested against a whole
 * afternoon of outages in milliseconds. This is the React skin over it, plus
 * the two browser signals the link cannot see for itself: the network coming
 * back, and the Operator returning to the tab.
 *
 * Those two matter more than they look. A laptop closed and reopened usually
 * has a connection that is dead without ever having fired a close event, so
 * without a nudge on wake the Console would sit on a frozen screen that looks
 * perfectly fine — which during an event reads as "quiet night" rather than
 * "broken". `wake()` replaces any socket that is no longer open and leaves a
 * healthy one alone.
 */

import { useEffect, useRef, useState } from "react";
import { TelemetryLink, telemetryUrl } from "./link";
import type { Connection, LinkState } from "./link";

export type { Connection, LinkState };
export { telemetryUrl };

const DETACHED: LinkState = {
  connection: "connecting",
  snapshot: null,
  stale: false,
  retryAt: null,
  attempt: 0,
};

export interface Telemetry extends LinkState {
  /** Stop waiting out the backoff and try the engine now. */
  retry: () => void;
}

export function useTelemetry(): Telemetry {
  const [state, setState] = useState<LinkState>(DETACHED);
  const link = useRef<TelemetryLink | null>(null);

  useEffect(() => {
    const telemetry = new TelemetryLink({ url: telemetryUrl() });
    link.current = telemetry;
    const unsubscribe = telemetry.subscribe(setState);
    telemetry.start();

    const wake = () => telemetry.wake();
    const wakeIfVisible = () => {
      if (document.visibilityState === "visible") telemetry.wake();
    };
    window.addEventListener("online", wake);
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wakeIfVisible);

    return () => {
      window.removeEventListener("online", wake);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", wakeIfVisible);
      unsubscribe();
      telemetry.stop();
      link.current = null;
    };
  }, []);

  return { ...state, retry: () => link.current?.wake() };
}
