"""Msgpack import shim. Tries RNS vendored copy first, then our vendored copy."""

try:
    from RNS.vendor.umsgpack import pack, packb, unpack, unpackb
except ImportError:
    try:
        from lrgp._vendor.umsgpack import pack, packb, unpack, unpackb
    except ImportError:
        from msgpack import pack, packb, unpack, unpackb

__all__ = ["pack", "packb", "unpack", "unpackb"]
