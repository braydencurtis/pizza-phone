from __future__ import annotations

from typing import Literal

Mode = Literal["tweeted", "puzzle", "roguelike"]
# Terminal outcomes a Call Session can end on. "succeed"/"fail"/"exile" come
# from the mode handlers; "hangup" is what core.flow returns when the caller
# picks up but enters nothing — a real outcome the engine persists as such.
Outcome = Literal["succeed", "fail", "exile", "hangup"]
# The two ways a Walk through the Roguelike Phone-Tree can end, and a subset of
# Outcome: the Code read out, or the caller gone. The maze has neither Exile nor
# anything to get wrong — "no lives or attempt limits" (CONTEXT.md) — so the
# other two outcomes cannot arise there.
WalkOutcome = Literal["succeed", "hangup"]
