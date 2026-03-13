"""LRGP SQLite persistence for game sessions and actions."""

import json
import sqlite3
import time
import threading


_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS game_sessions (
    session_id    TEXT NOT NULL,
    identity_id   TEXT NOT NULL DEFAULT '',
    app_id        TEXT NOT NULL,
    app_version   INTEGER NOT NULL DEFAULT 1,
    contact_hash  TEXT NOT NULL,
    initiator     TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    metadata      TEXT NOT NULL DEFAULT '{}',
    unread        INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL DEFAULT 0,
    last_action_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, identity_id)
);

CREATE INDEX IF NOT EXISTS idx_game_sessions_contact
    ON game_sessions(contact_hash, identity_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_status
    ON game_sessions(status);

CREATE TABLE IF NOT EXISTS game_actions (
    session_id    TEXT NOT NULL,
    identity_id   TEXT NOT NULL DEFAULT '',
    action_num    INTEGER NOT NULL,
    command       TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    sender        TEXT NOT NULL,
    timestamp     REAL NOT NULL DEFAULT 0,
    UNIQUE (session_id, identity_id, action_num)
);
"""


class LrgpStore:
    """SQLite-backed storage for LRGP game sessions and actions."""

    def __init__(self, db_path=":memory:"):
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(_CREATE_TABLES)
        conn.commit()

    # --- Sessions ---

    def save_session(self, session):
        """Insert or replace a session. Accepts a Session object or dict."""
        if hasattr(session, "to_dict"):
            d = session.to_dict()
        else:
            d = dict(session)

        meta = d.get("metadata", {})
        if isinstance(meta, dict):
            meta = json.dumps(meta)

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO game_sessions
               (session_id, identity_id, app_id, app_version, contact_hash,
                initiator, status, metadata, unread, created_at, updated_at,
                last_action_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (d["session_id"], d.get("identity_id", ""),
             d["app_id"], d.get("app_version", 1),
             d["contact_hash"], d.get("initiator", ""),
             d.get("status", "pending"), meta,
             d.get("unread", 0),
             d.get("created_at", time.time()),
             d.get("updated_at", time.time()),
             d.get("last_action_at", time.time())),
        )
        conn.commit()

    def get_session(self, session_id, identity_id=""):
        """Get a session by ID. Returns dict or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM game_sessions WHERE session_id=? AND identity_id=?",
            (session_id, identity_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def update_session(self, session_id, identity_id="", **kwargs):
        """Update specific fields of a session."""
        if not kwargs:
            return
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            kwargs["metadata"] = json.dumps(kwargs["metadata"])
        kwargs["updated_at"] = time.time()

        sets = ", ".join("{}=?".format(k) for k in kwargs)
        vals = list(kwargs.values()) + [session_id, identity_id]

        conn = self._get_conn()
        conn.execute(
            "UPDATE game_sessions SET {} WHERE session_id=? AND identity_id=?".format(sets),
            vals,
        )
        conn.commit()

    def list_sessions(self, identity_id="", app_id=None, status=None,
                      contact_hash=None):
        """List sessions with optional filters."""
        clauses = ["identity_id=?"]
        params = [identity_id]

        if app_id:
            clauses.append("app_id=?")
            params.append(app_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        if contact_hash:
            clauses.append("contact_hash=?")
            params.append(contact_hash)

        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM game_sessions WHERE {} ORDER BY last_action_at DESC".format(
                " AND ".join(clauses)),
            params,
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_session(self, session_id, identity_id=""):
        """Delete a session and its actions."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM game_sessions WHERE session_id=? AND identity_id=?",
            (session_id, identity_id),
        )
        conn.execute(
            "DELETE FROM game_actions WHERE session_id=? AND identity_id=?",
            (session_id, identity_id),
        )
        conn.commit()

    # --- Actions ---

    def save_action(self, session_id, identity_id, action_num, command,
                    payload, sender, timestamp=None):
        """Record a game action in the history."""
        if timestamp is None:
            timestamp = time.time()
        payload_json = json.dumps(payload) if isinstance(payload, dict) else payload

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO game_actions
               (session_id, identity_id, action_num, command, payload_json,
                sender, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, identity_id, action_num, command, payload_json,
             sender, timestamp),
        )
        conn.commit()

    def get_actions(self, session_id, identity_id=""):
        """Get all actions for a session, ordered by action_num."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM game_actions
               WHERE session_id=? AND identity_id=?
               ORDER BY action_num""",
            (session_id, identity_id),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json", "{}"))
            result.append(d)
        return result

    def get_action_count(self, session_id, identity_id=""):
        """Get the number of actions in a session."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM game_actions WHERE session_id=? AND identity_id=?",
            (session_id, identity_id),
        ).fetchone()
        return row[0]

    # --- Helpers ---

    @staticmethod
    def _row_to_dict(row):
        d = dict(row)
        meta = d.get("metadata", "{}")
        if isinstance(meta, str):
            d["metadata"] = json.loads(meta)
        return d
