"""LRGP transport bridge for LXMF (optional, requires lrgp[rns])."""

from .envelope import pack_lxmf_fields, unpack_envelope


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
        if delivery not in ("opportunistic", "direct"):
            raise ValueError("delivery must be 'opportunistic' or 'direct'")

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
            envelope = unpack_envelope(fields)
            if envelope is not None:
                # ``source_hash`` is presentation data until LXMF has
                # validated the message signature.  LRGP binds sessions to
                # this identifier, so never hand an unverified value to the
                # caller as an authenticated participant.
                if getattr(lxm, "signature_validated", False) is not True:
                    return
                source_hash = getattr(lxm, "source_hash", None)
                if not isinstance(source_hash, (bytes, bytearray)) or not source_hash:
                    return
                sender = bytes(source_hash).hex()
                self._handler(envelope, sender, lxm)

        self._router.register_delivery_callback(_on_message)
