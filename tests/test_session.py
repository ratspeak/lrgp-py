"""Tests for RLAP session state machine and lifecycle."""

import time
import pytest
from lrgp.session import Session, SessionStateMachine
from lrgp.constants import (
    STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED,
    STATUS_EXPIRED, STATUS_DECLINED,
    CMD_CHALLENGE, CMD_ACCEPT, CMD_DECLINE, CMD_MOVE,
    CMD_RESIGN, CMD_DRAW_ACCEPT, CMD_DRAW_OFFER, CMD_DRAW_DECLINE,
    TTL_PENDING, TTL_ACTIVE, TTL_GRACE_PERIOD,
)
from lrgp.errors import IllegalTransition


class TestSession:
    def test_create_defaults(self):
        s = Session(session_id="abc")
        assert s.session_id == "abc"
        assert s.status == STATUS_PENDING
        assert s.metadata == {}
        assert s.identity_id == ""

    def test_to_dict_roundtrip(self):
        s = Session(session_id="abc", app_id="ttt", metadata={"key": "val"})
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.session_id == "abc"
        assert s2.app_id == "ttt"
        assert s2.metadata == {"key": "val"}


class TestStateMachine:
    def _make_session(self, status=STATUS_PENDING):
        return Session(session_id="test", status=status)

    def test_accept_transitions_to_active(self):
        s = self._make_session(STATUS_PENDING)
        result = SessionStateMachine.apply_command(s, CMD_ACCEPT)
        assert result == STATUS_ACTIVE
        assert s.status == STATUS_ACTIVE

    def test_decline_transitions_to_declined(self):
        s = self._make_session(STATUS_PENDING)
        result = SessionStateMachine.apply_command(s, CMD_DECLINE)
        assert result == STATUS_DECLINED
        assert s.status == STATUS_DECLINED

    def test_resign_transitions_to_completed(self):
        s = self._make_session(STATUS_ACTIVE)
        result = SessionStateMachine.apply_command(s, CMD_RESIGN)
        assert result == STATUS_COMPLETED

    def test_draw_accept_transitions_to_completed(self):
        s = self._make_session(STATUS_ACTIVE)
        result = SessionStateMachine.apply_command(s, CMD_DRAW_ACCEPT)
        assert result == STATUS_COMPLETED

    def test_move_stays_active(self):
        s = self._make_session(STATUS_ACTIVE)
        result = SessionStateMachine.apply_command(s, CMD_MOVE)
        assert result == STATUS_ACTIVE

    def test_terminal_move_completes(self):
        s = self._make_session(STATUS_ACTIVE)
        result = SessionStateMachine.apply_command(s, CMD_MOVE, terminal=True)
        assert result == STATUS_COMPLETED

    def test_draw_offer_stays_active(self):
        s = self._make_session(STATUS_ACTIVE)
        result = SessionStateMachine.apply_command(s, CMD_DRAW_OFFER)
        assert result == STATUS_ACTIVE

    def test_draw_decline_stays_active(self):
        s = self._make_session(STATUS_ACTIVE)
        result = SessionStateMachine.apply_command(s, CMD_DRAW_DECLINE)
        assert result == STATUS_ACTIVE

    def test_challenge_on_pending_ok(self):
        s = self._make_session(STATUS_PENDING)
        result = SessionStateMachine.apply_command(s, CMD_CHALLENGE)
        assert result == STATUS_PENDING

    # --- Illegal transitions ---

    def test_move_on_pending_raises(self):
        s = self._make_session(STATUS_PENDING)
        with pytest.raises(IllegalTransition):
            SessionStateMachine.apply_command(s, CMD_MOVE)

    def test_accept_on_active_raises(self):
        s = self._make_session(STATUS_ACTIVE)
        with pytest.raises(IllegalTransition):
            SessionStateMachine.apply_command(s, CMD_ACCEPT)

    def test_resign_on_pending_raises(self):
        s = self._make_session(STATUS_PENDING)
        with pytest.raises(IllegalTransition):
            SessionStateMachine.apply_command(s, CMD_RESIGN)

    def test_challenge_on_active_raises(self):
        s = self._make_session(STATUS_ACTIVE)
        with pytest.raises(IllegalTransition):
            SessionStateMachine.apply_command(s, CMD_CHALLENGE)

    def test_move_on_completed_raises(self):
        s = self._make_session(STATUS_COMPLETED)
        with pytest.raises(IllegalTransition):
            SessionStateMachine.apply_command(s, CMD_MOVE)


class TestExpiry:
    def test_pending_expires_after_ttl(self):
        s = Session(session_id="test", status=STATUS_PENDING)
        s.last_action_at = time.time() - TTL_PENDING - TTL_GRACE_PERIOD - 1
        assert SessionStateMachine.check_expiry(s) is True
        assert s.status == STATUS_EXPIRED

    def test_active_expires_after_ttl(self):
        s = Session(session_id="test", status=STATUS_ACTIVE)
        s.last_action_at = time.time() - TTL_ACTIVE - TTL_GRACE_PERIOD - 1
        assert SessionStateMachine.check_expiry(s) is True
        assert s.status == STATUS_EXPIRED

    def test_pending_within_ttl_does_not_expire(self):
        s = Session(session_id="test", status=STATUS_PENDING)
        s.last_action_at = time.time()
        assert SessionStateMachine.check_expiry(s) is False
        assert s.status == STATUS_PENDING

    def test_completed_never_expires(self):
        s = Session(session_id="test", status=STATUS_COMPLETED)
        s.last_action_at = 0
        assert SessionStateMachine.check_expiry(s) is False
        assert s.status == STATUS_COMPLETED

    def test_custom_ttl(self):
        s = Session(session_id="test", status=STATUS_PENDING)
        s.last_action_at = time.time() - 100
        # Short TTL should expire
        assert SessionStateMachine.check_expiry(
            s, ttl={"pending": 10}, now=time.time() + TTL_GRACE_PERIOD + 100
        ) is True

    def test_grace_period(self):
        s = Session(session_id="test", status=STATUS_PENDING)
        now = time.time()
        # Exactly at TTL boundary but within grace period
        s.last_action_at = now - TTL_PENDING
        assert SessionStateMachine.check_expiry(s, now=now) is False
