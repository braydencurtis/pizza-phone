import { describe, expect, it } from "vitest";
import { FIRST_RETRY_MS, MAX_RETRY_MS, retryDelay } from "./backoff";

/** No jitter: the midpoint of the band, so a delay is exactly its base. */
const CENTRED = () => 0.5;

describe("retryDelay", () => {
  it("waits barely at all before the first retry", () => {
    expect(retryDelay(1, CENTRED)).toBe(FIRST_RETRY_MS);
  });

  it("doubles the wait for each consecutive failure", () => {
    expect(retryDelay(2, CENTRED)).toBe(FIRST_RETRY_MS * 2);
    expect(retryDelay(3, CENTRED)).toBe(FIRST_RETRY_MS * 4);
    expect(retryDelay(4, CENTRED)).toBe(FIRST_RETRY_MS * 8);
  });

  it("caps the wait, so a forgotten console still notices the engine return", () => {
    expect(retryDelay(20, CENTRED)).toBe(MAX_RETRY_MS);
    expect(retryDelay(1000, CENTRED)).toBe(MAX_RETRY_MS);
  });

  it("jitters around the base so a room full of consoles does not retry in lockstep", () => {
    const low = retryDelay(5, () => 0);
    const high = retryDelay(5, () => 1);
    expect(low).toBeLessThan(retryDelay(5, CENTRED));
    expect(high).toBeGreaterThan(retryDelay(5, CENTRED));
    // …but still recognisably a wait of that order, not a random one.
    expect(low).toBeGreaterThan(retryDelay(5, CENTRED) / 2);
    expect(high).toBeLessThan(retryDelay(5, CENTRED) * 2);
  });

  it("never returns a delay that would hammer the engine", () => {
    for (let attempt = 1; attempt <= 50; attempt += 1) {
      for (const random of [0, 0.25, 0.5, 0.75, 1]) {
        const delay = retryDelay(attempt, () => random);
        expect(delay).toBeGreaterThanOrEqual(FIRST_RETRY_MS / 2);
        expect(delay).toBeLessThanOrEqual(MAX_RETRY_MS * 1.5);
      }
    }
  });
});
