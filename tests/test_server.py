import time

import pytest
from fastapi.testclient import TestClient

from devgui.runtime.bus import BusManager
from devgui.runtime.devices import DeviceManager, DeviceState
from devgui.server import create_app
from devgui.widgets import Button, Category, Device, NumberInput, Select
from examples.mock_devices import MockDevice


class Harness:
    def __init__(self):
        self.psu = MockDevice()
        self.selected: list[object] = []

        self.layout = [
            Category(
                "Power",
                [
                    Device(
                        "PSU1",
                        self.psu,
                        widgets=[
                            Button("Reset", call=lambda: "reset-ok"),
                            NumberInput("Voltage", call=self.psu.set, min=0, max=10),
                            Select(
                                "Range",
                                call=lambda v: self.selected.append(v),
                                options={"Low": 1, "High": 2},
                            ),
                        ],
                        auto_open=False,
                    ),
                ],
            ),
            Category("Placeholders", [Device("LOatt3", None, widgets=[])]),
        ]
        self.bus_manager = BusManager()
        self.device_manager = DeviceManager(self.layout, self.bus_manager)
        self.app = create_app(self.layout, self.device_manager, self.bus_manager)

    def stop(self):
        self.bus_manager.stop_all(wait=False)


@pytest.fixture
def harness():
    h = Harness()
    yield h
    h.stop()


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_api_layout_structure(harness):
    with TestClient(harness.app) as client:
        resp = client.get("/api/layout")
        assert resp.status_code == 200
        data = resp.json()
        assert [c["title"] for c in data["categories"]] == ["Power", "Placeholders"]
        psu = data["categories"][0]["devices"][0]
        assert psu["name"] == "PSU1"
        assert psu["state"] == "disconnected"
        assert psu["can_open"] is True
        assert [w["type"] for w in psu["widgets"]] == ["Button", "NumberInput", "Select"]
        assert psu["widgets"][2]["options"] == ["Low", "High"]

        placeholder = data["categories"][1]["devices"][0]
        assert placeholder["state"] == "not_installed"
        assert placeholder["can_open"] is False


def test_api_call_button(harness):
    with TestClient(harness.app) as client:
        widget_id = "PSU1:0"
        resp = client.post(f"/api/call/{widget_id}", json={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_api_call_number_input_sets_value(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/call/PSU1:1", json={"value": 3.5})
        assert resp.json() == {"ok": True}
        assert harness.psu.value == 3.5


def test_api_call_number_input_out_of_range_returns_ok_false(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/call/PSU1:1", json={"value": 100})
        body = resp.json()
        assert body["ok"] is False
        assert "above max" in body["error"]


def test_api_call_select_maps_display_key_to_value(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/call/PSU1:2", json={"value": "High"})
        assert resp.json() == {"ok": True}
        assert harness.selected == [2]


def test_api_call_unknown_widget_404(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/call/does-not-exist", json={})
        assert resp.status_code == 404


def test_api_device_open_close_roundtrip(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/devices/PSU1/open")
        assert resp.json() == {"ok": True}
        assert wait_until(lambda: harness.device_manager.get("PSU1").state == DeviceState.CONNECTED)

        resp = client.post("/api/devices/PSU1/close")
        assert resp.json() == {"ok": True}
        assert wait_until(
            lambda: harness.device_manager.get("PSU1").state == DeviceState.DISCONNECTED
        )


def test_api_device_open_not_installed_returns_409(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/devices/LOatt3/open")
        assert resp.status_code == 409


def test_api_device_open_unknown_device_404(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/devices/NoSuchDevice/open")
        assert resp.status_code == 404


def test_websocket_hello_and_snapshot(harness):
    with TestClient(harness.app) as client:
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert "client_id" in hello

            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["devices"]["PSU1"]["state"] == "disconnected"
            assert snapshot["devices"]["LOatt3"]["state"] == "not_installed"


def test_websocket_receives_device_state_broadcast_on_manual_open(harness):
    with TestClient(harness.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/devices/PSU1/open")

            connecting = ws.receive_json(mode="text")
            assert connecting == {
                "type": "device_state",
                "name": "PSU1",
                "state": "connecting",
                "error_message": None,
            }

            connected = ws.receive_json(mode="text")
            assert connected == {
                "type": "device_state",
                "name": "PSU1",
                "state": "connected",
                "error_message": None,
            }
