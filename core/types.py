from __future__ import annotations

from typing import Literal

Mode = Literal["tweeted", "puzzle", "roguelike"]
# Terminal outcomes a Call Session can end on. "succeed"/"fail"/"exile" come
# from the mode handlers; "hangup" is what core.flow returns when the caller
# picks up but enters nothing — a real outcome the engine persists as such.
Outcome = Literal["succeed", "fail", "exile", "hangup"]
