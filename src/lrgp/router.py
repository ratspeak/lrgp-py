"""LRGP game registry, discovery, and dispatch."""

import pkgutil
import importlib
import threading

from .app_base import GameBase
from .envelope import pack_envelope, parse_app_version
from .errors import UnknownApp

_registry = {}
_registry_lock = threading.Lock()


def register(app):
    """Register a GameBase instance."""
    with _registry_lock:
        _registry[app.app_id] = app


def unregister(app_id):
    """Remove a game from the registry."""
    with _registry_lock:
        _registry.pop(app_id, None)


def get_app(app_id):
    """Get a registered game instance, or None."""
    with _registry_lock:
        return _registry.get(app_id)


def list_apps():
    """Return manifests for all registered games."""
    with _registry_lock:
        return [app.get_manifest() for app in _registry.values()]


def discover(package):
    """Scan a package for GameBase subclasses and register them.

    Args:
        package: a Python package (must have __path__ and __name__).
    """
    with _registry_lock:
        for importer, name, ispkg in pkgutil.iter_modules(package.__path__):
            try:
                mod = importlib.import_module("{}.{}".format(package.__name__, name))
                for attr_name in dir(mod):
                    cls = getattr(mod, attr_name)
                    if (isinstance(cls, type)
                            and issubclass(cls, GameBase)
                            and cls is not GameBase
                            and cls.app_id):
                        instance = cls()
                        _registry[instance.app_id] = instance
            except Exception:
                pass


def dispatch_incoming(envelope, sender_hash, identity_id=""):
    """Route an incoming LRGP envelope to the correct game handler.

    Returns:
        dict: result from the game's handle_incoming, or None.

    Raises:
        UnknownApp: if no handler is registered for the game.
    """
    app_ver = envelope.get("a", "")
    app_id, _version = parse_app_version(app_ver)
    command = envelope.get("c", "")
    session_id = envelope.get("s", "")
    payload = envelope.get("p", {})

    with _registry_lock:
        handler = _registry.get(app_id)

    if not handler:
        raise UnknownApp("No handler for game '{}'".format(app_id))

    return handler.handle_incoming(session_id, command, payload,
                                   sender_hash, identity_id)


def dispatch_outgoing(app_id, command, payload, session_id,
                      identity_id=""):
    """Prepare an outgoing LRGP game action.

    Returns:
        tuple: (envelope_dict, fallback_text, delivery_method)

    Raises:
        UnknownApp: if no handler is registered for the game.
    """
    with _registry_lock:
        handler = _registry.get(app_id)

    if not handler:
        raise UnknownApp("Unknown game: {}".format(app_id))

    enriched, fallback = handler.handle_outgoing(session_id, command,
                                                  payload, identity_id)
    delivery = handler.get_delivery_method(command)

    envelope = pack_envelope(handler.app_id, handler.version,
                             command, session_id, enriched)

    return envelope, fallback, delivery
