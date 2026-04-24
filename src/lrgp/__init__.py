"""LRGP — Lightweight Reticulum Gaming Protocol."""

__version__ = "0.2.0"

from .constants import PROTOCOL_TYPE, FIELD_CUSTOM_TYPE, FIELD_CUSTOM_META
from .errors import LrgpError
from .envelope import pack_envelope, unpack_envelope, validate_envelope_size, generate_nonce
from .session import Session, SessionStateMachine
from .app_base import GameBase
from .router import register, discover, dispatch_incoming, dispatch_outgoing, list_apps
from .dedup import ReplayDedup
