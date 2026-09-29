import pytest

from devgui.runtime.operator import (
    InvalidOperatorTokenError,
    OperatorHeldError,
    OperatorManager,
    RequestRejected,
    TakeoverCooldownError,
    TakeoverInProgressError,
    TakeoverNotFoundError,
    TransferResult,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def manager(clock):
    return OperatorManager(idle_timeout=600.0, disconnect_grace=30.0, clock=clock)


def test_acquire_when_free_returns_token(manager):
    token = manager.acquire("c1", "Alice (1.2.3.4)")
    assert token
    assert manager.is_held is True
    assert manager.is_holder(token) is True
    assert manager.snapshot() == {
        "holder_display_name": "Alice (1.2.3.4)",
        "is_held": True,
        "request_pending": False,
        "external": False,
        "blocked": False,
    }


def test_acquire_when_held_raises(manager):
    manager.acquire("c1", "Alice")
    with pytest.raises(OperatorHeldError):
        manager.acquire("c2", "Bob")


def test_release_clears_holder(manager):
    token = manager.acquire("c1", "Alice")
    manager.release(token)
    assert manager.is_held is False
    assert manager.snapshot() == {
        "holder_display_name": None,
        "is_held": False,
        "request_pending": False,
        "external": False,
        "blocked": False,
    }


def test_release_with_wrong_token_raises(manager):
    manager.acquire("c1", "Alice")
    with pytest.raises(InvalidOperatorTokenError):
        manager.release("not-the-token")


def test_release_when_free_raises(manager):
    with pytest.raises(InvalidOperatorTokenError):
        manager.release("anything")


def test_is_holder_false_for_none_or_wrong_token(manager):
    token = manager.acquire("c1", "Alice")
    assert manager.is_holder(None) is False
    assert manager.is_holder("wrong") is False
    assert manager.is_holder(token) is True


def test_after_release_someone_else_can_acquire(manager):
    token = manager.acquire("c1", "Alice")
    manager.release(token)
    token2 = manager.acquire("c2", "Bob")
    assert token2 != token
    assert manager.snapshot()["holder_display_name"] == "Bob"


def test_idle_timeout_auto_releases(manager, clock):
    manager.acquire("c1", "Alice")
    assert manager.check_expiration() is False

    clock.advance(599)
    assert manager.check_expiration() is False
    assert manager.is_held is True

    clock.advance(2)  # total 601 > 600 idle_timeout
    assert manager.check_expiration() is True
    assert manager.is_held is False


def test_touch_resets_idle_timer(manager, clock):
    token = manager.acquire("c1", "Alice")
    clock.advance(500)
    manager.touch(token)
    clock.advance(500)  # 500 since touch, still < 600
    assert manager.check_expiration() is False
    assert manager.is_held is True


def test_touch_with_wrong_token_is_a_no_op(manager, clock):
    manager.acquire("c1", "Alice")
    clock.advance(500)
    manager.touch("wrong-token")
    clock.advance(200)  # 700 since real acquire; > 600 idle timeout
    assert manager.check_expiration() is True


def test_disconnect_then_reconnect_within_grace_keeps_holder(manager, clock):
    token = manager.acquire("c1", "Alice")
    manager.mark_disconnected("c1")

    clock.advance(29)
    assert manager.check_expiration() is False
    assert manager.is_held is True

    # Reconnects with a new client_id but presents the old token.
    reclaimed = manager.acquire("c2", "Alice", presented_token=token)
    assert reclaimed == token
    assert manager.snapshot()["holder_display_name"] == "Alice"

    clock.advance(60)  # long past the old grace window
    assert manager.check_expiration() is False  # disconnected_at was cleared by the reclaim
    assert manager.is_held is True


def test_disconnect_without_reconnect_expires_after_grace(manager, clock):
    manager.acquire("c1", "Alice")
    manager.mark_disconnected("c1")

    clock.advance(31)
    assert manager.check_expiration() is True
    assert manager.is_held is False


def test_mark_disconnected_ignores_non_holder_client(clock):
    manager = OperatorManager(idle_timeout=10_000.0, disconnect_grace=30.0, clock=clock)
    manager.acquire("c1", "Alice")
    manager.mark_disconnected("someone-else")
    clock.advance(60)
    assert manager.check_expiration() is False
    assert manager.is_held is True


def test_reclaim_with_wrong_token_while_held_raises(manager):
    manager.acquire("c1", "Alice")
    with pytest.raises(OperatorHeldError):
        manager.acquire("c2", "Bob", presented_token="not-the-real-token")


# --- forced takeover (section 9.4) ---


def test_request_takeover_creates_pending_request(manager):
    manager.acquire("c1", "Alice")
    request = manager.request_takeover("c2", "9.9.9.9", "Bob")
    assert request.request_id
    assert request.requester_client_id == "c2"
    assert manager.snapshot()["request_pending"] is True


def test_request_takeover_while_one_pending_raises(manager):
    manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "9.9.9.9", "Bob")
    with pytest.raises(TakeoverInProgressError):
        manager.request_takeover("c3", "8.8.8.8", "Carol")


def test_accept_transfers_the_right(manager, clock):
    holder_token = manager.acquire("c1", "Alice")
    request = manager.request_takeover("c2", "9.9.9.9", "Bob")

    result = manager.respond_to_request(request.request_id, holder_token, accept=True)

    assert isinstance(result, TransferResult)
    assert result.new_token != holder_token
    assert result.requester_client_id == "c2"
    assert result.old_holder_client_id == "c1"
    assert result.old_holder_display_name == "Alice"
    assert manager.is_holder(result.new_token) is True
    assert manager.is_holder(holder_token) is False
    assert manager.snapshot()["holder_display_name"] == "Bob"
    assert manager.snapshot()["request_pending"] is False


def test_reject_notifies_and_sets_cooldown(manager, clock):
    holder_token = manager.acquire("c1", "Alice")
    request = manager.request_takeover("c2", "9.9.9.9", "Bob")

    result = manager.respond_to_request(request.request_id, holder_token, accept=False)

    assert isinstance(result, RequestRejected)
    assert result.requester_client_id == "c2"
    assert manager.snapshot()["request_pending"] is False
    assert manager.is_holder(holder_token) is True  # rejection keeps the same holder

    with pytest.raises(TakeoverCooldownError):
        manager.request_takeover("c2", "9.9.9.9", "Bob")

    clock.advance(31)  # past the 30s default cooldown
    manager.request_takeover("c2", "9.9.9.9", "Bob")  # no longer raises


def test_respond_by_non_holder_raises(manager):
    manager.acquire("c1", "Alice")
    request = manager.request_takeover("c2", "9.9.9.9", "Bob")
    with pytest.raises(InvalidOperatorTokenError):
        manager.respond_to_request(request.request_id, "not-the-holder-token", accept=True)


def test_respond_with_wrong_request_id_raises(manager):
    holder_token = manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "9.9.9.9", "Bob")
    with pytest.raises(TakeoverNotFoundError):
        manager.respond_to_request("not-the-request-id", holder_token, accept=True)


def test_cancel_request_by_requester(manager):
    manager.acquire("c1", "Alice")
    request = manager.request_takeover("c2", "9.9.9.9", "Bob")
    manager.cancel_request(request.request_id, "c2")
    assert manager.snapshot()["request_pending"] is False


def test_cancel_request_by_non_requester_raises(manager):
    manager.acquire("c1", "Alice")
    request = manager.request_takeover("c2", "9.9.9.9", "Bob")
    with pytest.raises(TakeoverNotFoundError):
        manager.cancel_request(request.request_id, "not-the-requester")
    assert manager.snapshot()["request_pending"] is True


def test_cancel_if_requester_disconnects(manager):
    manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "9.9.9.9", "Bob")
    assert manager.cancel_if_requester("c2") is True
    assert manager.snapshot()["request_pending"] is False


def test_cancel_if_requester_ignores_unrelated_client(manager):
    manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "9.9.9.9", "Bob")
    assert manager.cancel_if_requester("someone-else") is False
    assert manager.snapshot()["request_pending"] is True


def test_check_pending_expiration_before_deadline_is_none(manager, clock):
    manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "9.9.9.9", "Bob")
    clock.advance(9)  # < default 10s takeover_wait
    assert manager.check_pending_expiration() is None


def test_check_pending_expiration_after_deadline_transfers(manager, clock):
    holder_token = manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "9.9.9.9", "Bob")
    clock.advance(11)  # > default 10s takeover_wait

    result = manager.check_pending_expiration()

    assert isinstance(result, TransferResult)
    assert result.requester_client_id == "c2"
    assert manager.is_holder(holder_token) is False
    assert manager.is_holder(result.new_token) is True


def test_no_response_includes_holder_disconnected(manager, clock):
    holder_token = manager.acquire("c1", "Alice")
    manager.mark_disconnected("c1")
    manager.request_takeover("c2", "9.9.9.9", "Bob")

    clock.advance(11)
    result = manager.check_pending_expiration()

    assert isinstance(result, TransferResult)
    assert result.requester_client_id == "c2"


def test_holder_releasing_while_request_pending_grants_requester(manager):
    holder_token = manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "9.9.9.9", "Bob")

    result = manager.release(holder_token)

    assert isinstance(result, TransferResult)
    assert result.requester_client_id == "c2"
    assert manager.is_holder(holder_token) is False
    assert manager.snapshot()["holder_display_name"] == "Bob"


def test_release_without_pending_request_returns_none(manager):
    token = manager.acquire("c1", "Alice")
    assert manager.release(token) is None
