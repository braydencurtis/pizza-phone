#!/usr/bin/env python3
"""Interactive CLI playground for roguelike mode."""

import sys

from core import mode_roguelike


class CliContext:
    """RoguelikeContext backed by stdin/stdout."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        print(f"  📞 {text}")

    def read_choice(self, keys: str) -> str:
        """Read a key, or ``""`` for a player who walked away.

        Empty is what a real caller's timed-out DTMF read returns, and the
        walker ends the call on it (#53) — so EOF, or just pressing enter, is
        how you hang up on the playground.
        """
        try:
            val = input(f"  Press [{keys}]: ").strip()
        except EOFError:
            return ""
        if val and val not in keys:
            print(f"  ⚠ Invalid choice. Press [{keys}].")
        return val


def main() -> None:
    code = "1234"
    seed = None
    if "--seed" in sys.argv:
        idx = sys.argv.index("--seed")
        if idx + 1 < len(sys.argv):
            seed = int(sys.argv[idx + 1])

    print("=" * 60)
    print("  📱 ROGUELIKE PHONE-TREE MODE")
    print("=" * 60)
    print(f"  Code to find: {code}")
    print(f"  Seed: {seed if seed is not None else 'random'}")
    print()

    ctx = CliContext()
    result = mode_roguelike.handle(ctx, code, seed=seed)

    print()
    print("=" * 60)
    print(f"  Outcome: {result['outcome']}")
    print(f"  Steps taken: {len(result['path'])}")
    print(f"  Nodes visited: {result['nodes_visited']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
