"""The single, server-wide operator (exclusive control) right.

Per docs/design.md sections 9.1-9.4, including the forced-takeover state
machine (9.4). There is exactly one operator right for the whole server,
not one per device. Holding it is proven with a random token
(`secrets.token_urlsafe`); the server is always the source of truth for
who holds it - the client-side UI is only a convenience.

Time-based expiry (idle timeout, disconnect grace, takeover wait,
takeover cooldown) uses an injectable clock so tests can advance time
deterministically instead of sleeping. This module knows nothing about
asyncio or WebSockets: `OperatorManager` is plain synchronous state, and
the caller (server.py) is responsible for polling `check_expiration()`
and `check_pending_expiration()` periodically and acting on the results
(broadcasts, targeted WebSocket notifications).
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable


class OperatorError(Exception):
    """Base for operator-right errors."""


class OperatorHeldError(OperatorError):
    """acquire() was called while someone else already holds the right."""


class InvalidOperatorTokenError(OperatorError):
    """release() (or similar) was called with a token that isn't the current holder's."""


class TakeoverInProgressError(OperatorError):
    """request_takeover() was called while another request is already pending."""


class TakeoverCooldownError(OperatorError):
    """request_takeover() was called by a requester still in a post-rejection cooldown."""


class TakeoverNotFoundError(OperatorError):
    """cancel_request()/respond_to_request() referenced a request that isn't the pending one."""


@dataclass
class PendingRequest:
    request_id: str
    requester_client_id: str
    requester_ip: str
    requester_display_name: str
    deadline: float


@dataclass
class TransferResult:
    """A takeover request resolved in the requester's favor (accepted, timed
    out with no response, or the holder released while it was pending)."""

    new_token: str
    requester_client_id: str
    requester_display_name: str
    old_holder_client_id: str | None
    old_holder_display_name: str | None


@dataclass
class RequestRejected:
    requester_client_id: str
    requester_display_name: str


class OperatorManager:
    def __init__(
        self,
        *,
        idle_timeout: float = 600.0,
        disconnect_grace: float = 30.0,
        takeover_wait: float = 10.0,
        takeover_cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_timeout = idle_timeout
        self._disconnect_grace = disconnect_grace
        self._takeover_wait = takeover_wait
        self._takeover_cooldown = takeover_cooldown
        self._clock = clock
        self._lock = threading.Lock()

        self.token: str | None = None
        self.client_id: str | None = None
        self.display_name: str | None = None
        self._last_activity: float | None = None
        self._disconnected_at: float | None = None
        self._pending_request: PendingRequest | None = None
        self._cooldowns: dict[tuple[str, str], float] = {}

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

    def release(self, token: str) -> TransferResult | None:
        """Release the right - or, if a takeover request is pending (section
        9.4 item 5), hand it straight to the requester instead of freeing it."""
        with self._lock:
            self._require_holder_locked(token)
            if self._pending_request is not None:
                return self._transfer_to_requester_locked(self._pending_request)
            self._clear_locked()
            return None

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
            return {
                "holder_display_name": self.display_name,
                "is_held": self.token is not None,
                "request_pending": self._pending_request is not None,
            }

    def request_takeover(
        self, client_id: str, ip: str, display_name: str
    ) -> PendingRequest:
        """Ask to take over the (currently held) right. Callers should check
        `is_held` first and call `acquire()` directly when it's free (section
        9.4 item 1) - this method only handles the "someone already holds
        it" negotiation."""
        with self._lock:
            if self._pending_request is not None:
                raise TakeoverInProgressError("another request is already being processed")

            key = (client_id, ip)
            now = self._clock()
            cooldown_until = self._cooldowns.get(key)
            if cooldown_until is not None and now < cooldown_until:
                raise TakeoverCooldownError("requester is in a post-rejection cooldown")

            request = PendingRequest(
                request_id=secrets.token_urlsafe(8),
                requester_client_id=client_id,
                requester_ip=ip,
                requester_display_name=display_name,
                deadline=now + self._takeover_wait,
            )
            self._pending_request = request
            return request

    def cancel_request(self, request_id: str, requester_client_id: str) -> None:
        """The requester withdraws their own pending request."""
        with self._lock:
            request = self._pending_request
            if (
                request is None
                or request.request_id != request_id
                or request.requester_client_id != requester_client_id
            ):
                raise TakeoverNotFoundError("no matching pending request to cancel")
            self._pending_request = None

    def cancel_if_requester(self, client_id: str) -> bool:
        """Withdraw the pending request if `client_id` is its requester (their
        WebSocket disconnected). Returns True if a request was cancelled."""
        with self._lock:
            if self._pending_request is not None and self._pending_request.requester_client_id == client_id:
                self._pending_request = None
                return True
            return False

    def respond_to_request(
        self, request_id: str, holder_token: str, accept: bool
    ) -> TransferResult | RequestRejected:
        """Only the current holder may accept/reject a pending request."""
        with self._lock:
            self._require_holder_locked(holder_token)
            request = self._pending_request
            if request is None or request.request_id != request_id:
                raise TakeoverNotFoundError("no matching pending request")

            if accept:
                return self._transfer_to_requester_locked(request)

            self._pending_request = None
            self._cooldowns[(request.requester_client_id, request.requester_ip)] = (
                self._clock() + self._takeover_cooldown
            )
            return RequestRejected(
                requester_client_id=request.requester_client_id,
                requester_display_name=request.requester_display_name,
            )

    def check_pending_expiration(self) -> TransferResult | None:
        """If the pending request's wait time has elapsed with no response
        (holder connected but silent, or disconnected), transfer to the
        requester - "no response" includes the holder being disconnected
        (section 9.4 item 3)."""
        with self._lock:
            request = self._pending_request
            if request is None or self._clock() < request.deadline:
                return None
            return self._transfer_to_requester_locked(request)

    def _transfer_to_requester_locked(self, request: PendingRequest) -> TransferResult:
        old_client_id = self.client_id
        old_display_name = self.display_name
        new_token = secrets.token_urlsafe(32)
        self.token = new_token
        self.client_id = request.requester_client_id
        self.display_name = request.requester_display_name
        self._last_activity = self._clock()
        self._disconnected_at = None
        self._pending_request = None
        return TransferResult(
            new_token=new_token,
            requester_client_id=request.requester_client_id,
            requester_display_name=request.requester_display_name,
            old_holder_client_id=old_client_id,
            old_holder_display_name=old_display_name,
        )

    def _require_holder_locked(self, token: str) -> None:
        if self.token is None or not secrets.compare_digest(self.token, token):
            raise InvalidOperatorTokenError("invalid or missing operator token")

    def _clear_locked(self) -> None:
        self.token = None
        self.client_id = None
        self.display_name = None
        self._last_activity = None
        self._disconnected_at = None
