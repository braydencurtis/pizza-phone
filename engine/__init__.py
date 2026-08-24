"""The Call Engine: one long-running asyncio process that owns live calls.

Phase 1 landed the thin ARI client (`ari_client`), the `CallIO` adapter
(`ari_call_io`), the session dispatch (`call_engine`, `call_session`) and the
call-history store (`call_store`). Phase 2 adds the Fake PBX development harness
(`fake_pbx`) and the dashboard WS/HTTP server.
"""
