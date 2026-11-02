"""Receiving-identity-scoped envelope replay cache.

LRGP envelopes carry a required 8-byte ``n`` nonce. The router probes the
``(receiving_identity_id, session_id, nonce)`` cache before participant
checks, then checks and records under its replay-cache lock after those
checks. The caller must authenticate the transport sender before dispatch.

Each scope has a nonce LRU bound; an outer LRU bounds the number of scopes.
TTL uses first-seen monotonic timestamps, so repeats do not extend it.
Capacity eviction, expiry and explicit removal can make a previously seen
nonce fresh again. Terminal sessions use the same limits as other sessions;
there is no permanent or durable replay guarantee.
"""

import time
from collections import OrderedDict

from .constants import (
    DEDUP_CACHE_PER_SESSION, DEDUP_CACHE_SESSIONS, DEDUP_TTL_SECONDS,
    KEY_NONCE, KEY_SESSION, NONCE_BYTES,
)


class ReplayDedup:
    """Bounded LRU of scoped session/nonces with first-seen timestamps.

    Callers must serialize access and provide sensible nonnegative capacities
    and a TTL. The router supplies the lock when it owns this cache.
    """

    def __init__(self, max_per_session=DEDUP_CACHE_PER_SESSION,
                 ttl_seconds=DEDUP_TTL_SECONDS,
                 max_sessions=DEDUP_CACHE_SESSIONS):
        self._max = max_per_session
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._by_session = OrderedDict()  # session_id -> OrderedDict[bytes, float]

    def check(self, envelope, now=None, scope=""):
        """Check and record replay state for a validated envelope.

        Return True for a retained duplicate or an invalid nonce/session field;
        return False after recording a fresh nonce and enforcing capacity limits.
        A zero capacity can immediately evict that new record. Later arrivals are
        replays only while the scoped record remains cached.

        Use an envelope already validated by ``unpack_envelope``. This method
        checks only nonce type/length and session string type, not the full schema.
        ``scope`` identifies the receiving local identity. ``now`` uses the same
        monotonic time base as earlier calls, or defaults to time.monotonic().
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
        """Forget one scoped nonce, returning whether it existed.

        This also supports explicit durable-persistence rollback; it does not
        restore any application state.
        """
        entries = self._by_session.get((scope, session_id))
        if entries is None:
            return False
        removed = entries.pop(bytes(nonce), None) is not None
        if not entries:
            self._by_session.pop((scope, session_id), None)
        return removed

    def sweep(self, now=None):
        """Prune expired entries and empty scopes, returning the nonce count removed.

        A nonce expires when its age is strictly greater than the configured TTL.
        """
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
