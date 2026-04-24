"""Tests for the per-session replay-dedup cache."""

import pytest

from lrgp.dedup import ReplayDedup
from lrgp.envelope import pack_envelope
from lrgp.constants import KEY_NONCE


def _env(session, nonce):
    return pack_envelope("ttt", 1, "move", session, {}, nonce=nonce)


class TestFirstSightingAccepted:
    def test_fresh_nonce_is_not_a_replay(self):
        d = ReplayDedup()
        assert d.check(_env("s1", b"\x00" * 8)) is False


class TestReplayDetection:
    def test_same_nonce_same_session_is_replay(self):
        d = ReplayDedup()
        env = _env("s1", b"\x11" * 8)
        assert d.check(env) is False
        assert d.check(env) is True
        # Third and subsequent arrivals should stay flagged as replays.
        assert d.check(env) is True

    def test_same_nonce_different_session_is_not_replay(self):
        d = ReplayDedup()
        n = b"\x22" * 8
        assert d.check(_env("s1", n)) is False
        assert d.check(_env("s2", n)) is False

    def test_different_nonce_same_session_is_not_replay(self):
        d = ReplayDedup()
        assert d.check(_env("s1", b"\x33" * 8)) is False
        assert d.check(_env("s1", b"\x44" * 8)) is False


class TestLegacyPeerCompat:
    def test_envelope_without_nonce_is_never_a_replay(self):
        d = ReplayDedup()
        legacy = {"a": "ttt.1", "c": "move", "s": "s1", "p": {}}
        # A legacy peer could reasonably retransmit the same message; we
        # cannot dedup without a nonce. Verify we never spuriously report
        # these as replays.
        assert d.check(legacy) is False
        assert d.check(legacy) is False


class TestLruEviction:
    def test_oldest_nonce_evicted_once_cap_exceeded(self):
        d = ReplayDedup(max_per_session=4)
        # Fill the cache.
        for i in range(4):
            n = bytes([i]) + b"\x00" * 7
            assert d.check(_env("s1", n)) is False
        # One more pushes out the first.
        evicting = b"\x09" + b"\x00" * 7
        assert d.check(_env("s1", evicting)) is False
        # The evicted nonce is now unknown again — retransmit treated as fresh.
        first = b"\x00" * 8
        assert d.check(_env("s1", first)) is False
        # But the nonces still in the cache are still flagged.
        recent = bytes([3]) + b"\x00" * 7
        assert d.check(_env("s1", recent)) is True


class TestTtlExpiry:
    def test_stale_entry_is_not_a_replay(self):
        # Use the injected-clock escape hatch so we don't rely on wall time.
        d = ReplayDedup(max_per_session=8, ttl_seconds=10)
        n = b"\x77" * 8
        assert d.check(_env("s1", n), now=0.0) is False
        # Well past the TTL — the old entry must be forgotten before we
        # check the new arrival, otherwise this would flag as a replay.
        assert d.check(_env("s1", n), now=100.0) is False


class TestDropSession:
    def test_drop_session_clears_cache(self):
        d = ReplayDedup()
        n = b"\x55" * 8
        assert d.check(_env("s1", n)) is False
        d.drop_session("s1")
        # Same nonce after session closes should be accepted again (new session).
        assert d.check(_env("s1", n)) is False

    def test_drop_session_unknown_is_a_noop(self):
        d = ReplayDedup()
        d.drop_session("never-seen")  # must not raise
