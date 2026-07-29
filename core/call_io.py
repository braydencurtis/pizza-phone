from __future__ import annotations

from typing import Protocol


class CallIO(Protocol):
    """The one seam between channel-agnostic game logic and a live call.

    `core/` drives a call entirely through this protocol; a channel driver
    (the ARI Call Engine, or the retired AGI scripts before it) supplies a
    concrete implementation. Keeping the surface this small is what lets the
    same flow logic run under either driver — and be exercised in tests with
    an in-memory fake.

    `media` arguments are driver-scoped identifiers (a ``sound:``/``recording:``
    URI under ARI; an Asterisk stream name like ``"voicemail/busy"`` under the
    old AGI driver). Core never constructs them; it only replays what the caller
    passed in, so the naming scheme stays the driver's concern.
    """

    def play(self, media: str) -> None:
        """Play a prerecorded prompt to the caller and wait for it to finish."""
        ...

    def read_dtmf(self, num_digits: int, timeout_ms: int) -> str:
        """Collect up to ``num_digits`` DTMF digits, returning them as a string.

        Returns an empty string if the caller entered nothing before the
        timeout (the flow treats that as a hang-up signal).
        """
        ...

    def speak(self, text: str) -> None:
        """Synthesize ``text`` to speech and play it to the caller."""
        ...

    def hangup(self) -> None:
        """Tear down the call."""
        ...

    def to_success(self) -> None:
        """Route the caller onto the success path (ring the Upstairs Phone)."""
        ...
