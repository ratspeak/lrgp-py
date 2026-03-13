"""LRGP protocol constants."""

# LXMF field IDs
FIELD_CUSTOM_TYPE = 0xFB  # 251
FIELD_CUSTOM_META = 0xFD  # 253
FIELD_FILE_ATTACHMENTS = 0x05

# Protocol marker
PROTOCOL_TYPE = "lrgp.v1"
LEGACY_TYPES = ("rlap.v1", "ratspeak.game")

# Size limits (bytes)
ENVELOPE_MAX_PACKED = 200
OPPORTUNISTIC_MAX_CONTENT = 295
LINK_PACKET_MAX_CONTENT = 319
LXMF_OVERHEAD = 112  # 16B dest + 16B src + 64B sig + 8B ts + 8B structure

# Session statuses
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_EXPIRED = "expired"
STATUS_DECLINED = "declined"

ALL_STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_COMPLETED,
                STATUS_EXPIRED, STATUS_DECLINED)

# Game session types
SESSION_TURN_BASED = "turn_based"
SESSION_REAL_TIME = "real_time"
SESSION_ROUND_BASED = "round_based"
SESSION_SINGLE_ROUND = "single_round"

# Validation models
VALIDATION_SENDER = "sender"
VALIDATION_RECEIVER = "receiver"
VALIDATION_BOTH = "both"

# Standard commands
CMD_CHALLENGE = "challenge"
CMD_ACCEPT = "accept"
CMD_DECLINE = "decline"
CMD_MOVE = "move"
CMD_RESIGN = "resign"
CMD_DRAW_OFFER = "draw_offer"
CMD_DRAW_ACCEPT = "draw_accept"
CMD_DRAW_DECLINE = "draw_decline"
CMD_ERROR = "error"

# Standard error codes
ERR_UNSUPPORTED_APP = "unsupported_app"
ERR_INVALID_MOVE = "invalid_move"
ERR_NOT_YOUR_TURN = "not_your_turn"
ERR_SESSION_EXPIRED = "session_expired"
ERR_PROTOCOL_ERROR = "protocol_error"

# Session TTL defaults (seconds)
TTL_PENDING = 86400      # 24 hours
TTL_ACTIVE = 604800      # 7 days
TTL_GRACE_PERIOD = 3600  # 1 hour clock-skew tolerance

# Envelope keys
KEY_APP = "a"
KEY_COMMAND = "c"
KEY_SESSION = "s"
KEY_PAYLOAD = "p"

# Error payload keys
KEY_ERR_CODE = "code"
KEY_ERR_MSG = "msg"
KEY_ERR_REF = "ref"
