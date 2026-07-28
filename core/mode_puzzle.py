from __future__ import annotations

import random
from pathlib import Path
from typing import Any


class PuzzleSelector:
    """Selects a random puzzle WAV from the pool directory."""

    def __init__(self, pool_dir: Path, seed: int | None = None) -> None:
        self.pool_dir = pool_dir
        self._rng = random.Random(seed)

    def pick(self) -> Path:
        wav_files = sorted(p for p in self.pool_dir.iterdir() if p.suffix.lower() == ".wav")
        if not wav_files:
            raise FileNotFoundError(f"No .wav files in {self.pool_dir}")
        return self._rng.choice(wav_files)


def handle(
    *,
    answer: str,
    expected_code: str,
    attempt: int,
    max_attempts: int,
    puzzle_id: str,
) -> dict[str, Any]:
    """Evaluate a single puzzle answer attempt.

    Returns outcome with attempt tracking and Exile support.
    """
    if answer == expected_code:
        return {
            "outcome": "succeed",
            "attempts": attempt,
            "puzzle_id": puzzle_id,
        }
    if attempt >= max_attempts:
        return {
            "outcome": "exile",
            "attempts": attempt,
            "puzzle_id": puzzle_id,
        }
    return {
        "outcome": "fail",
        "attempts": attempt,
        "puzzle_id": puzzle_id,
    }
