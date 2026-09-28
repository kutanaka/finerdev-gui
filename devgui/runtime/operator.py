"""The single, server-wide operator (exclusive control) right.

Per docs/design.md section 9.1-9.3 (section 9.4's forced-takeover state
machine is a later step, built on top of this module). There is exactly
one operator right for the whole server, not one per device. Holding it
is proven with a random token (`secrets.token_urlsafe`); the server is
always the source of truth for who holds it - the client-side UI is only
a convenience.

Time-based expiry (idle timeout, disconnect grace) uses an injectable
clock so tests can advance time deterministically instead of sleeping.
This module knows nothing about asyncio or WebSockets: `OperatorManager`
is plain synchronous state, and the caller (server.py) is responsible for
polling `check_expiration()` periodically and broadcasting the result.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Callable


class OperatorError(Exception):
    """Base for operator-right errors."""


class OperatorHeldError(OperatorError):
    """acquire() was called while someone else already holds the right."""


class InvalidOperatorTokenError(OperatorError):
    """release() (or similar) was called with a token that isn't the current holder's."""


class OperatorManager:
    def __init__(
        self,
        *,
        idle_timeout: float = 600.0,
        disconnect_grace: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_timeout = idle_timeout
        self._disconnect_grace = disconnect_grace
        self._clock = clock
        self._lock = threading.Lock()

        self.token: str | None = None
        self.client_id: str | None = None
        self.display_name: str | None = None
        self._last_activity: float | None = None
        self._disconnected_at: float | None = None

    @property
    def is_held(self) -> bool:
        with self._lock:
            return self.token is not None

    def acquire(
        self, client_id: str, display_name: str, *, presented_token: str | None = None
    ) -> str:
        """Take the operator right if free, or reclaim it after a reconnect.

        If the right is currently held but disconnected (within the grace
        period) and `presented_token` matches the current holder's token,
        this re-associates the (new, post-reconnect) client_id with the
        same token instead of failing - the browser proves continuity by
        presenting the token it kept in sessionStorage.
        """
        with self._lock:
            if self.token is not None:
                if presented_token is not None and secrets.compare_digest(
                    self.token, presented_token
                ):
                    self.client_id = client_id
                    self.display_name = display_name
                    self._disconnected_at = None
                    self._last_activity = self._clock()
                    return self.token
                raise OperatorHeldError("operator right is already held")

            token = secrets.token_urlsafe(32)
            self.token = token
            self.client_id = client_id
            self.display_name = display_name
            self._last_activity = self._clock()
            self._disconnected_at = None
            return token

    def release(self, token: str) -> None:
        with self._lock:
            self._require_holder_locked(token)
            self._clear_locked()

    def is_holder(self, token: str | None) -> bool:
        if token is None:
            return False
        with self._lock:
            return self.token is not None and secrets.compare_digest(self.token, token)

    def touch(self, token: str) -> None:
        """Reset the idle timer; call this after any successful operator action."""
        with self._lock:
            if self.token is not None and secrets.compare_digest(self.token, token):
                self._last_activity = self._clock()

    def mark_disconnected(self, client_id: str) -> None:
        """Call when a WebSocket with this client_id disconnects."""
        with self._lock:
            if self.client_id == client_id:
                self._disconnected_at = self._clock()

    def check_expiration(self) -> bool:
        """Auto-release on idle timeout or expired disconnect grace.

        Returns True if a release just happened (so the caller knows to
        broadcast operator_state).
        """
        with self._lock:
            if self.token is None:
                return False
            now = self._clock()
            if (
                self._disconnected_at is not None
                and now - self._disconnected_at > self._disconnect_grace
            ):
                self._clear_locked()
                return True
            if self._last_activity is not None and now - self._last_activity > self._idle_timeout:
                self._clear_locked()
                return True
            return False

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"holder_display_name": self.display_name, "is_held": self.token is not None}

    def _require_holder_locked(self, token: str) -> None:
        if self.token is None or not secrets.compare_digest(self.token, token):
            raise InvalidOperatorTokenError("invalid or missing operator token")

    def _clear_locked(self) -> None:
        self.token = None
        self.client_id = None
        self.display_name = None
        self._last_activity = None
        self._disconnected_at = None
