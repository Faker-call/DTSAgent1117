from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pmagent.contracts import Case, SessionState
from pmagent.errors import BudgetExceeded
from pmagent.memory import (
    BudgetLedger,
    Card,
    ContextCompressor,
    EvidenceLedger,
    MemoryStore,
    ResultCache,
    RunEventLog,
    extract_facts,
)


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memory.db")
    yield store
    store.close()


def test_working_memory_is_process_local(store: MemoryStore) -> None:
    store.working.set("k", 1)
    assert store.working.get("k") == 1
    store.working.clear()
    assert len(store.working) == 0


def test_session_roundtrip(store: MemoryStore) -> None:
    state = SessionState(id="s1")
    state.slots.sdk_version = "6.0.1"
    store.save_session(state)
    loaded = store.load_session("s1")
    assert loaded is not None
    assert loaded.slots.sdk_version == "6.0.1"
    assert [item["id"] for item in store.list_sessions()] == ["s1"]


def test_session_retention_purges_old(store: MemoryStore) -> None:
    store.save_session(SessionState(id="old"))
    past = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    store._conn.execute("UPDATE sessions SET updated_at = ? WHERE id = 'old'", (past,))
    store._conn.commit()
    result = store.purge_expired()
    assert result["sessions"] == 1
    assert store.load_session("old") is None


def test_run_roundtrip(store: MemoryStore) -> None:
    store.save_run("r1", kind="diagnose", status="running", index_version="abc", state={"stage": 2})
    run = store.load_run("r1")
    assert run is not None
    assert run["kind"] == "diagnose"
    assert run["state"] == {"stage": 2}
    assert store.list_runs()[0]["run_id"] == "r1"


def test_case_crud(store: MemoryStore) -> None:
    case = Case(case_id="c1", symptom="缝隙", root_cause="未闭合", root_cause_file="a.cc")
    store.add_case(case, dedupe_key="k1")
    assert store.find_case_by_dedupe("k1") is not None
    assert [item.case_id for item in store.list_cases()] == ["c1"]
    store.set_case_status("c1", "expired")
    assert store.list_cases() == []
    store.delete_case("c1")
    assert store.case_status("c1") is None


def test_ledger_cards_and_rehydrate() -> None:
    ledger = EvidenceLedger()
    item = ledger.register(
        kind="code",
        ref={"path": "src/a.cc", "line_start": 10, "line_end": 20},
        summary="BuildStrip",
        body="full body",
        score=0.8,
        channels=["bm25"],
    )
    card = ledger.card(item.evidence_id)
    assert "E1" in card and "src/a.cc:10-20" in card
    assert ledger.rehydrate(item.evidence_id) == "full body"
    assert ledger.cards() == [card]
    assert len(ledger) == 1


def test_ledger_reloader(store: MemoryStore) -> None:
    ledger = EvidenceLedger()
    item = ledger.register(
        kind="log",
        ref={"path": "a.log"},
        summary="lazy",
        reloader=lambda: "loaded body",
    )
    assert ledger.body(item.evidence_id) == "loaded body"


def test_compression_level1_keeps_facts_and_does_not_mutate_ledger() -> None:
    ledger = EvidenceLedger()
    item = ledger.register(
        kind="code",
        ref={"path": "a.cc"},
        body="\n".join(["pm::BuildStrip", "line2", "line3", "line4", "line5"]),
        score=0.9,
    )
    cards = [Card(item.evidence_id, ledger.body(item.evidence_id), 0.9)]
    facts = extract_facts(ledger.body(item.evidence_id))
    result = ContextCompressor().compress(cards, facts=facts, target_chars=100)
    assert "pm::BuildStrip" in result.text
    assert ledger.body(item.evidence_id).count("line5") == 1
    assert len(ledger) == 1


def test_compression_rolls_back_when_facts_lost() -> None:
    cards = [
        Card("E1", "first line pm::A\nsecond line E404", 1.0),
        Card("E2", "unrelated text\nanother line", 0.5),
    ]
    facts = {"pm::A", "E404"}
    result = ContextCompressor().compress(cards, facts=facts, target_chars=10)
    assert result.coverage >= 0.8
    assert not result.degraded


def test_budget_ledger() -> None:
    ledger = BudgetLedger(total=100)
    ledger.allocate("retrieve", 40)
    ledger.consume("retrieve", 30)
    assert ledger.stage_remaining("retrieve") == 10
    assert ledger.remaining == 70
    with pytest.raises(BudgetExceeded):
        ledger.consume("retrieve", 200)


def test_event_log_append_and_resume(tmp_path: Path) -> None:
    log = RunEventLog(tmp_path / "r1")
    log.append("stage_started", stage="normalize")
    log.append("stage_finished", stage="normalize", status="ok")
    log.append("stage_started", stage="triage")
    log.checkpoint({"stage": "triage", "progress": 1})

    point = log.resume(["normalize", "triage", "report"])
    assert point.next_stage == "triage"
    assert point.completed == ["normalize"]
    assert point.state["stage"] == "triage"

    log.append("interrupted")
    assert log.interrupted()
    assert len(log.read()) == 4


def test_checkpoint_is_atomic(tmp_path: Path) -> None:
    log = RunEventLog(tmp_path / "r1")
    log.checkpoint({"a": 1})
    log.checkpoint({"a": 2})
    assert json.loads(log.state_path.read_text(encoding="utf-8")) == {"a": 2}
    assert not log.state_path.with_suffix(".json.tmp").exists()


def test_result_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = ResultCache(path)
    assert cache.get("k") is None
    cache.put("k", {"v": 1})
    assert cache.get("k") == {"v": 1}
    assert cache.hits == 1 and cache.misses == 1

    reopened = ResultCache(path)
    assert reopened.get("k") == {"v": 1}


def test_cached_caller_avoids_second_call(tmp_path: Path) -> None:
    from pmagent.memory.cache import CachedCaller

    cache = ResultCache()
    caller = CachedCaller(cache, "tool")
    calls = {"n": 0}

    def fn() -> dict:
        calls["n"] += 1
        return {"ok": True}

    assert caller.call({"q": "x"}, fn) == {"ok": True}
    assert caller.call({"q": "x"}, fn) == {"ok": True}
    assert calls["n"] == 1
