#!/usr/bin/env python3
"""Interactive CLI playground for roguelike mode."""

import sys
from typing import Literal

from agi import mode_roguelike


class CliContext:
    """RoguelikeContext backed by stdin/stdout."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        print(f"  📞 {text}")

    def read_choice(self, keys: str) -> str:
        while True:
            try:
                val = input(f"  Press [{keys}]: ").strip()
            except EOFError:
                val = ""
            if val and val in keys:
                return val
            print(f"  ⚠ Invalid choice. Press [{keys}].")


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
    print(f"  Steps taken: {len(result['path'])}")
    print(f"  Nodes visited: {result['nodes_visited']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
