"""LRGP transport bridge for LXMF (optional, requires lrgp[rns])."""

from .constants import FIELD_CUSTOM_TYPE, FIELD_CUSTOM_META, PROTOCOL_TYPE, LEGACY_TYPES
from .envelope import pack_lxmf_fields


class LrgpTransport:
    """Wraps an LXMRouter to send/receive LRGP game messages over LXMF.

    This module is the ONLY part of LRGP that imports RNS/LXMF.
    It is not imported by default — only when using ``lrgp[rns]``.
    """

    def __init__(self, lxmf_router, identity):
        """
        Args:
            lxmf_router: an LXMF.LXMRouter instance.
            identity: an RNS.Identity instance.
        """
        self._router = lxmf_router
        self._identity = identity
        self._handler = None

    def send(self, dest_hash_hex, envelope, fallback_text,
             delivery="opportunistic", title=""):
        """Send an LRGP envelope as an LXMF message.

        Args:
            dest_hash_hex: destination identity hash as hex string.
            envelope: LRGP envelope dict.
            fallback_text: human-readable content for non-LRGP clients.
            delivery: "opportunistic" or "direct".
            title: optional LXMF title.
        """
        import RNS
        import LXMF

        dest_hash = bytes.fromhex(dest_hash_hex)
        dest_identity = RNS.Identity.recall(dest_hash)
        if dest_identity is None:
            RNS.Transport.request_path(dest_hash)
            raise RuntimeError("Identity not known, path requested")

        dest = RNS.Destination(
            dest_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
            "lxmf", "delivery"
        )

        lxm = LXMF.LXMessage(
            dest, self._identity.destination, fallback_text,
            title=title, desired_method=(
                LXMF.LXMessage.OPPORTUNISTIC if delivery == "opportunistic"
                else LXMF.LXMessage.DIRECT
            ),
        )

        fields = pack_lxmf_fields(envelope)
        lxm.fields = fields

        self._router.handle_outbound(lxm)
        return lxm

    def register_handler(self, callback):
        """Register a callback for incoming LRGP messages.

        The callback signature: callback(envelope, sender_hash, lxm)
        where envelope is the unpacked LRGP envelope dict.
        """
        self._handler = callback

        def _on_message(lxm):
            fields = lxm.fields if hasattr(lxm, "fields") else {}
            custom_type = fields.get(FIELD_CUSTOM_TYPE, "")
            if custom_type == PROTOCOL_TYPE or custom_type in LEGACY_TYPES:
                envelope = fields.get(FIELD_CUSTOM_META, {})
                sender = lxm.source_hash.hex() if hasattr(lxm, "source_hash") else ""
                self._handler(envelope, sender, lxm)

        self._router.register_delivery_callback(_on_message)
