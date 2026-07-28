"""The Call Engine: one long-running asyncio process that owns live calls.

Phase 1 lands the thin ARI client (`ari_client`). Later phases add the
CallIO adapter, the CallSession dispatch, and the dashboard WS/HTTP server.
"""
