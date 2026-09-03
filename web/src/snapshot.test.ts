import { describe, expect, it } from "vitest";
import { finalCountLabel, STATE_COPY } from "./snapshot";

/**
 * The number a finished call shows is not the same number in every Mode, so it
 * does not get the same name (#56). Attempts are answers offered against the
 * Code, counted towards the Attempt Limit; a maze walk has neither, and the
 * column carries its moves instead.
 */
describe("finalCountLabel", () => {
  it("calls a finished maze call's number rooms, not attempts", () => {
    expect(finalCountLabel("roguelike")).toBe("Rooms");
  });

  it("calls it attempts in the two Modes that have an Attempt Limit", () => {
    expect(finalCountLabel("tweeted")).toBe("Attempts");
    expect(finalCountLabel("puzzle")).toBe("Attempts");
  });

  it("falls back to attempts for a call with no Mode yet", () => {
    expect(finalCountLabel(null)).toBe("Attempts");
  });
});

describe("STATE_COPY", () => {
  it("does not tell the Operator every Exile was a burned Attempt Limit", () => {
    // The maze exiles a caller who never finds the room, with no wrong answer
    // anywhere in it (#59) — so the note may not claim one.
    expect(STATE_COPY.exiled.note).toMatch(/maze/);
  });
});
