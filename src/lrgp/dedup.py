"""Per-session envelope replay-dedup cache.

LRGP envelopes carry an optional 8-byte ``n`` nonce (see ``envelope.py``).
The receiver keeps a bounded, TTL'd cache of recently-seen ``(session_id,
nonce)`` pairs. If an inbound envelope's nonce is already in the cache for
that session, it is treated as a retransmit and dropped; otherwise the
nonce is recorded and the envelope is dispatched normally.

Design constraints:

* Keyed by session id so cross-session reuse of a nonce value (negligible
  but free to isolate) cannot cause a false reject.
* LRU bound prevents unbounded growth inside a single long-running session.
* TTL bound makes the cache forget nonces older than any realistic round
  trip, which limits memory for short-lived sessions that never reach
  terminal state.
* Caller is responsible for ``drop_session()`` on session close; the cache
  does not inspect session state on its own.
"""

import time
from collections import OrderedDict

from .constants import DEDUP_CACHE_PER_SESSION, DEDUP_TTL_SECONDS, KEY_NONCE, KEY_SESSION


class ReplayDedup:
    """Bounded LRU of ``(session_id, nonce)`` -> last-seen timestamp."""

    def __init__(self, max_per_session=DEDUP_CACHE_PER_SESSION, ttl_seconds=DEDUP_TTL_SECONDS):
        self._max = max_per_session
        self._ttl = ttl_seconds
        self._by_session = {}  # session_id -> OrderedDict[bytes, float]

    def check(self, envelope, now=None):
        """Decide whether ``envelope`` is a replay.

        Returns ``True`` if this envelope is a duplicate of one the caller
        has already processed (caller should drop it). Returns ``False``
        otherwise; in that case the nonce has been recorded and future
        arrivals with the same nonce for the same session will be
        flagged as replays.

        Envelopes without a ``KEY_NONCE`` field — legacy pre-nonce peers —
        are always treated as fresh. The caller is expected to log such
        peers once per session.
        """
        nonce = envelope.get(KEY_NONCE)
        if nonce is None:
            return False
        session_id = envelope.get(KEY_SESSION)
        if session_id is None:
            return False

        ts = time.monotonic() if now is None else now
        entries = self._by_session.get(session_id)
        if entries is None:
            entries = OrderedDict()
            self._by_session[session_id] = entries
        else:
            self._prune_expired(entries, ts)

        if nonce in entries:
            # Refresh recency so an active duplicate burst doesn't evict
            # the canonical entry mid-stream.
            entries.move_to_end(nonce)
            return True

        entries[nonce] = ts
        entries.move_to_end(nonce)
        while len(entries) > self._max:
            entries.popitem(last=False)
        return False

    def drop_session(self, session_id):
        """Forget all nonces for ``session_id``. Called on session close."""
        self._by_session.pop(session_id, None)

    def _prune_expired(self, entries, now):
        cutoff = now - self._ttl
        # OrderedDict is insertion-ordered (== last-seen-ordered here), so
        # the oldest is always at the front.
        while entries:
            _nonce, seen_at = next(iter(entries.items()))
            if seen_at >= cutoff:
                break
            entries.popitem(last=False)
