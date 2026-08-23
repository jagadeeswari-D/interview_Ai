"""Server-side sessions backed by SQLite.

The browser cookie contains only a random, opaque token. The actual session
payload is stored in the `sessions` table. This gives us real server-side
session control (revocation on logout, idle timeout, size limits) rather than
the default client-side signed cookie.

Design notes
------------
* Idle timeout: a session expires if it has not been seen for
  SESSION_IDLE_TIMEOUT_MINUTES. Access refreshes `last_seen`, giving a
  rolling timeout.
* Anonymous, never-modified sessions are never written, so random visitors
  do not bloat the database.
* On logout (`session.clear()`), the row is deleted and the cookie revoked.
"""

import json
import secrets
from datetime import datetime, timedelta

from flask.sessions import SessionInterface, SessionMixin

from .db import get_db


class ServerSideSession(dict, SessionMixin):
    """A session dict that also carries its storage token."""

    def __init__(self, initial=None, token=None, new=False):
        super().__init__(initial or {})
        self.token = token
        self.new = new


class SQLiteSessionInterface(SessionInterface):
    """SessionInterface that persists session data to SQLite."""

    session_class = ServerSideSession
    cookie_httponly = True
    cookie_samesite = "Lax"

    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def open_session(self, app, request):
        name = app.config["SESSION_COOKIE_NAME"]
        idle = timedelta(minutes=app.config["SESSION_IDLE_TIMEOUT_MINUTES"])
        token = request.cookies.get(name)

        if token:
            db = get_db()
            row = db.execute(
                "SELECT data, last_seen FROM sessions WHERE token = ?", (token,)
            ).fetchone()
            if row:
                last_seen = datetime.fromisoformat(row["last_seen"])
                if datetime.now() - last_seen <= idle:
                    db.execute(
                        "UPDATE sessions SET last_seen = ? WHERE token = ?",
                        (self._now(), token),
                    )
                    db.commit()
                    return ServerSideSession(
                        initial=json.loads(row["data"]), token=token
                    )
                db.execute("DELETE FROM sessions WHERE token = ?", (token,))
                db.commit()

        return ServerSideSession(token=secrets.token_urlsafe(32), new=True)

    def save_session(self, app, session, response):
        name = app.config["SESSION_COOKIE_NAME"]
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        secure = app.config["SESSION_COOKIE_SECURE"]
        idle = timedelta(minutes=app.config["SESSION_IDLE_TIMEOUT_MINUTES"])
        expires = datetime.now() + idle
        db = get_db()

        if session.new and not session:
            # Anonymous visitor with nothing in the session: no row, no cookie.
            return

        if not session:
            # Logged out / cleared session: revoke on the server and client.
            db.execute("DELETE FROM sessions WHERE token = ?", (session.token,))
            db.commit()
            response.delete_cookie(name, domain=domain, path=path)
            return

        payload = json.dumps(dict(session))
        if session.new:
            db.execute(
                "INSERT INTO sessions (token, data, created_at, last_seen) "
                "VALUES (?, ?, ?, ?)",
                (session.token, payload, self._now(), self._now()),
            )
        else:
            db.execute(
                "UPDATE sessions SET data = ?, last_seen = ? WHERE token = ?",
                (payload, self._now(), session.token),
            )
        db.commit()

        response.set_cookie(
            name,
            session.token,
            expires=expires,
            domain=domain,
            path=path,
            httponly=self.cookie_httponly,
            samesite=self.cookie_samesite,
            secure=secure,
        )
