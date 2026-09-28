import pytest

from devgui.runtime.operator import (
    InvalidOperatorTokenError,
    OperatorHeldError,
    OperatorManager,
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
    assert manager.snapshot() == {"holder_display_name": "Alice (1.2.3.4)", "is_held": True}


def test_acquire_when_held_raises(manager):
    manager.acquire("c1", "Alice")
    with pytest.raises(OperatorHeldError):
        manager.acquire("c2", "Bob")


def test_release_clears_holder(manager):
    token = manager.acquire("c1", "Alice")
    manager.release(token)
    assert manager.is_held is False
    assert manager.snapshot() == {"holder_display_name": None, "is_held": False}


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
