/**
 * The telemetry link: one socket to the engine, kept up on its own.
 *
 * A laptop waking from sleep, a Wi-Fi blip or an engine restart used to leave
 * the Console frozen on a screen that still looked fine — which during an event
 * reads as "quiet night" rather than "broken". This is the thing that stops
 * that: it reconnects, it backs off while it does, and until it succeeds it
 * says so loudly enough that nothing on the page can be mistaken for current.
 *
 * Recovery needs no resync protocol. Snapshots are whole state, never deltas
 * (ADR-0003), so the first message after reattaching *is* the truth — which is
 * the payoff of that decision, collected here.
 *
 * Three things are worth knowing:
 *
 * **A refused handshake is not the same as a dead engine.** The browser cannot
 * tell them apart — both arrive as a bare close — so a socket that closes
 * *before it ever opened* is followed by `checkSession()`, which can. See
 * `engine/README.md`, "Reconnection".
 *
 * **Silence is not proof of life.** A connection killed by sleep or a vanished
 * access point often never fires a close event at all, so the engine pulses a
 * snapshot on a timer and this side treats a longer gap than
 * {@link SILENCE_LIMIT_MS} as a dead socket.
 *
 * **It is framework-free, with everything non-deterministic injected** — the
 * socket, the clock, the timers, the jitter, the probe. `useTelemetry` in
 * `telemetry.ts` is a thin React wrapper over it, and the suite drives an
 * afternoon of outages in a few milliseconds.
 */

import { checkSession } from "./api";
import type { SessionCheck } from "./api";
import { retryDelay } from "./backoff";
import type { Snapshot } from "./snapshot";

export type { SessionCheck };

/** How the Console is currently placed with respect to the engine. */
export type Connection = "connecting" | "live" | "lost" | "unauthorized";

/**
 * How long the engine may say nothing before we stop believing the socket.
 *
 * **Must stay above twice `KEEPALIVE` in `engine/console.py`** (20s there, so
 * two missed pulses plus slack here). The two numbers are one setting with a
 * process boundary through the middle: raise the engine's pulse without raising
 * this and every console starts tearing down healthy sockets on a timer.
 */
export const SILENCE_LIMIT_MS = 50_000;

const SOCKET_CONNECTING = 0;
const SOCKET_OPEN = 1;

/**
 * The slice of `WebSocket` this module uses.
 *
 * Narrow on purpose: it is the whole surface a test has to fake, and the
 * browser's real socket is adapted onto it by {@link browserSocket} rather than
 * cast to it.
 */
export interface TelemetrySocket {
  readyState: number;
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onclose: (() => void) | null;
}

/** What the Console is showing, and how much it should be believed. */
export interface LinkState {
  connection: Connection;
  /** The last snapshot the engine sent — not necessarily the current one. */
  snapshot: Snapshot | null;
  /**
   * True when `snapshot` is a memory rather than the truth. Kept here rather
   * than left for each caller to work out: "is this still current?" is the one
   * question a dashboard must never get wrong.
   */
  stale: boolean;
  /** When the next reconnect attempt is due, or `null` if none is waiting. */
  retryAt: number | null;
  /** Consecutive failed attempts. Zero whenever the link is live. */
  attempt: number;
}

export interface LinkOptions {
  url: string;
  open?: (url: string) => TelemetrySocket;
  checkSession?: () => Promise<SessionCheck>;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => number;
  clearTimer?: (id: number) => void;
  random?: () => number;
}

/** The real socket, adapted onto {@link TelemetrySocket}. */
export function browserSocket(url: string): TelemetrySocket {
  const ws = new WebSocket(url);
  const socket: TelemetrySocket = {
    get readyState() {
      return ws.readyState;
    },
    close: () => ws.close(),
    onopen: null,
    onmessage: null,
    onclose: null,
  };
  ws.onopen = () => socket.onopen?.();
  ws.onmessage = (event) => socket.onmessage?.({ data: String(event.data) });
  // An errored socket always closes too, so one handler covers both — and
  // taking the error as well means we don't wait for the close to be delivered.
  ws.onclose = () => socket.onclose?.();
  ws.onerror = () => socket.onclose?.();
  return socket;
}

export function telemetryUrl(location: Location = window.location): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ws/telemetry`;
}

export class TelemetryLink {
  private readonly url: string;
  private readonly open: (url: string) => TelemetrySocket;
  private readonly probe: () => Promise<SessionCheck>;
  private readonly now: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => number;
  private readonly clearTimer: (id: number) => void;
  private readonly random: () => number;

  private listeners = new Set<(state: LinkState) => void>();
  private current: LinkState = {
    connection: "connecting",
    snapshot: null,
    stale: false,
    retryAt: null,
    attempt: 0,
  };

  private socket: TelemetrySocket | null = null;
  private retryTimer: number | null = null;
  private silenceTimer: number | null = null;
  private running = false;
  /**
   * Which socket the events arriving now belong to. A socket we have given up
   * on can still deliver a close afterwards, and without this that late event
   * would knock over the connection that replaced it.
   */
  private generation = 0;

  constructor(options: LinkOptions) {
    this.url = options.url;
    this.open = options.open ?? browserSocket;
    this.probe = options.checkSession ?? checkSession;
    this.now = options.now ?? Date.now;
    this.setTimer = options.setTimer ?? ((fn, ms) => window.setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((id) => window.clearTimeout(id));
    this.random = options.random ?? Math.random;
  }

  // -- the outside world -------------------------------------------------

  /** What the Console should be showing right now. */
  get state(): LinkState {
    return this.current;
  }

  /** Connect, and keep reconnecting until {@link stop}. */
  start(): void {
    if (this.running) return;
    this.running = true;
    this.connect();
  }

  /** Let go of the socket and every timer. Safe to call twice. */
  stop(): void {
    this.running = false;
    this.generation += 1;
    this.cancelRetry();
    this.cancelSilenceWatch();
    this.closeSocket();
  }

  /**
   * The laptop woke, the network came back, or the Operator looked at the tab.
   *
   * Any of those is new information, so we stop serving out a backoff wait and
   * try now. It also catches the connection that sleep killed without ever
   * firing a close event: if the socket we are holding is no longer open, it is
   * replaced whatever the state says.
   */
  wake(): void {
    if (!this.running) return;
    // Nothing to wake to: the session is gone until somebody logs in again.
    if (this.current.connection === "unauthorized") return;
    // A socket we are already holding open, or one still shaking hands, is the
    // attempt this wake would have started. Alt-tabbing fires a wake per focus,
    // and restarting an in-flight attempt each time would sidestep the backoff
    // and hammer the engine — the opposite of what waking is for.
    const readyState = this.socket?.readyState;
    if (readyState === SOCKET_OPEN || readyState === SOCKET_CONNECTING) return;
    this.cancelRetry();
    this.update({ attempt: 0 });
    this.connect();
  }

  /** Watch the state. The returned function stops watching. */
  subscribe(listener: (state: LinkState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  // -- the socket --------------------------------------------------------

  private connect(): void {
    this.cancelSilenceWatch();
    this.closeSocket();
    const generation = ++this.generation;
    this.update({ connection: "connecting", stale: this.current.snapshot !== null, retryAt: null });

    const socket = this.open(this.url);
    this.socket = socket;
    let opened = false;

    socket.onopen = () => {
      if (generation !== this.generation) return;
      opened = true;
      // The handshake is not the news. What is on screen is still whatever we
      // last heard, so `stale` stays set until a snapshot actually lands —
      // clearing it here would un-dim the panel and restart the elapsed clock
      // on a call that may have ended minutes ago.
      this.update({
        connection: "live",
        stale: this.current.snapshot !== null,
        attempt: 0,
        retryAt: null,
      });
      this.watchForSilence();
    };
    socket.onmessage = (event) => {
      if (generation !== this.generation) return;
      this.watchForSilence();
      const snapshot = parseSnapshot(event.data);
      if (snapshot === null) return;
      this.update({ connection: "live", snapshot, stale: false });
    };
    socket.onclose = () => {
      if (generation !== this.generation) return;
      // Retire it before doing anything else: a socket that manages to deliver
      // a second close must not schedule a second reconnect.
      this.generation += 1;
      this.socket = null;
      this.cancelSilenceWatch();
      this.lost(opened);
    };
  }

  /**
   * Contact is gone. Decide whether to wait and retry, or send them to login.
   *
   * `everOpened` is the whole signal: a socket that opened and then closed lost
   * a connection it was entitled to, so retrying is right. One that never
   * opened may have been refused — which is what an engine restart looks like,
   * since it forgets every Console Session — so we ask before assuming.
   */
  private lost(everOpened: boolean): void {
    if (!this.running) return;
    this.update({ connection: "lost", stale: this.current.snapshot !== null });
    if (everOpened) {
      this.scheduleRetry();
      return;
    }
    const generation = this.generation;
    void this.probe().then((result) => {
      if (!this.running || generation !== this.generation) return;
      if (result === "unauthorized") {
        this.update({ connection: "unauthorized", retryAt: null });
        return;
      }
      this.scheduleRetry();
    });
  }

  private scheduleRetry(): void {
    this.cancelRetry();
    const attempt = this.current.attempt + 1;
    const delay = retryDelay(attempt, this.random);
    this.update({ attempt, retryAt: this.now() + delay });
    this.retryTimer = this.setTimer(() => {
      this.retryTimer = null;
      if (this.running) this.connect();
    }, delay);
  }

  private cancelRetry(): void {
    if (this.retryTimer !== null) {
      this.clearTimer(this.retryTimer);
      this.retryTimer = null;
    }
  }

  /**
   * Restart the "the engine has gone quiet" countdown.
   *
   * Called on every message, so it only fires when the engine's keepalive
   * snapshots stop arriving — a connection that died without saying so.
   */
  private watchForSilence(): void {
    this.cancelSilenceWatch();
    this.silenceTimer = this.setTimer(() => {
      this.silenceTimer = null;
      if (!this.running) return;
      const socket = this.socket;
      this.socket = null;
      this.generation += 1;
      socket?.close();
      this.update({ connection: "lost", stale: this.current.snapshot !== null });
      this.scheduleRetry();
    }, SILENCE_LIMIT_MS);
  }

  private cancelSilenceWatch(): void {
    if (this.silenceTimer !== null) {
      this.clearTimer(this.silenceTimer);
      this.silenceTimer = null;
    }
  }

  private closeSocket(): void {
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }

  private update(changes: Partial<LinkState>): void {
    this.current = { ...this.current, ...changes };
    for (const listener of [...this.listeners]) listener(this.current);
  }
}

/**
 * A snapshot off the wire, or `null` if it wasn't one.
 *
 * A message we can't parse is dropped rather than thrown: the alternative is an
 * exception inside a socket callback taking the whole console down, and the
 * next snapshot is the complete truth anyway.
 */
function parseSnapshot(data: string): Snapshot | null {
  try {
    return JSON.parse(data) as Snapshot;
  } catch {
    return null;
  }
}
