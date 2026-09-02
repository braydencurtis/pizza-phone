/**
 * The reconnecting telemetry link (#40).
 *
 * Everything the link touches that isn't deterministic — the socket, the clock,
 * the timers, the jitter, the session probe — is injected, so these tests drive
 * a whole afternoon of Wi-Fi blips and engine restarts in a few milliseconds
 * with no browser and no waiting.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { FIRST_RETRY_MS, MAX_RETRY_MS } from "./backoff";
import { SILENCE_LIMIT_MS, TelemetryLink } from "./link";
import type { LinkState, SessionCheck, TelemetrySocket } from "./link";
import type { Snapshot } from "./snapshot";

const OPEN = 1;
const CLOSED = 3;

function snapshot(code: string, sessionId: string | null = null): Snapshot {
  return {
    schema: 2,
    config: { mode: "tweeted", code, attempt_limit: 3, upstream_extension: "300" },
    call:
      sessionId === null
        ? null
        : {
            session_id: sessionId,
            state: "in_mode",
            mode: "tweeted",
            caller_id: "+15551234567",
            started_at: "2026-09-01T12:00:00+00:00",
            ended_at: null,
            digits: "",
            attempts: 0,
            outcome: null,
          },
  };
}

/** A socket the test opens, feeds and drops by hand. */
class FakeSocket implements TelemetrySocket {
  readyState = 0;
  closedByClient = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;

  close(): void {
    this.closedByClient = true;
    this.readyState = CLOSED;
  }

  /** The handshake succeeded. */
  accept(): void {
    this.readyState = OPEN;
    this.onopen?.();
  }

  /** The engine pushed a snapshot. */
  push(value: Snapshot): void {
    this.onmessage?.({ data: JSON.stringify(value) });
  }

  /** The far end went away (or refused the handshake, if never accepted). */
  drop(): void {
    this.readyState = CLOSED;
    this.onclose?.();
  }

  /** The laptop slept: the connection is dead and nobody told the page. */
  die(): void {
    this.readyState = CLOSED;
  }
}

/** A clock the test advances, so a 15-second backoff costs no seconds. */
class FakeClock {
  time = 0;
  private nextId = 1;
  private timers = new Map<number, { at: number; fn: () => void }>();

  setTimer = (fn: () => void, ms: number): number => {
    const id = this.nextId++;
    this.timers.set(id, { at: this.time + ms, fn });
    return id;
  };

  clearTimer = (id: number): void => {
    this.timers.delete(id);
  };

  now = (): number => this.time;

  get pending(): number {
    return this.timers.size;
  }

  /** Move time forward, firing what comes due, then let promises settle. */
  async advance(ms: number): Promise<void> {
    const until = this.time + ms;
    for (;;) {
      const due = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= until)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (due === undefined) break;
      const [id, timer] = due;
      this.timers.delete(id);
      this.time = timer.at;
      timer.fn();
      await settle();
    }
    this.time = until;
    await settle();
  }
}

/** The connection states a run of updates passed through, in order. */
function transitions(states: LinkState[]): string[] {
  return states
    .map((state) => state.connection)
    .filter((connection, index, all) => connection !== all[index - 1]);
}

/** Let every already-queued promise callback run. */
function settle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("TelemetryLink", () => {
  let sockets: FakeSocket[];
  let clock: FakeClock;
  let sessionCheck: SessionCheck;
  let probes: number;

  function link(): TelemetryLink {
    return new TelemetryLink({
      url: "ws://booth/ws/telemetry",
      open: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
      checkSession: async () => {
        probes += 1;
        return sessionCheck;
      },
      now: clock.now,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
      random: () => 0.5,
    });
  }

  const latest = (): FakeSocket => sockets[sockets.length - 1];

  beforeEach(() => {
    sockets = [];
    clock = new FakeClock();
    sessionCheck = "unreachable";
    probes = 0;
  });

  it("opens a socket on start and reports itself connecting", () => {
    const telemetry = link();
    telemetry.start();
    expect(sockets).toHaveLength(1);
    expect(telemetry.state.connection).toBe("connecting");
    expect(telemetry.state.snapshot).toBeNull();
    telemetry.stop();
  });

  it("goes live and holds the snapshots the engine pushes", () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    expect(telemetry.state.connection).toBe("live");
    latest().push(snapshot("1234"));
    expect(telemetry.state.snapshot?.config.code).toBe("1234");
    expect(telemetry.state.stale).toBe(false);
    telemetry.stop();
  });

  it("reconnects by itself after the socket drops", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().push(snapshot("1234"));

    latest().drop();
    expect(telemetry.state.connection).toBe("lost");
    expect(sockets).toHaveLength(1);

    await clock.advance(FIRST_RETRY_MS);
    expect(sockets).toHaveLength(2);
    expect(telemetry.state.connection).toBe("connecting");
    telemetry.stop();
  });

  it("marks what it is still showing as stale the moment contact is lost", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().push(snapshot("1234", "sess-1"));
    expect(telemetry.state.stale).toBe(false);

    latest().drop();
    // The last we heard is kept — an Operator wants to see what was happening —
    // but it is flagged, so nothing can render it as the current truth.
    expect(telemetry.state.snapshot?.call?.session_id).toBe("sess-1");
    expect(telemetry.state.stale).toBe(true);

    await clock.advance(FIRST_RETRY_MS);
    expect(telemetry.state.stale).toBe(true);
    telemetry.stop();
  });

  it("does not un-dim on a bare handshake, before the engine has said anything", async () => {
    // The gap between the socket opening and the first snapshot landing is
    // still a gap: what is on screen is the pre-drop call. Clearing `stale`
    // here would drop the banner, un-dim the panel and — worst — restart the
    // elapsed clock, ticking a long-dead call forward as though it were live.
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().push(snapshot("1234", "sess-1"));
    latest().drop();
    await clock.advance(FIRST_RETRY_MS);

    latest().accept();
    expect(telemetry.state.connection).toBe("live");
    expect(telemetry.state.stale).toBe(true);

    latest().push(snapshot("1234", "sess-1"));
    expect(telemetry.state.stale).toBe(false);
    telemetry.stop();
  });

  it("is correct again on the next snapshot, with no reload", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().push(snapshot("1234", "sess-1"));
    latest().drop();

    await clock.advance(FIRST_RETRY_MS);
    latest().accept();
    // Snapshots are whole state, so reattaching needs no resync: the first
    // message after reconnecting is the entire truth.
    latest().push(snapshot("9999", null));

    expect(telemetry.state.connection).toBe("live");
    expect(telemetry.state.stale).toBe(false);
    expect(telemetry.state.snapshot?.config.code).toBe("9999");
    expect(telemetry.state.snapshot?.call).toBeNull();
    telemetry.stop();
  });

  it("backs off between attempts rather than hammering the engine", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().drop();

    const waits: number[] = [];
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await settle();
      const scheduledFor = telemetry.state.retryAt;
      expect(scheduledFor).not.toBeNull();
      waits.push(scheduledFor! - clock.now());
      await clock.advance(scheduledFor! - clock.now());
      latest().drop();
    }

    expect(waits[0]).toBe(FIRST_RETRY_MS);
    for (let i = 1; i < waits.length; i += 1) {
      expect(waits[i]).toBeGreaterThanOrEqual(waits[i - 1]);
    }
    expect(waits[waits.length - 1]).toBeLessThanOrEqual(MAX_RETRY_MS);
    telemetry.stop();
  });

  it("keeps trying while the engine is simply down", async () => {
    sessionCheck = "unreachable";
    const telemetry = link();
    telemetry.start();
    // Refused before it ever opened — the engine is not listening.
    latest().drop();
    await settle();

    expect(telemetry.state.connection).toBe("lost");
    await clock.advance(MAX_RETRY_MS);
    expect(sockets.length).toBeGreaterThan(1);
    telemetry.stop();
  });

  it("sends the Operator back to login when the engine restarted under it", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().push(snapshot("1234"));

    // The engine restarts: every in-memory Console Session dies with it, so the
    // socket upgrade is refused at the handshake and never opens.
    sessionCheck = "unauthorized";
    latest().drop();
    await clock.advance(FIRST_RETRY_MS);
    latest().drop();
    await settle();

    expect(probes).toBeGreaterThan(0);
    expect(telemetry.state.connection).toBe("unauthorized");

    // And it stops: there is nothing to reconnect to until someone logs in.
    const attempts = sockets.length;
    await clock.advance(MAX_RETRY_MS * 4);
    expect(sockets).toHaveLength(attempts);
    telemetry.stop();
  });

  it("does not mistake an engine that is merely down for a lost session", async () => {
    sessionCheck = "unreachable";
    const telemetry = link();
    telemetry.start();
    latest().drop();
    await settle();
    expect(telemetry.state.connection).not.toBe("unauthorized");
    telemetry.stop();
  });

  it("reconnects at once when the laptop wakes, instead of serving out the wait", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().drop();
    await clock.advance(FIRST_RETRY_MS);
    latest().drop();
    await settle();
    const before = sockets.length;

    telemetry.wake();
    expect(sockets).toHaveLength(before + 1);
    expect(telemetry.state.connection).toBe("connecting");
    telemetry.stop();
  });

  it("replaces a socket that died silently while the laptop slept", () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    // Sleep kills the connection without the page ever seeing a close event.
    latest().die();

    telemetry.wake();
    expect(sockets).toHaveLength(2);
    telemetry.stop();
  });

  it("does not restart a handshake that is already in flight", async () => {
    // Alt-tabbing during an outage fires a wake per focus. Each one must not
    // throw away the attempt in progress and start over, or a burst of them
    // would sidestep the backoff entirely and hammer the engine.
    const telemetry = link();
    telemetry.start();
    latest().drop();
    await settle();
    await clock.advance(FIRST_RETRY_MS);
    const opened = sockets.length;

    telemetry.wake();
    telemetry.wake();
    telemetry.wake();
    expect(sockets).toHaveLength(opened);
    telemetry.stop();
  });

  it("leaves a healthy socket alone when the tab is merely refocused", () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();

    telemetry.wake();
    expect(sockets).toHaveLength(1);
    expect(telemetry.state.connection).toBe("live");
    telemetry.stop();
  });

  it("stops believing a socket that has gone silent for too long", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().push(snapshot("1234"));

    // The engine keeps the socket fed even when nothing changes, so silence
    // this long is a dead connection, not a quiet booth.
    await clock.advance(SILENCE_LIMIT_MS + 1);
    expect(telemetry.state.connection).toBe("lost");
    expect(telemetry.state.stale).toBe(true);
    expect(latest().closedByClient).toBe(true);
    telemetry.stop();
  });

  it("keeps believing a socket the engine is still feeding", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    for (let tick = 0; tick < 5; tick += 1) {
      await clock.advance(SILENCE_LIMIT_MS - 1);
      latest().push(snapshot("1234"));
    }
    expect(telemetry.state.connection).toBe("live");
    expect(sockets).toHaveLength(1);
    telemetry.stop();
  });

  it("tells its subscribers every time the state moves", async () => {
    const telemetry = link();
    const seen: LinkState[] = [];
    const unsubscribe = telemetry.subscribe((state) => seen.push(state));
    telemetry.start();
    latest().accept();
    latest().push(snapshot("1234"));
    latest().drop();

    expect(transitions(seen)).toEqual(["connecting", "live", "lost"]);
    expect(seen[seen.length - 1].stale).toBe(true);

    const heard = seen.length;
    unsubscribe();
    await clock.advance(FIRST_RETRY_MS);
    latest().accept();
    expect(seen).toHaveLength(heard);
    telemetry.stop();
  });

  it("lets go of the socket and its timers when stopped", async () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    latest().drop();
    expect(clock.pending).toBeGreaterThan(0);

    telemetry.stop();
    expect(clock.pending).toBe(0);
    await clock.advance(MAX_RETRY_MS * 4);
    expect(sockets).toHaveLength(1);
  });

  it("closes the socket it holds when stopped mid-call", () => {
    const telemetry = link();
    telemetry.start();
    latest().accept();
    telemetry.stop();
    expect(latest().closedByClient).toBe(true);
  });

  it("ignores a socket it has already replaced", async () => {
    const telemetry = link();
    telemetry.start();
    const first = latest();
    first.accept();
    first.drop();
    await clock.advance(FIRST_RETRY_MS);
    const second = latest();
    second.accept();

    // A late event from the abandoned socket must not knock the live one over.
    first.drop();
    expect(telemetry.state.connection).toBe("live");
    telemetry.stop();
  });
});
