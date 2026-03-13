"""Tests for LRGP game router."""

import pytest
from lrgp.router import register, unregister, get_app, list_apps, dispatch_incoming, dispatch_outgoing
from lrgp.errors import UnknownApp
from lrgp.apps.tictactoe import TicTacToeApp


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure clean registry for each test."""
    import lrgp.router as r
    old = r._registry.copy()
    r._registry.clear()
    yield
    r._registry.clear()
    r._registry.update(old)


class TestRegister:
    def test_register_and_get(self):
        app = TicTacToeApp()
        register(app)
        assert get_app("ttt") is app

    def test_unregister(self):
        app = TicTacToeApp()
        register(app)
        unregister("ttt")
        assert get_app("ttt") is None

    def test_list_apps(self):
        app = TicTacToeApp()
        register(app)
        manifests = list_apps()
        assert len(manifests) == 1
        assert manifests[0]["app_id"] == "ttt"
        assert manifests[0]["display_name"] == "Tic-Tac-Toe"


class TestDiscover:
    def test_discover_ttt(self):
        from lrgp.router import discover
        import lrgp.apps
        discover(lrgp.apps)
        assert get_app("ttt") is not None


class TestDispatch:
    def test_dispatch_incoming_unknown_app(self):
        with pytest.raises(UnknownApp):
            dispatch_incoming(
                {"a": "unknown.1", "c": "challenge", "s": "abc", "p": {}},
                "sender123",
            )

    def test_dispatch_incoming_challenge(self):
        app = TicTacToeApp()
        register(app)
        result = dispatch_incoming(
            {"a": "ttt.1", "c": "challenge", "s": "abc123", "p": {}},
            "sender_hash", "my_id",
        )
        assert result is not None
        assert result["session"]["status"] == "pending"
        assert result["emit"]["type"] == "challenge"

    def test_dispatch_outgoing_unknown_app(self):
        with pytest.raises(UnknownApp):
            dispatch_outgoing("unknown", "challenge", {}, "abc")

    def test_dispatch_outgoing_challenge(self):
        app = TicTacToeApp()
        register(app)
        envelope, fallback, delivery = dispatch_outgoing(
            "ttt", "challenge", {}, "sess123", "my_id",
        )
        assert envelope["a"] == "ttt.1"
        assert envelope["c"] == "challenge"
        assert "[LRGP TTT]" in fallback
        assert delivery == "opportunistic"
