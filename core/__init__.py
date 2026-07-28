"""Channel-agnostic game logic for the pizza phone.

`core/` holds the driver-independent pieces of the call experience: mode
handlers, the router/dispatch, puzzle selection, roguelike tree generation and
navigation, TTS, config, and the call-session record shape. It talks to the
outside world only through the `CallIO` protocol (see `core.call_io`) and never
imports a channel driver (AGI, ARI, …).
"""
