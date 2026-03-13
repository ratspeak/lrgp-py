"""Tests for RLAP SQLite store."""

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

    def test_save_session_from_dict(self, store):
        d = {
            "session_id": "s1", "identity_id": "id1", "app_id": "ttt",
            "contact_hash": "c1", "status": "pending",
            "metadata": {"key": "val"},
        }
        store.save_session(d)
        result = store.get_session("s1", "id1")
        assert result["metadata"]["key"] == "val"


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
