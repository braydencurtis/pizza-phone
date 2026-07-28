from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from engine.call_store import CallRecord, CallStore, new_session_id


def _record(**overrides: Any) -> CallRecord:
    base: dict[str, Any] = {
        "session_id": new_session_id(),
        "started_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        "ended_at": datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC),
        "mode": "tweeted",
        "outcome": "succeed",
        "duration_seconds": 30.0,
        "attempts": 1,
    }
    base.update(overrides)
    return CallRecord(**base)


async def _store(tmp_path: Path) -> CallStore:
    store = CallStore(tmp_path / "calls.db")
    await store.initialize()
    return store


class TestSchema:

    def test_initialize_creates_calls_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "calls.db"
        asyncio.run(CallStore(db_path).initialize())

        with sqlite3.connect(db_path) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "calls" in names

    def test_initialize_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "calls.db"
        asyncio.run(CallStore(db_path).initialize())
        # A second initialize must not raise (CREATE ... IF NOT EXISTS).
        asyncio.run(CallStore(db_path).initialize())

    def test_initialize_creates_parent_directory(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "dir" / "calls.db"
        asyncio.run(CallStore(db_path).initialize())
        assert db_path.exists()


class TestAddAndGet:

    def test_roundtrips_all_fields(self, tmp_path: Path) -> None:
        async def run() -> CallRecord | None:
            store = await _store(tmp_path)
            record = _record(
                mode="roguelike",
                outcome="fail",
                duration_seconds=312.5,
                attempts=0,
                caller_id="+15550001234",
                detail={"path": ["1", "3", "2"], "nodes_visited": ["a", "b"]},
                recording_mixed="/rec/mixed.wav",
                recording_in="/rec/in.wav",
                recording_out="/rec/out.wav",
            )
            await store.add(record)
            return await store.get(record.session_id)

        got = asyncio.run(run())
        assert got is not None
        assert got.mode == "roguelike"
        assert got.outcome == "fail"
        assert got.duration_seconds == 312.5
        assert got.attempts == 0
        assert got.caller_id == "+15550001234"
        assert got.detail == {"path": ["1", "3", "2"], "nodes_visited": ["a", "b"]}
        assert got.recording_mixed == "/rec/mixed.wav"
        assert got.recording_in == "/rec/in.wav"
        assert got.recording_out == "/rec/out.wav"
        assert got.started_at == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert got.ended_at == datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)

    def test_defaults_leave_optional_columns_null(self, tmp_path: Path) -> None:
        async def run() -> CallRecord | None:
            store = await _store(tmp_path)
            record = _record()
            await store.add(record)
            return await store.get(record.session_id)

        got = asyncio.run(run())
        assert got is not None
        assert got.caller_id is None
        assert got.detail == {}
        assert got.recording_mixed is None
        assert got.recording_in is None
        assert got.recording_out is None

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        async def run() -> CallRecord | None:
            store = await _store(tmp_path)
            return await store.get("does-not-exist")

        assert asyncio.run(run()) is None

    def test_duplicate_session_id_raises(self, tmp_path: Path) -> None:
        async def run() -> None:
            store = await _store(tmp_path)
            record = _record()
            await store.add(record)
            await store.add(record)

        try:
            asyncio.run(run())
        except sqlite3.IntegrityError:
            return
        raise AssertionError("expected IntegrityError on duplicate session_id")


class TestQuery:

    def test_filters_by_mode(self, tmp_path: Path) -> None:
        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            await store.add(_record(mode="tweeted"))
            await store.add(_record(mode="puzzle"))
            await store.add(_record(mode="puzzle"))
            return await store.query(mode="puzzle")

        results = asyncio.run(run())
        assert len(results) == 2
        assert all(r.mode == "puzzle" for r in results)

    def test_filters_by_outcome(self, tmp_path: Path) -> None:
        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            await store.add(_record(outcome="succeed"))
            await store.add(_record(outcome="exile"))
            return await store.query(outcome="exile")

        results = asyncio.run(run())
        assert len(results) == 1
        assert results[0].outcome == "exile"

    def test_filters_by_date_range(self, tmp_path: Path) -> None:
        jan = datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC)
        feb = datetime(2026, 2, 15, 9, 0, 0, tzinfo=UTC)
        mar = datetime(2026, 3, 15, 9, 0, 0, tzinfo=UTC)

        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            await store.add(_record(started_at=jan))
            await store.add(_record(started_at=feb))
            await store.add(_record(started_at=mar))
            # since is inclusive, until is exclusive
            return await store.query(
                since=datetime(2026, 2, 1, tzinfo=UTC),
                until=datetime(2026, 3, 1, tzinfo=UTC),
            )

        results = asyncio.run(run())
        assert len(results) == 1
        assert results[0].started_at == feb

    def test_orders_most_recent_first(self, tmp_path: Path) -> None:
        early = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
        late = datetime(2026, 1, 1, 20, 0, 0, tzinfo=UTC)

        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            await store.add(_record(started_at=early))
            await store.add(_record(started_at=late))
            return await store.query()

        results = asyncio.run(run())
        assert [r.started_at for r in results] == [late, early]

    def test_respects_limit(self, tmp_path: Path) -> None:
        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            for hour in range(5):
                await store.add(
                    _record(started_at=datetime(2026, 1, 1, hour, tzinfo=UTC))
                )
            return await store.query(limit=2)

        assert len(asyncio.run(run())) == 2

    def test_combines_filters(self, tmp_path: Path) -> None:
        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            await store.add(_record(mode="puzzle", outcome="succeed"))
            await store.add(_record(mode="puzzle", outcome="exile"))
            await store.add(_record(mode="tweeted", outcome="exile"))
            return await store.query(mode="puzzle", outcome="exile")

        results = asyncio.run(run())
        assert len(results) == 1
        assert results[0].mode == "puzzle"
        assert results[0].outcome == "exile"

    def test_non_utc_offsets_compare_by_instant(self, tmp_path: Path) -> None:
        # 08:00-05:00 is 13:00Z; 09:00+02:00 is 07:00Z — so despite the larger
        # wall-clock reading, the second call is the earlier instant. A store
        # that normalizes to UTC before lexical comparison must order and
        # filter them by instant, not by the raw offset string.
        eastern = datetime(2026, 1, 1, 8, 0, tzinfo=timezone(timedelta(hours=-5)))
        central_eu = datetime(2026, 1, 1, 9, 0, tzinfo=timezone(timedelta(hours=2)))

        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            await store.add(_record(started_at=eastern))
            await store.add(_record(started_at=central_eu))
            # Window 06:00Z–08:00Z catches only the +02:00 call (07:00Z).
            return await store.query(
                since=datetime(2026, 1, 1, 6, 0, tzinfo=UTC),
                until=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
            )

        results = asyncio.run(run())
        assert len(results) == 1
        assert results[0].started_at == central_eu

    def test_naive_datetime_is_rejected(self, tmp_path: Path) -> None:
        async def run() -> None:
            store = await _store(tmp_path)
            await store.add(_record(started_at=datetime(2026, 1, 1, 12, 0)))  # noqa: DTZ001

        try:
            asyncio.run(run())
        except ValueError:
            return
        raise AssertionError("expected ValueError for a naive datetime")

    def test_empty_store_returns_empty_list(self, tmp_path: Path) -> None:
        async def run() -> list[CallRecord]:
            store = await _store(tmp_path)
            return await store.query()

        assert asyncio.run(run()) == []


class TestSessionId:

    def test_new_session_id_is_unique(self) -> None:
        ids = {new_session_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_new_session_id_is_str(self) -> None:
        assert isinstance(new_session_id(), str)
