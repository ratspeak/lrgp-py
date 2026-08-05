"""Tests for LRGP SQLite store."""

import sqlite3
import threading
import time
import pytest
from lrgp.store import LrgpStore
from lrgp.session import Session


@pytest.fixture
def store():
    return LrgpStore(":memory:")


class TestSessionCRUD:
    def test_save_and_get(self, store):
        s = Session(session_id="s1", identity_id="id1", app_id="ttt",
                    contact_hash="contact1", metadata={"board": "_________"})
        store.save_session(s)
        result = store.get_session("s1", "id1")
        assert result is not None
        assert result["session_id"] == "s1"
        assert result["app_id"] == "ttt"
        assert result["metadata"]["board"] == "_________"

    def test_get_missing_returns_none(self, store):
        assert store.get_session("nonexistent") is None

    def test_update_session(self, store):
        s = Session(session_id="s1", identity_id="id1", app_id="ttt",
                    contact_hash="c1", status="pending")
        store.save_session(s)
        store.update_session("s1", "id1", status="active",
                             metadata={"board": "X________"})
        result = store.get_session("s1", "id1")
        assert result["status"] == "active"
        assert result["metadata"]["board"] == "X________"

    def test_list_sessions(self, store):
        for i in range(3):
            s = Session(session_id="s{}".format(i), identity_id="id1",
                        app_id="ttt", contact_hash="c1")
            store.save_session(s)
        results = store.list_sessions("id1")
        assert len(results) == 3

    def test_list_sessions_filter_status(self, store):
        s1 = Session(session_id="s1", identity_id="id1", app_id="ttt",
                     contact_hash="c1", status="pending")
        s2 = Session(session_id="s2", identity_id="id1", app_id="ttt",
                     contact_hash="c1", status="active")
        store.save_session(s1)
        store.save_session(s2)
        results = store.list_sessions("id1", status="active")
        assert len(results) == 1
        assert results[0]["session_id"] == "s2"

    def test_delete_session(self, store):
        s = Session(session_id="s1", identity_id="id1", app_id="ttt",
                    contact_hash="c1")
        store.save_session(s)
        store.save_action("s1", "id1", 1, "challenge", {}, "sender1")
        store.delete_session("s1", "id1")
        assert store.get_session("s1", "id1") is None
        assert store.get_actions("s1", "id1") == []

    def test_delete_session_and_actions_roll_back_as_one_transaction(self, store):
        s = Session(session_id="s1", identity_id="id1", app_id="ttt",
                    contact_hash="c1")
        store.save_session(s)
        store.save_action("s1", "id1", 1, "challenge", {}, "sender1")
        store._get_conn().execute(
            """CREATE TRIGGER reject_action_delete
               BEFORE DELETE ON game_actions
               BEGIN SELECT RAISE(ABORT, 'test rollback'); END"""
        )

        with pytest.raises(sqlite3.IntegrityError):
            store.delete_session("s1", "id1")

        assert store.get_session("s1", "id1") is not None
        assert len(store.get_actions("s1", "id1")) == 1

    def test_save_session_from_dict(self, store):
        d = {
            "session_id": "s1", "identity_id": "id1", "app_id": "ttt",
            "contact_hash": "c1", "status": "pending",
            "metadata": {"key": "val"},
        }
        store.save_session(d)
        result = store.get_session("s1", "id1")
        assert result["metadata"]["key"] == "val"

    def test_update_rejects_unknown_or_immutable_identifier(self, store):
        store.save_session(Session(
            session_id="s1", identity_id="id1", app_id="ttt",
            contact_hash="c1",
        ))
        with pytest.raises(ValueError):
            store.update_session("s1", "id1", **{"status = 'active' --": "x"})
        with pytest.raises(ValueError):
            store.update_session("s1", "id1", contact_hash="attacker")
        assert store.get_session("s1", "id1")["contact_hash"] == "c1"

    def test_duplicate_save_cannot_replace_established_session(self, store):
        original = Session(
            session_id="s1", identity_id="id1", app_id="ttt",
            contact_hash="peer", initiator="me", status="pending",
        )
        store.save_session(original)
        for changed in (
            Session(session_id="s1", identity_id="id1", app_id="chess",
                    contact_hash="peer", initiator="me"),
            Session(session_id="s1", identity_id="id1", app_id="ttt",
                    contact_hash="attacker", initiator="me"),
            Session(session_id="s1", identity_id="id1", app_id="ttt",
                    contact_hash="peer", initiator="attacker"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                store.save_session(changed)
        assert store.get_session("s1", "id1")["contact_hash"] == "peer"

    def test_duplicate_save_fails_and_explicit_update_mutates_allowlist(self, store):
        original = Session(
            session_id="s1", identity_id="id1", app_id="ttt",
            contact_hash="peer", initiator="me", status="pending",
            created_at=10,
        )
        store.save_session(original)
        updated = Session.from_dict(original.to_dict())
        updated.status = "active"
        updated.metadata = {"board": "_________"}
        updated.created_at = 99
        with pytest.raises(sqlite3.IntegrityError):
            store.save_session(updated)
        unchanged = store.get_session("s1", "id1")
        assert unchanged["status"] == "pending"
        assert unchanged["metadata"] == {}
        assert unchanged["created_at"] == 10

        store.update_session(
            "s1", "id1", status="active", metadata={"board": "_________"}
        )
        stored = store.get_session("s1", "id1")
        assert stored["status"] == "active"
        assert stored["metadata"] == {"board": "_________"}
        assert stored["created_at"] == 10

    def test_memory_store_is_shared_across_threads(self, store):
        store.save_session(Session(
            session_id="s1", identity_id="id1", app_id="ttt",
            contact_hash="peer",
        ))
        seen = []
        thread = threading.Thread(
            target=lambda: seen.append(store.get_session("s1", "id1"))
        )
        thread.start()
        thread.join()
        assert seen[0]["app_id"] == "ttt"


class TestActionCRUD:
    def test_save_and_get_actions(self, store):
        store.save_action("s1", "id1", 1, "challenge", {}, "sender1")
        store.save_action("s1", "id1", 2, "accept", {"b": "_________"}, "sender2")
        actions = store.get_actions("s1", "id1")
        assert len(actions) == 2
        assert actions[0]["command"] == "challenge"
        assert actions[1]["command"] == "accept"
        assert actions[1]["payload"]["b"] == "_________"

    def test_action_count(self, store):
        store.save_action("s1", "id1", 1, "challenge", {}, "sender1")
        store.save_action("s1", "id1", 2, "accept", {}, "sender2")
        assert store.get_action_count("s1", "id1") == 2

    def test_action_ordering(self, store):
        store.save_action("s1", "id1", 3, "move", {}, "a")
        store.save_action("s1", "id1", 1, "challenge", {}, "a")
        store.save_action("s1", "id1", 2, "accept", {}, "b")
        actions = store.get_actions("s1", "id1")
        assert [a["action_num"] for a in actions] == [1, 2, 3]

    def test_duplicate_action_number_never_overwrites_history(self, store):
        store.save_action("s1", "id1", 1, "challenge", {}, "sender1")
        with pytest.raises(sqlite3.IntegrityError):
            store.save_action("s1", "id1", 1, "move", {"i": 4}, "sender2")
        actions = store.get_actions("s1", "id1")
        assert len(actions) == 1
        assert actions[0]["command"] == "challenge"
        assert actions[0]["sender"] == "sender1"
