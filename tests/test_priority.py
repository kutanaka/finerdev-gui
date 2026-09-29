"""External force-acquire/block of the operator right (design.md section
9.5): OperatorManager.external_*, /api/priority/*, devgui.priority."""

import socket

import pytest
from fastapi.testclient import TestClient

from devgui.priority import PriorityError, priority
from devgui.runtime.bus import BusManager
from devgui.runtime.devices import DeviceManager
from devgui.runtime.operator import (
    InvalidOperatorTokenError,
    NotExternallyHeldError,
    OperatorBlockedError,
    OperatorHeldError,
    OperatorManager,
    TransferResult,
)
from devgui.server import create_app
from devgui.widgets import Category, Device

from tests.test_operator import FakeClock
from tests.test_server import receive_until


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def manager(clock):
    return OperatorManager(idle_timeout=600.0, disconnect_grace=30.0, takeover_wait=10.0, clock=clock)


def test_external_acquire_when_free_never_expires(manager, clock):
    manager.external_acquire("script", block=False, force=False)
    snap = manager.snapshot()
    assert snap["is_held"] and snap["external"] and not snap["blocked"]
    clock.advance(10_000)
    assert manager.check_expiration() is False
    assert manager.is_held


def test_external_acquire_without_force_fails_when_gui_holds(manager):
    manager.acquire("c1", "Alice")
    with pytest.raises(OperatorHeldError):
        manager.external_acquire("script", block=True, force=False)


def test_force_revokes_gui_holder_and_drops_pending_request(manager):
    old_token = manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "1.2.3.4", "Bob")
    result = manager.external_acquire("script", block=False, force=True)
    assert result.old_holder_client_id == "c1"
    assert result.dropped_request.requester_client_id == "c2"
    assert not manager.is_holder(old_token)
    assert manager.snapshot()["request_pending"] is False


def test_block_refuses_gui_acquire_and_request_until_release(manager):
    held = manager.external_acquire("script", block=True, force=True)
    with pytest.raises(OperatorBlockedError):
        manager.acquire("c1", "Alice")
    with pytest.raises(OperatorBlockedError):
        manager.request_takeover("c1", "1.2.3.4", "Alice")
    assert manager.external_release(held.token) is None
    assert manager.snapshot()["blocked"] is False
    assert manager.acquire("c1", "Alice")


def test_unblocked_external_hold_is_taken_over_after_wait(manager, clock):
    manager.external_acquire("script", block=False, force=False)
    manager.request_takeover("c1", "1.2.3.4", "Alice")
    clock.advance(11)
    result = manager.check_pending_expiration()
    assert isinstance(result, TransferResult)
    assert result.requester_client_id == "c1"
    assert manager.snapshot()["external"] is False


def test_external_release_when_not_external_raises(manager):
    with pytest.raises(NotExternallyHeldError):
        manager.external_release("x")
    token = manager.acquire("c1", "Alice")
    with pytest.raises(NotExternallyHeldError):
        manager.external_release(token)


def test_external_release_only_by_the_getter(manager):
    manager.external_acquire("script", block=True, force=False)
    with pytest.raises(InvalidOperatorTokenError):
        manager.external_release("someone-else")
    with pytest.raises(InvalidOperatorTokenError):
        manager.external_release(None)
    assert manager.snapshot()["blocked"] is True


def test_force_release_frees_whoever_holds_it(manager):
    manager.external_acquire("script", block=True, force=False)
    forced = manager.force_release()
    assert forced.old_holder_client_id is None
    assert manager.snapshot() == {
        "holder_display_name": None,
        "is_held": False,
        "request_pending": False,
        "external": False,
        "blocked": False,
    }

    token = manager.acquire("c1", "Alice")
    manager.request_takeover("c2", "1.2.3.4", "Bob")
    forced = manager.force_release()
    assert forced.old_holder_client_id == "c1"
    assert forced.dropped_request.requester_client_id == "c2"
    assert not manager.is_holder(token)

    assert manager.force_release().old_holder_client_id is None  # free: no-op


def _app(**kwargs):
    layout = [Category("C", [Device("D", None, widgets=[])])]
    bus_manager = BusManager()
    return create_app(layout, DeviceManager(layout, bus_manager), bus_manager, **kwargs), bus_manager


@pytest.fixture
def client():
    # TestClient's requests come from host "testclient".
    app, bus_manager = _app(priority_hosts=("testclient",))
    with TestClient(app) as c:
        yield c
    bus_manager.stop_all(wait=False)


def test_priority_api_refused_from_non_allowed_host():
    app, bus_manager = _app()  # default: loopback only
    with TestClient(app) as c:
        assert c.post("/api/priority/get", json={"force": True}).status_code == 403
        assert c.post("/api/priority/release").status_code == 403
    bus_manager.stop_all(wait=False)


def test_priority_force_block_revokes_holder_and_blocks_gui(client):
    token = client.post("/api/operator/acquire", headers={"X-Client-Id": "c1"}).json()["token"]
    with client.websocket_connect("/ws") as ws:
        receive_until(ws, "snapshot")
        resp = client.post("/api/priority/get", json={"block": True, "force": True, "name": "script"})
        assert resp.json()["ok"] is True
        state = receive_until(ws, "operator_state")
        assert state["holder_display_name"] == "script"
        assert state["external"] is True and state["blocked"] is True

    # the old GUI token is dead, and the GUI can't get back in
    assert client.post("/api/call/D:0", json={}, headers={"X-Operator-Token": token}).status_code == 403
    assert client.post("/api/operator/acquire", headers={"X-Client-Id": "c2"}).status_code == 423
    assert client.post("/api/operator/request", headers={"X-Client-Id": "c2"}).status_code == 423

    got_token = resp.json()["token"]
    assert client.post("/api/priority/release", json={"token": "wrong"}).status_code == 403
    assert client.post("/api/priority/release", json={"token": got_token}).json() == {"ok": True}
    assert client.post("/api/operator/acquire", headers={"X-Client-Id": "c2"}).status_code == 200


def test_priority_get_without_force_conflicts_with_gui_holder(client):
    client.post("/api/operator/acquire", headers={"X-Client-Id": "c1"})
    assert client.post("/api/priority/get", json={"block": True}).status_code == 409


def test_priority_release_when_not_held_externally_is_409(client):
    assert client.post("/api/priority/release", json={"token": "x"}).status_code == 409


def test_priority_force_release_by_another_program_unblocks(client):
    client.post("/api/priority/get", json={"block": True, "name": "script A"})
    # a different program, without script A's token
    assert client.post("/api/priority/release", json={"force": True}).json() == {"ok": True}
    assert client.post("/api/operator/acquire", headers={"X-Client-Id": "c1"}).status_code == 200


def test_priority_force_release_revokes_gui_holder(client):
    token = client.post("/api/operator/acquire", headers={"X-Client-Id": "c1"}).json()["token"]
    with client.websocket_connect("/ws") as ws:
        receive_until(ws, "snapshot")
        client.post("/api/priority/release", json={"force": True})
        state = receive_until(ws, "operator_state")
        assert state["is_held"] is False
    assert client.post("/api/operator/release", headers={"X-Operator-Token": token}).status_code == 403


def test_request_response_carries_wait_seconds_for_countdown(client):
    client.post("/api/priority/get", json={})
    body = client.post("/api/operator/request", headers={"X-Client-Id": "c1"}).json()
    assert body["granted_immediately"] is False
    assert body["wait_seconds"] == 10.0


def test_priority_client_validates_action_and_reports_unreachable_server():
    with pytest.raises(ValueError):
        priority("take")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # nothing listens here once closed
    with pytest.raises(PriorityError, match="cannot reach"):
        priority("get", url=f"http://127.0.0.1:{port}", timeout=1)
