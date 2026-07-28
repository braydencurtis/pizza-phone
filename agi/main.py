"""Entry point for the AGI call router.

Invoked by Asterisk dialplan via AGI application. Thin channel wiring only: it
builds an :class:`AGICallIO` over the live :class:`AGIChannel` and hands the
interactive flow to ``core.flow``. All game logic lives in ``core/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agi.agi_call_io import AGICallIO
from agi.agi_channel import AGIChannel
from core import flow
from core.mode_puzzle import PuzzleSelector
from core.router import Router

# Asterisk builtin prompts used for feedback (driver-specific media names).
EXILE_MEDIA = "voicemail/busy"
WRONG_MEDIA = "beep"


def _asterisk_stream_path(wav_path: Path, audio_base: Path) -> str:
    """Convert a WAV file path to an Asterisk stream filename (no extension, no audio/ prefix)."""
    return str(wav_path.with_suffix("")).replace(str(audio_base), "", 1).lstrip("/")


def main() -> None:
    base = Path(__file__).resolve().parent.parent
    config_dir = base / "config"
    log_dir = base / "logs"
    log_dir.mkdir(exist_ok=True)

    channel = AGIChannel()
    router = Router(config_dir=config_dir, log_dir=log_dir)
    config = router.load_config()

    mode = config.get("mode", "tweeted")
    code = config.get("code", "0000")
    max_attempts = config.get("attempt_limit", 3)
    upstream_ext = config.get("upstream_extension", "200")

    channel.verbose(f"Pizza Phone AGI — mode: {mode}, code: {code}")
    io = AGICallIO(channel=channel, upstream_ext=upstream_ext)

    try:
        if mode == "tweeted":
            channel.verbose("Mode: tweeted — waiting for code entry")
            flow.run_tweeted(
                io,
                router,
                code=code,
                max_attempts=max_attempts,
                exile_media=EXILE_MEDIA,
                wrong_media=WRONG_MEDIA,
            )
        elif mode == "puzzle":
            _run_puzzle(io, router, base, channel, code, max_attempts)
        elif mode == "roguelike":
            channel.verbose("Mode: roguelike — navigating phone tree")
            flow.run_roguelike(io, router, code=code)
        else:
            channel.verbose(f"Unknown mode: {mode}")
            channel.hangup()
    except Exception as e:  # noqa: BLE001 — AGI entry point safety net
        channel.verbose(f"AGI error: {e}")
        print(f"AGI error: {e}", file=sys.stderr)
        channel.hangup()


def _run_puzzle(
    io: AGICallIO,
    router: Router,
    base: Path,
    channel: AGIChannel,
    code: str,
    max_attempts: int,
) -> None:
    """Select a puzzle from the pool and run the puzzle flow.

    Puzzle selection is core; resolving the chosen WAV to an Asterisk stream
    name is AGI-specific, so it stays here.
    """
    audio_base = base / "audio"
    pool_dir = audio_base / "puzzles"

    channel.verbose("Mode: puzzle — presenting audio puzzle")
    puzzle_path = PuzzleSelector(pool_dir).pick()
    channel.verbose(f"Puzzle selected: {puzzle_path.name}")

    flow.run_puzzle(
        io,
        router,
        code=code,
        max_attempts=max_attempts,
        puzzle_id=puzzle_path.name,
        prompt_media=_asterisk_stream_path(puzzle_path, audio_base),
        exile_media=EXILE_MEDIA,
        wrong_media=WRONG_MEDIA,
    )


if __name__ == "__main__":
    main()
