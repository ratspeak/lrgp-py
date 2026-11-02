"""LRGP transport bridge for LXMF (optional, requires lrgp[rns])."""

from .envelope import pack_lxmf_fields, unpack_envelope


class LrgpTransport:
    """Bridge an existing LXMRouter to LRGP envelopes.

    Import this module explicitly; installing the rns extra supplies optional
    transport dependencies but does not construct or start a transport. The
    package's MessagePack shim can also import RNS's vendored serializer.
    """

    def __init__(self, lxmf_router, identity):
        """Keep an existing router and local source provider.

        Args:
            lxmf_router: an LXMF.LXMRouter instance.
            identity: an object exposing the registered local LXMF delivery
                destination as .destination. A plain RNS.Identity does not supply
                this attribute; expose the destination returned by
                register_delivery_identity, as in examples/ttt_cli.py.
        """
        self._router = lxmf_router
        self._identity = identity
        self._handler = None

    def send(self, dest_hash_hex, envelope, fallback_text,
             delivery="opportunistic", title=""):
        """Submit an LRGP envelope to the existing LXMF router and return its LXMessage.

        Args:
            dest_hash_hex: remote LXMF delivery destination hash in hexadecimal,
                not the remote identity hash.
            envelope: LRGP envelope dict.
            fallback_text: human-readable content for non-LRGP clients.
            delivery: "opportunistic" or "direct".
            title: optional LXMF title.

        If the remote identity is not recalled, request its path and raise
        RuntimeError immediately; this helper does not wait or retry. Successful
        return means handle_outbound was called, not that delivery was proved.
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
        """Set the LXMF delivery callback for incoming LRGP messages.

        The callback receives (envelope, sender_hash, lxm), with a validated LRGP
        envelope and hexadecimal source hash. Messages require LXMF's
        signature_validated flag to be exactly True and a nonempty binary source.
        Malformed LRGP fields and callback exceptions can propagate. This replaces
        the router's registered delivery callback; it does not dispatch to a game
        or provide the receiving local identity scope automatically.
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
