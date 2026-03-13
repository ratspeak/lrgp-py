"""Msgpack import shim. Tries RNS vendored copy first, then our vendored copy."""

try:
    from RNS.vendor.umsgpack import packb, unpackb
except ImportError:
    try:
        from lrgp._vendor.umsgpack import packb, unpackb
    except ImportError:
        from msgpack import packb, unpackb

__all__ = ["packb", "unpackb"]
