from __future__ import annotations

"""Entry point for the AGI call router.

Invoked by Asterisk dialplan via AGI application. Handles the full
interactive call flow: play audio, collect DTMF, verify code,
and either connect to Upstairs Phone or hang up.
"""

import sys
from pathlib import Path

from agi.agi_channel import AGIChannel
from agi.router import Router


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
            handle_puzzle(channel, router, code, upstream_ext)
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

    for attempt in range(1, max_attempts + 1):
        remaining = max_attempts - attempt + 1
        channel.verbose(f"Attempt {attempt}/{max_attempts}")

        digit_count = len(code)
        entered = channel.read_digits(
            filename="silence/beam",
            num_digits=digit_count,
            timeout=15000,
        )

        if not entered:
            channel.verbose("No digits entered, hanging up")
            channel.hangup()
            return

        result = router.dispatch(code_attempt=entered)

        if result["outcome"] == "succeed":
            channel.verbose("Code accepted, connecting to upstream")
            channel.set_variable("UPSTREAM_EXT", upstream_ext)
            channel.exec_app("Goto", "pizza-success,s,1")
            return
        else:
            if attempt < max_attempts:
                channel.verbose("Wrong code, playing error tone")
                channel.stream_file("beep")
            else:
                channel.verbose("Max attempts reached, hanging up")

    channel.verbose("All attempts failed, hanging up")
    channel.hangup()


def handle_puzzle(
    channel: AGIChannel,
    router: Router,
    code: str,
    upstream_ext: str,
) -> None:
    """Puzzle mode: play audio puzzle, collect answer."""
    channel.verbose("Mode: puzzle — presenting audio puzzle")

    channel.stream_file("silence/beam")

    digit_count = len(code)
    answer = channel.read_digits(
        filename="silence/beam",
        num_digits=digit_count,
        timeout=30000,
    )

    result = router.dispatch(answer=answer)

    if result["outcome"] == "succeed":
        channel.verbose("Puzzle solved, connecting to upstream")
        channel.set_variable("UPSTREAM_EXT", upstream_ext)
        channel.exec_app("Goto", "pizza-success,s,1")
    else:
        channel.verbose("Puzzle failed, hanging up")
        channel.hangup()


def handle_roguelike(
    channel: AGIChannel,
    router: Router,
    code: str,
    upstream_ext: str,
) -> None:
    """Roguelike mode: navigate a DTMF phone tree."""
    channel.verbose("Mode: roguelike — navigating phone tree")

    path: list[str] = []
    max_depth = 5

    while len(path) < max_depth:
        choice = channel.read_digits(
            filename="silence/beam",
            num_digits=1,
            timeout=15000,
        )

        if not choice:
            break

        path.append(choice)
        channel.verbose(f"Path so far: {path}")

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
