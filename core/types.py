from __future__ import annotations

from typing import Literal

Mode = Literal["tweeted", "puzzle", "roguelike"]
# Terminal outcomes a Call Session can end on. "succeed"/"fail"/"exile" come
# from the mode handlers; "hangup" is what core.flow returns when the caller
# picks up but enters nothing — a real outcome the engine persists as such, and
# also what the engine records for a caller who put the handset down mid-call,
# since that is the same ending reaching us by a different route (#50).
#
# "dropped" is the one outcome no mode handler can return: the *engine* ended
# the call, after an exception with the caller still on the line. It exists so
# that a failure of ours is persisted as a failure of ours — folding it into
# "hangup" would quietly credit the caller with walking away and poison every
# count drawn off the history.
Outcome = Literal["succeed", "fail", "exile", "hangup", "dropped"]
# The three ways a Walk through the Roguelike Phone-Tree can end, and a subset
# of Outcome: the Code read out at the room that holds it, the caller gone, or
# the walk run out to its bound without ever finding that room — which is Exile,
# the same flavoured disconnect the other Modes give a caller who runs out of
# attempts (#59). The maze still has nothing to get *wrong*, so "no lives or
# attempt limits" holds; what it now has is a way to lose. "fail" is the one
# Outcome that cannot arise here: a walk either found the room or it did not.
WalkOutcome = Literal["succeed", "hangup", "exile"]
