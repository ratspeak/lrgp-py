"""LRGP — Lightweight Reticulum Gaming Protocol."""

__version__ = "0.4.0"

from .constants import PROTOCOL_TYPE, FIELD_CUSTOM_TYPE, FIELD_CUSTOM_META
from .errors import (
    AdmissionLimit, AuthenticatedSenderRequired, LrgpError,
    OutgoingIdentityRequired, ReceivingIdentityRequired, SessionExists,
)
from .envelope import (
    generate_nonce, generate_session_id, pack_envelope, pack_to_bytes,
    unpack_envelope, unpack_from_bytes, validate_envelope,
    validate_envelope_size,
)
from .session import Session, SessionStateMachine
from .app_base import GameBase
from .router import (
    IncomingDispatch, LrgpRouter, PreparedOutgoing, RemoteProtocolError,
    dispatch_incoming, dispatch_outgoing, dispatch_outgoing_to, discover,
    forget_incoming_nonce, hydrate_session, list_apps, list_sessions, register, remove_session,
    restore_session, rollback_incoming, rollback_outgoing, snapshot_session,
    snapshot_before_outgoing,
)
from .dedup import ReplayDedup
