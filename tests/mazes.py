"""Which maze a seed builds — the one home for the trees the tests rely on.

``mode_roguelike.make_tree`` regenerates the tree per Call Session, so a walk on
an unseeded tree can end any of three ways and no test that cares which one it
got can go unseeded. These are the two trees worth naming, both described by
what a caller who mashes a single key does on them, because that is the caller
who mostly reaches either ending (CONTEXT.md, *Walking the maze out*).

They live here rather than in each test file because three files now depend on
these exact tree shapes, and a seed whose meaning is restated per file is a seed
that will eventually be restated wrongly.
"""

from __future__ import annotations

# Pressing "1" every time walks into the room holding the Code, in two moves.
SOLVING_SEED = 42

# Pressing "1" every time closes into a loop of two rooms and never finds it, so
# the walk runs out the Walk Bound and the caller is Exiled.
CYCLING_SEED = 0

# Keys that win on CYCLING_SEED — the tree that Exiles a key-masher. The pair is
# the point: one tree, and only the caller's own keys separate the win from the
# loss, which is what a record built from a simulated walk could not show (#56).
WINNING_KEYS_ON_CYCLING_TREE = ["1", "1", "2", "2"]
