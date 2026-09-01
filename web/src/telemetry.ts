/**
 * The telemetry socket, as a hook.
 *
 * Deliberately dumb: hold the last snapshot the engine sent and the state of
 * the socket, and keep those two separate. A console with no call to show and a
 * console that has lost the engine look nothing alike to an Operator, and
 * conflating them is how a dashboard ends up lying about a quiet booth.
 *
 * Reconnection is #40. Until then a dropped socket says so and stops.
 */

import { useEffect, useState } from "react";
import type { Snapshot } from "./snapshot";

export type Connection = "connecting" | "live" | "lost" | "unauthorized";

export interface Telemetry {
  connection: Connection;
  snapshot: Snapshot | null;
}

const TELEMETRY_URL = "/ws/telemetry";

export function telemetryUrl(location: Location = window.location): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${TELEMETRY_URL}`;
}

export function useTelemetry(): Telemetry {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);

  useEffect(() => {
    const socket = new WebSocket(telemetryUrl());
    let open = false;

    socket.onopen = () => {
      open = true;
      setConnection("live");
    };
    socket.onmessage = (event) => {
      setSnapshot(JSON.parse(event.data as string) as Snapshot);
    };
    socket.onclose = () => {
      // A socket refused at the handshake never opened: the cookie is gone or
      // expired, and the operator needs the password, not a retry.
      setConnection(open ? "lost" : "unauthorized");
    };

    return () => socket.close();
  }, []);

  return { connection, snapshot };
}
