from __future__ import annotations

"""Entry point for the AGI call router.

Invoked by Asterisk dialplan via AGI application. Handles the full
interactive call flow: play audio, collect DTMF, verify code,
and either connect to Upstairs Phone or hang up.
"""

import sys
from pathlib import Path
from typing import Any

from agi.agi_channel import AGIChannel
from agi.mode_puzzle import PuzzleSelector
from agi.mode_roguelike import handle as handle_roguelike_mode
from agi.router import Router
from agi.tts import SayBackend, TTSBackend, synthesize


def _asterisk_stream_path(wav_path: Path, audio_base: Path) -> str:
    """Convert a WAV file path to an Asterisk stream filename (no extension, no audio/ prefix)."""
    return str(wav_path.with_suffix("")).replace(str(audio_base), "", 1).lstrip("/")


def _attempt_loop(
    channel: AGIChannel,
    router: Router,
    digit_count: int,
    timeout: int,
    max_attempts: int,
    exile_audio: str,
    dispatch_kwargs: dict[str, Any],
) -> None:
    """Shared attempt loop for modes with DTMF answer collection and attempt limits.

    Collects digits, dispatches to router, handles succeed/exile/fail outcomes.
    On success, routes to upstream. On exile, plays audio and hangs up.
    Logs only the final (terminal) outcome.
    """
    for attempt in range(1, max_attempts + 1):
        channel.verbose(f"Attempt {attempt}/{max_attempts}")

        entered = channel.read_digits(
            filename="silence/beam",
            num_digits=digit_count,
            timeout=timeout,
        )

        if not entered:
            channel.verbose("No digits entered, hanging up")
            channel.hangup()
            return

        result = router.dispatch(
            attempt=attempt,
            log=attempt == max_attempts,
            **dispatch_kwargs,
        )

        if result["outcome"] == "succeed":
            router.logger.log(result | {"timestamp": __import__("datetime").datetime.now(__import__("datetime").UTC)})
            channel.verbose("Code accepted, connecting to upstream")
            channel.set_variable("UPSTREAM_EXT", dispatch_kwargs.pop("upstream_ext", ""))
            channel.exec_app("Goto", "pizza-success,s,1")
            return

        if result["outcome"] == "exile":
            channel.verbose("Exile — max attempts exhausted")
            channel.stream_file(exile_audio)
            channel.hangup()
            return

        channel.verbose("Wrong answer, playing error tone")
        channel.stream_file("beep")


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

    try:
        if mode == "tweeted":
            handle_tweeted(channel, router, code, max_attempts, upstream_ext)
        elif mode == "puzzle":
            handle_puzzle(channel, router, code, max_attempts, upstream_ext)
        elif mode == "roguelike":
            handle_roguelike(channel, router, code, upstream_ext)
        else:
            channel.verbose(f"Unknown mode: {mode}")
            channel.hangup()
    except Exception as e:
        channel.verbose(f"AGI error: {e}")
        print(f"AGI error: {e}", file=sys.stderr)
        channel.hangup()


def handle_tweeted(
    channel: AGIChannel,
    router: Router,
    code: str,
    max_attempts: int,
    upstream_ext: str,
) -> None:
    """Tweeted mode: caller enters a code via DTMF to get through."""
    channel.verbose("Mode: tweeted — waiting for code entry")

    _attempt_loop(
        channel=channel,
        router=router,
        digit_count=len(code),
        timeout=15000,
        max_attempts=max_attempts,
        exile_audio="voicemail/busy",
        dispatch_kwargs={
            "code_attempt": None,
            "upstream_ext": upstream_ext,
        },
    )


def handle_puzzle(
    channel: AGIChannel,
    router: Router,
    code: str,
    max_attempts: int,
    upstream_ext: str,
) -> None:
    """Puzzle mode: play audio riddle, collect DTMF answer with attempt loop."""
    base = Path(__file__).resolve().parent.parent
    pool_dir = base / "audio" / "puzzles"
    audio_base = base / "audio"

    channel.verbose("Mode: puzzle — presenting audio puzzle")

    puzzle_path = PuzzleSelector(pool_dir).pick()
    puzzle_id = puzzle_path.name
    channel.verbose(f"Puzzle selected: {puzzle_id}")

    channel.stream_file(_asterisk_stream_path(puzzle_path, audio_base))

    _attempt_loop(
        channel=channel,
        router=router,
        digit_count=len(code),
        timeout=30000,
        max_attempts=max_attempts,
        exile_audio="voicemail/busy",
        dispatch_kwargs={
            "answer": None,
            "puzzle_id": puzzle_id,
            "upstream_ext": upstream_ext,
        },
    )


class RoguelikeContextImpl:
    def __init__(self, channel: AGIChannel, tts: TTSBackend) -> None:
        self.channel = channel
        self.tts = tts

    def speak(self, text: str) -> None:
        audio_path = synthesize(text, backend=self.tts)
        self.channel.verbose(f"TTS: {audio_path}")
        self.channel.exec_app("Playback", str(audio_path))

    def read_choice(self, keys: str) -> str:
        return self.channel.read_digits(
            filename="silence/beam",
            num_digits=1,
            timeout=15000,
        )


def handle_roguelike(
    channel: AGIChannel,
    router: Router,
    code: str,
    upstream_ext: str,
) -> None:
    """Roguelike mode: navigate a DTMF phone tree."""
    channel.verbose("Mode: roguelike — navigating phone tree")

    tts_backend = SayBackend()
    ctx = RoguelikeContextImpl(channel=channel, tts=tts_backend)
    path = handle_roguelike_mode(ctx, code)
    channel.verbose(f"Roguelike path: {path}")

    result = router.dispatch(path=path)

    if result["outcome"] == "succeed":
        channel.verbose("Roguelike complete, connecting to upstream")
        channel.set_variable("UPSTREAM_EXT", upstream_ext)
        channel.exec_app("Goto", "pizza-success,s,1")
    else:
        channel.verbose("Roguelike failed, hanging up")
        channel.hangup()


if __name__ == "__main__":
    main()
