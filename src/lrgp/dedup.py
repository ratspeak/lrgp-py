"""Receiving-identity-scoped envelope replay-dedup cache.

LRGP envelopes carry a required 8-byte ``n`` nonce (see ``envelope.py``).
The receiver keeps a bounded, TTL'd cache of recently-seen
``(receiving_identity_id, session_id, nonce)`` tuples. The router first probes
without recording, authenticates the participant, and only then atomically
checks and records. This prevents unauthenticated fresh nonces from evicting
legitimate replay state while still resolving concurrent duplicate races.

Design constraints:

* Scoped by receiving identity and session id so legitimate reuse in another
  local identity or session cannot cause a false reject.
* LRU bound prevents unbounded growth inside a single long-running session.
* An outer LRU bound prevents unbounded growth in the number of sessions.
* TTL bound makes the cache forget nonces older than any realistic round
  trip, which limits memory for short-lived sessions that never reach
  terminal state.
* Terminal sessions retain their nonces through the normal TTL so late
  retransmits remain replays. Explicit session removal may call
  ``drop_session()``.
"""

import time
from collections import OrderedDict

from .constants import (
    DEDUP_CACHE_PER_SESSION, DEDUP_CACHE_SESSIONS, DEDUP_TTL_SECONDS,
    KEY_NONCE, KEY_SESSION, NONCE_BYTES,
)


class ReplayDedup:
    """Bounded LRU of scoped session/nonces with first-seen timestamps."""

    def __init__(self, max_per_session=DEDUP_CACHE_PER_SESSION,
                 ttl_seconds=DEDUP_TTL_SECONDS,
                 max_sessions=DEDUP_CACHE_SESSIONS):
        self._max = max_per_session
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._by_session = OrderedDict()  # session_id -> OrderedDict[bytes, float]

    def check(self, envelope, now=None, scope=""):
        """Decide whether ``envelope`` is a replay.

        Returns ``True`` if this envelope is a duplicate of one the caller
        has already processed (caller should drop it). Returns ``False``
        otherwise; in that case the nonce has been recorded and future
        arrivals with the same nonce for the same session will be
        flagged as replays.

        The envelope MUST be post-``unpack_envelope`` validated; missing or
        malformed fields here are a protocol violation and are dropped as
        replays.
        """
        nonce = envelope.get(KEY_NONCE)
        if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
            return True
        session_id = envelope.get(KEY_SESSION)
        if not isinstance(session_id, str):
            return True

        ts = time.monotonic() if now is None else now
        self.sweep(ts)
        cache_key = (scope, session_id)
        entries = self._by_session.get(cache_key)
        if entries is None:
            entries = OrderedDict()
            self._by_session[cache_key] = entries
        else:
            self._by_session.move_to_end(cache_key)

        if nonce in entries:
            # Refresh only LRU recency. The stored first-seen timestamp is
            # deliberately unchanged, so duplicate traffic cannot extend
            # the replay window forever.
            entries.move_to_end(nonce)
            return True

        entries[nonce] = ts
        entries.move_to_end(nonce)
        while len(entries) > self._max:
            entries.popitem(last=False)
        while len(self._by_session) > self._max_sessions:
            self._by_session.popitem(last=False)
        return False

    def probe(self, envelope, now=None, scope=""):
        """Check replay state without recording or evicting a fresh nonce.

        Routers call this before participant authorization, then call
        :meth:`check` while holding the same replay-cache lock after
        authorization. A replay refreshes bounded LRU order but retains its
        original timestamp; a fresh probe never creates a scope or nonce.
        """
        nonce = envelope.get(KEY_NONCE)
        if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
            return True
        session_id = envelope.get(KEY_SESSION)
        if not isinstance(session_id, str):
            return True

        ts = time.monotonic() if now is None else now
        self.sweep(ts)
        cache_key = (scope, session_id)
        entries = self._by_session.get(cache_key)
        if entries is None:
            return False
        self._by_session.move_to_end(cache_key)
        if nonce not in entries:
            return False
        entries.move_to_end(nonce)
        return True

    def drop_session(self, session_id, scope=None):
        """Forget nonces for one identity/session, or every matching session."""
        if scope is not None:
            self._by_session.pop((scope, session_id), None)
            return
        for cache_key in list(self._by_session):
            if cache_key[1] == session_id:
                self._by_session.pop(cache_key, None)

    def forget(self, session_id, nonce, scope=""):
        """Forget one scoped nonce after pre-dispatch authorization fails."""
        entries = self._by_session.get((scope, session_id))
        if entries is None:
            return False
        removed = entries.pop(bytes(nonce), None) is not None
        if not entries:
            self._by_session.pop((scope, session_id), None)
        return removed

    def sweep(self, now=None):
        """Prune expired entries and empty sessions, returning drop count."""
        ts = time.monotonic() if now is None else now
        dropped = 0
        for session_id, entries in list(self._by_session.items()):
            before = len(entries)
            self._prune_expired(entries, ts)
            dropped += before - len(entries)
            if not entries:
                self._by_session.pop(session_id, None)
        return dropped

    def _prune_expired(self, entries, now):
        cutoff = now - self._ttl
        for nonce, seen_at in list(entries.items()):
            if seen_at < cutoff:
                del entries[nonce]
