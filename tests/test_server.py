import logging
import time

import pytest
from fastapi.testclient import TestClient

from devgui.runtime.bus import BusManager
from devgui.runtime.devices import DeviceManager, DeviceState
from devgui.server import create_app
from devgui.widgets import Button, Category, Device, DigitInput, Display, NumberInput, Select
from examples.mock_devices import MockDevice


class DualPurposeValue:
    """Mimics finerdev's freq()/amp()-style methods: no-arg call reads,
    one-arg call writes - the same callable is used for both `call` and
    `get` on a gettable NumberInput."""

    def __init__(self, initial=0.0):
        self.value = initial
        self.calls: list[float] = []

    def __call__(self, value=None):
        if value is None:
            return self.value
        self.value = value
        self.calls.append(value)


class Harness:
    def __init__(self, *, takeover_wait=10.0, takeover_cooldown=30.0, title="devgui"):
        self.psu = MockDevice()
        self.att_mock = MockDevice()
        self.synth_mock = MockDevice()
        self.synth_freq = DualPurposeValue(0.0)
        self.selected: list[object] = []
        self.att_values: list[int] = []

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
                            Display("Readback", call=self.psu.get, poll=0.02),
                        ],
                        auto_open=False,
                    ),
                    Device(
                        "Att1",
                        self.att_mock,
                        widgets=[
                            DigitInput(
                                "Attenuation",
                                call=lambda v: self.att_values.append(v),
                                digits=4,
                                min=0,
                                max=4095,
                                get=self.att_mock.get,
                            ),
                        ],
                        auto_open=False,
                    ),
                    Device(
                        "Synth1",
                        self.synth_mock,
                        widgets=[
                            NumberInput(
                                "Freq",
                                call=self.synth_freq,
                                get=self.synth_freq,
                                unit="GHz",
                                min=0,
                                max=20,
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
        self.app = create_app(
            self.layout,
            self.device_manager,
            self.bus_manager,
            operator_takeover_wait=takeover_wait,
            operator_takeover_cooldown=takeover_cooldown,
            title=title,
        )

    def stop(self):
        self.bus_manager.stop_all(wait=False)


@pytest.fixture
def harness():
    h = Harness()
    yield h
    h.stop()


@pytest.fixture
def fast_takeover_harness():
    h = Harness(takeover_wait=0.1, takeover_cooldown=0.1)
    yield h
    h.stop()


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def receive_until(ws, want_type, max_messages=20):
    """Pull websocket messages until one of `want_type` shows up, skipping
    any interleaved messages (device_state, display_entry from polling,
    other command_log lines, ...) along the way."""
    for _ in range(max_messages):
        msg = ws.receive_json(mode="text")
        if msg["type"] == want_type:
            return msg
    raise AssertionError(f"did not receive a {want_type!r} message within {max_messages} tries")


def operator_headers(client, client_id="test-client"):
    resp = client.post("/api/operator/acquire", headers={"X-Client-Id": client_id})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"X-Client-Id": client_id, "X-Operator-Token": token}


def test_api_layout_structure(harness):
    with TestClient(harness.app) as client:
        resp = client.get("/api/layout")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "devgui"
        assert [c["title"] for c in data["categories"]] == ["Power", "Placeholders"]
        psu = data["categories"][0]["devices"][0]
        assert psu["name"] == "PSU1"
        assert psu["state"] == "disconnected"
        assert psu["can_open"] is True
        assert [w["type"] for w in psu["widgets"]] == [
            "Button",
            "NumberInput",
            "Select",
            "Display",
        ]
        assert psu["widgets"][2]["options"] == ["Low", "High"]

        placeholder = data["categories"][1]["devices"][0]
        assert placeholder["state"] == "not_installed"
        assert placeholder["can_open"] is False


def test_api_layout_uses_configured_title():
    h = Harness(title="Lab Instruments")
    try:
        with TestClient(h.app) as client:
            resp = client.get("/api/layout")
            assert resp.json()["title"] == "Lab Instruments"
    finally:
        h.stop()


def test_api_call_without_operator_token_returns_403(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/call/PSU1:0", json={})
        assert resp.status_code == 403


def test_api_device_open_without_operator_token_returns_403(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/devices/PSU1/open")
        assert resp.status_code == 403


def test_api_call_button(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        widget_id = "PSU1:0"
        resp = client.post(f"/api/call/{widget_id}", json={}, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


def test_api_call_number_input_sets_value(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/call/PSU1:1", json={"value": 3.5}, headers=headers)
        assert resp.json() == {"ok": True}
        assert harness.psu.value == 3.5


def test_api_call_number_input_out_of_range_returns_ok_false(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/call/PSU1:1", json={"value": 100}, headers=headers)
        body = resp.json()
        assert body["ok"] is False
        assert "above max" in body["error"]


def test_api_call_success_logs_info_with_who_what_value(harness, caplog):
    with TestClient(harness.app) as client:
        headers = operator_headers(client, "alice")
        with caplog.at_level(logging.INFO):
            client.post("/api/call/PSU1:1", json={"value": 3.5}, headers=headers)
        messages = [r.message for r in caplog.records]
        assert any("PSU1:1" in m and "alice" in m and "3.5" in m and "ok" in m for m in messages)


def test_api_call_failure_logs_exception_with_traceback(harness, caplog):
    with TestClient(harness.app) as client:
        headers = operator_headers(client, "alice")
        with caplog.at_level(logging.INFO):
            client.post("/api/call/PSU1:1", json={"value": 100}, headers=headers)
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 1
        assert error_records[0].exc_info is not None


def test_api_call_select_maps_display_key_to_value(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/call/PSU1:2", json={"value": "High"}, headers=headers)
        assert resp.json() == {"ok": True}
        assert harness.selected == [2]


def test_digit_input_is_serialized_with_digits_min_max(harness):
    with TestClient(harness.app) as client:
        resp = client.get("/api/layout")
        att_device = resp.json()["categories"][0]["devices"][1]
        assert att_device["name"] == "Att1"
        widget = att_device["widgets"][0]
        assert widget["type"] == "DigitInput"
        assert widget["digits"] == 4
        assert widget["min"] == 0
        assert widget["max"] == 4095
        assert widget["default"] == 0


def test_api_call_digit_input_sets_value(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/call/Att1:0", json={"value": 2048}, headers=headers)
        assert resp.json() == {"ok": True}
        assert harness.att_values == [2048]


def test_api_call_digit_input_out_of_range_returns_ok_false(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/call/Att1:0", json={"value": 9999}, headers=headers)
        body = resp.json()
        assert body["ok"] is False
        assert "above max" in body["error"]
        assert harness.att_values == []


def test_digit_input_get_is_read_on_connect_and_reflected_in_layout(harness):
    harness.att_mock.value = 777.0
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/devices/Att1/open", headers=headers)
        assert resp.json() == {"ok": True}
        assert wait_until(
            lambda: harness.device_manager.get("Att1").state == DeviceState.CONNECTED
        )
        # widget.default is mutated in place, so a fresh /api/layout fetch
        # (this one, or a reload) reflects the value read back on connect.
        assert wait_until(
            lambda: client.get("/api/layout").json()["categories"][0]["devices"][1]["widgets"][0][
                "default"
            ]
            == 777
        )


def test_digit_input_get_broadcasts_widget_value_on_connect(harness):
    harness.att_mock.value = 42.0
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/devices/Att1/open", headers=headers)

            # device_state (connecting), device_state (connected),
            # command_log (open()), widget_value (get-on-connect),
            # command_log (get()) - the command log (section 8.2) means
            # widget_value is no longer the only message that follows.
            messages = []
            for _ in range(5):
                messages.append(ws.receive_json(mode="text"))
            widget_value_msgs = [m for m in messages if m["type"] == "widget_value"]
            assert widget_value_msgs == [
                {"type": "widget_value", "widget_id": "Att1:0", "value": 42}
            ]


def test_number_input_serializes_has_get(harness):
    with TestClient(harness.app) as client:
        data = client.get("/api/layout").json()
        plain = data["categories"][0]["devices"][0]["widgets"][1]  # PSU1's Voltage
        gettable = data["categories"][0]["devices"][2]["widgets"][0]  # Synth1's Freq
        assert plain["type"] == "NumberInput"
        assert plain["has_get"] is False
        assert gettable["type"] == "NumberInput"
        assert gettable["has_get"] is True


def test_number_input_get_is_read_on_connect(harness):
    harness.synth_freq.value = 9.5
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        client.post("/api/devices/Synth1/open", headers=headers)
        assert wait_until(
            lambda: harness.device_manager.get("Synth1").state == DeviceState.CONNECTED
        )
        assert wait_until(
            lambda: client.get("/api/layout").json()["categories"][0]["devices"][2]["widgets"][
                0
            ]["default"]
            == 9.5
        )


def test_number_input_get_is_reread_after_successful_set(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/call/Synth1:0", json={"value": 5.0}, headers=headers)
        assert resp.json() == {"ok": True}
        assert harness.synth_freq.calls == [5.0]
        # freq() is called again after the set succeeds, and its result
        # (not just the value we sent) is what ends up in `default`.
        assert wait_until(
            lambda: client.get("/api/layout").json()["categories"][0]["devices"][2]["widgets"][
                0
            ]["default"]
            == 5.0
        )


def test_number_input_get_broadcasts_widget_value_after_set(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/call/Synth1:0", json={"value": 7.25}, headers=headers)

            # command_log (the set() call itself), widget_value
            # (get-after-set), command_log (the get() call) - see section
            # 8.2; widget_value is no longer the only message sent back.
            messages = [ws.receive_json(mode="text") for _ in range(3)]
            widget_value_msgs = [m for m in messages if m["type"] == "widget_value"]
            assert widget_value_msgs == [
                {"type": "widget_value", "widget_id": "Synth1:0", "value": 7.25}
            ]


def test_api_call_unknown_widget_404(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/call/does-not-exist", json={}, headers=headers)
        assert resp.status_code == 404


def test_api_device_open_close_roundtrip(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/devices/PSU1/open", headers=headers)
        assert resp.json() == {"ok": True}
        assert wait_until(lambda: harness.device_manager.get("PSU1").state == DeviceState.CONNECTED)

        resp = client.post("/api/devices/PSU1/close", headers=headers)
        assert resp.json() == {"ok": True}
        assert wait_until(
            lambda: harness.device_manager.get("PSU1").state == DeviceState.DISCONNECTED
        )


def test_api_device_open_not_installed_returns_409(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/devices/LOatt3/open", headers=headers)
        assert resp.status_code == 409


def test_api_device_open_unknown_device_404(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        resp = client.post("/api/devices/NoSuchDevice/open", headers=headers)
        assert resp.status_code == 404


def test_static_index_and_assets_are_served(harness):
    with TestClient(harness.app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "devgui" in index.text

        app_js = client.get("/app.js")
        assert app_js.status_code == 200
        assert "api/layout" in app_js.text

        style_css = client.get("/style.css")
        assert style_css.status_code == 200

        # Regression guard: browsers may heuristically cache a static file
        # with no Cache-Control and skip revalidation, so a CSS/JS fix would
        # silently not take effect until a hard reload (see the [hidden]
        # override rule below, and its bug history in style.css).
        assert index.headers.get("cache-control") == "no-cache"
        assert app_js.headers.get("cache-control") == "no-cache"
        assert style_css.headers.get("cache-control") == "no-cache"


def test_style_css_restores_hidden_attribute_on_display_setting_classes(harness):
    with TestClient(harness.app) as client:
        style_css = client.get("/style.css").text
        # .modal-overlay/.panel-grid set `display` directly, which has equal
        # CSS specificity to the browser default `[hidden] { display: none }`
        # and would win the tie (author stylesheet beats UA stylesheet) -
        # silently breaking every el.hidden = true/false toggle in app.js.
        assert "[hidden]" in style_css
        assert "display: none !important" in style_css


def test_operator_acquire_when_free(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/operator/acquire", headers={"X-Client-Id": "c1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["token"]
        assert body["display_name"]


def test_operator_acquire_requires_client_id_header(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/operator/acquire")
        assert resp.status_code == 400


def test_operator_acquire_when_held_returns_409(harness):
    with TestClient(harness.app) as client:
        operator_headers(client, "c1")
        resp = client.post("/api/operator/acquire", headers={"X-Client-Id": "c2"})
        assert resp.status_code == 409


def test_operator_release_then_someone_else_can_acquire(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client, "c1")
        resp = client.post("/api/operator/release", headers=headers)
        assert resp.json() == {"ok": True}

        resp2 = client.post("/api/operator/acquire", headers={"X-Client-Id": "c2"})
        assert resp2.status_code == 200


def test_operator_release_without_token_returns_403(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/operator/release")
        assert resp.status_code == 403


def test_operator_release_with_wrong_token_returns_403(harness):
    with TestClient(harness.app) as client:
        operator_headers(client, "c1")
        resp = client.post(
            "/api/operator/release", headers={"X-Operator-Token": "not-the-token"}
        )
        assert resp.status_code == 403


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
            assert snapshot["displays"] == {"PSU1:3": []}
            assert snapshot["operator"] == {
                "holder_display_name": None,
                "is_held": False,
                "request_pending": False,
            }


def test_websocket_snapshot_includes_instance_creation_command_log(harness):
    with TestClient(harness.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            snapshot = ws.receive_json()

            by_device = {entry["device"]: entry for entry in snapshot["command_log"]}
            assert by_device["PSU1"]["text"] == "PSU1 = MockDevice(...)"
            assert by_device["Att1"]["text"] == "Att1 = MockDevice(...)"
            assert by_device["Synth1"]["text"] == "Synth1 = MockDevice(...)"
            assert "LOatt3" not in by_device  # instance=None: nothing was constructed


def test_api_call_broadcasts_command_log_entry(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/call/PSU1:1", json={"value": 5}, headers=headers)  # Voltage

            entry = receive_until(ws, "command_log")
            assert entry["device"] == "PSU1"
            assert entry["text"] == "PSU1.set(5.0) -> None"
            assert entry["error"] is False


def test_api_call_failure_broadcasts_command_log_error_entry(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/call/PSU1:1", json={"value": 999}, headers=headers)  # out of range

            entry = receive_until(ws, "command_log")
            assert entry["device"] == "PSU1"
            assert entry["error"] is True
            assert "PSU1.set(999" in entry["text"]
            assert "ValueError" in entry["text"]


def test_device_open_and_close_emit_command_log_entries(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/devices/PSU1/open", headers=headers)
            opened = receive_until(ws, "command_log")
            assert opened == {
                "type": "command_log",
                "t": opened["t"],
                "device": "PSU1",
                "text": "PSU1.open() -> None",
                "error": False,
            }

            client.post("/api/devices/PSU1/close", headers=headers)
            closed = receive_until(ws, "command_log")
            assert closed["device"] == "PSU1"
            assert closed["text"] == "PSU1.close() -> None"


def test_websocket_receives_operator_state_broadcast_on_acquire_and_release(harness):
    with TestClient(harness.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            resp = client.post("/api/operator/acquire", headers={"X-Client-Id": "c1"})
            token = resp.json()["token"]

            acquired = ws.receive_json(mode="text")
            assert acquired["type"] == "operator_state"
            assert acquired["is_held"] is True
            assert acquired["holder_display_name"]

            client.post("/api/operator/release", headers={"X-Operator-Token": token})
            released = ws.receive_json(mode="text")
            assert released == {
                "type": "operator_state",
                "holder_display_name": None,
                "is_held": False,
                "request_pending": False,
            }


def test_websocket_receives_display_entry_after_device_connects(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/devices/PSU1/open", headers=headers)
            ws.receive_json()  # device_state: connecting
            ws.receive_json()  # device_state: connected
            ws.receive_json()  # command_log: PSU1.open() -> ... (section 8.2)

            entry = ws.receive_json(mode="text")
            assert entry["type"] == "display_entry"
            assert entry["widget_id"] == "PSU1:3"
            assert entry["error"] is False
            assert "t" in entry and "value" in entry


def test_websocket_receives_device_state_broadcast_on_manual_open(harness):
    with TestClient(harness.app) as client:
        headers = operator_headers(client)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.receive_json()  # snapshot

            client.post("/api/devices/PSU1/open", headers=headers)

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


# --- forced takeover (section 9.4) ---


def test_operator_request_when_free_grants_immediately(harness):
    with TestClient(harness.app) as client:
        resp = client.post("/api/operator/request", headers={"X-Client-Id": "c1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["granted_immediately"] is True
        assert body["token"]


def test_operator_request_when_held_notifies_holder(harness):
    with TestClient(harness.app) as client:
        with client.websocket_connect("/ws") as holder_ws:
            holder_client_id = holder_ws.receive_json()["client_id"]
            holder_ws.receive_json()  # snapshot
            holder_headers = operator_headers(client, holder_client_id)
            holder_ws.receive_json(mode="text")  # operator_state from our own acquire

            with client.websocket_connect("/ws") as requester_ws:
                requester_client_id = requester_ws.receive_json()["client_id"]
                requester_ws.receive_json()  # snapshot

                resp = client.post(
                    "/api/operator/request", headers={"X-Client-Id": requester_client_id}
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["granted_immediately"] is False
                request_id = body["request_id"]

                takeover_request = holder_ws.receive_json(mode="text")
                assert takeover_request["type"] == "takeover_request"
                assert takeover_request["request_id"] == request_id
                assert takeover_request["requester_display_name"]

                op_state = holder_ws.receive_json(mode="text")
                assert op_state["type"] == "operator_state"
                assert op_state["is_held"] is True
                assert op_state["request_pending"] is True


def test_operator_request_second_while_pending_returns_409(harness):
    with TestClient(harness.app) as client:
        operator_headers(client, "holder")
        client.post("/api/operator/request", headers={"X-Client-Id": "requester1"})
        resp = client.post("/api/operator/request", headers={"X-Client-Id": "requester2"})
        assert resp.status_code == 409


def test_operator_request_accept_transfers_and_notifies_both(harness):
    with TestClient(harness.app) as client:
        with client.websocket_connect("/ws") as holder_ws:
            holder_client_id = holder_ws.receive_json()["client_id"]
            holder_ws.receive_json()
            holder_headers = operator_headers(client, holder_client_id)
            holder_ws.receive_json(mode="text")  # operator_state from our own acquire

            with client.websocket_connect("/ws") as requester_ws:
                requester_client_id = requester_ws.receive_json()["client_id"]
                requester_ws.receive_json()

                resp = client.post(
                    "/api/operator/request", headers={"X-Client-Id": requester_client_id}
                )
                request_id = resp.json()["request_id"]
                holder_ws.receive_json(mode="text")  # takeover_request
                holder_ws.receive_json(mode="text")  # operator_state (request_pending)
                requester_ws.receive_json(mode="text")  # operator_state (request_pending)

                resp = client.post(
                    f"/api/operator/request/{request_id}/respond",
                    json={"accept": True},
                    headers=holder_headers,
                )
                assert resp.status_code == 200

                revoked = holder_ws.receive_json(mode="text")
                assert revoked["type"] == "operator_revoked"

                granted = requester_ws.receive_json(mode="text")
                assert granted["type"] == "operator_granted"
                new_token = granted["token"]

                result = requester_ws.receive_json(mode="text")
                assert result == {"type": "takeover_result", "result": "granted"}

                op_state = requester_ws.receive_json(mode="text")
                assert op_state["is_held"] is True
                assert op_state["request_pending"] is False

                # The old holder's token no longer works.
                resp = client.post("/api/call/PSU1:0", json={}, headers=holder_headers)
                assert resp.status_code == 403

                # The new token does.
                resp = client.post(
                    "/api/call/PSU1:0",
                    json={},
                    headers={"X-Client-Id": requester_client_id, "X-Operator-Token": new_token},
                )
                assert resp.status_code == 200


def test_operator_request_reject_notifies_requester_and_sets_cooldown(harness):
    with TestClient(harness.app) as client:
        holder_headers = operator_headers(client, "holder")

        with client.websocket_connect("/ws") as requester_ws:
            requester_client_id = requester_ws.receive_json()["client_id"]
            requester_ws.receive_json()

            resp = client.post(
                "/api/operator/request", headers={"X-Client-Id": requester_client_id}
            )
            request_id = resp.json()["request_id"]
            requester_ws.receive_json(mode="text")  # operator_state (request_pending)

            resp = client.post(
                f"/api/operator/request/{request_id}/respond",
                json={"accept": False},
                headers=holder_headers,
            )
            assert resp.status_code == 200

            result = requester_ws.receive_json(mode="text")
            assert result == {"type": "takeover_result", "result": "rejected"}

        # Still on cooldown immediately after rejection.
        resp = client.post(
            "/api/operator/request", headers={"X-Client-Id": requester_client_id}
        )
        assert resp.status_code == 429


def test_operator_request_cancel_by_requester(harness):
    with TestClient(harness.app) as client:
        operator_headers(client, "holder")
        resp = client.post("/api/operator/request", headers={"X-Client-Id": "requester"})
        request_id = resp.json()["request_id"]

        resp = client.post(
            f"/api/operator/request/{request_id}/cancel", headers={"X-Client-Id": "requester"}
        )
        assert resp.status_code == 200

        # No longer blocked: someone else can request now.
        resp = client.post("/api/operator/request", headers={"X-Client-Id": "someone-else"})
        assert resp.status_code == 200


def test_operator_request_cancel_by_non_requester_returns_404(harness):
    with TestClient(harness.app) as client:
        operator_headers(client, "holder")
        resp = client.post("/api/operator/request", headers={"X-Client-Id": "requester"})
        request_id = resp.json()["request_id"]

        resp = client.post(
            f"/api/operator/request/{request_id}/cancel", headers={"X-Client-Id": "not-requester"}
        )
        assert resp.status_code == 404


def test_operator_request_respond_by_non_holder_returns_403(harness):
    with TestClient(harness.app) as client:
        operator_headers(client, "holder")
        resp = client.post("/api/operator/request", headers={"X-Client-Id": "requester"})
        request_id = resp.json()["request_id"]

        resp = client.post(
            f"/api/operator/request/{request_id}/respond",
            json={"accept": True},
            headers={"X-Operator-Token": "not-the-holder-token"},
        )
        assert resp.status_code == 403


def test_requester_disconnect_cancels_pending_request(harness):
    with TestClient(harness.app) as client:
        operator_headers(client, "holder")

        with client.websocket_connect("/ws") as requester_ws:
            hello = requester_ws.receive_json()
            requester_client_id = hello["client_id"]
            requester_ws.receive_json()  # snapshot

            client.post("/api/operator/request", headers={"X-Client-Id": requester_client_id})
            # Requester's own websocket disconnects here (context manager exit).

        # A different client can now request without hitting 409.
        resp = client.post("/api/operator/request", headers={"X-Client-Id": "someone-else"})
        assert resp.status_code == 200


def test_holder_releasing_while_request_pending_grants_requester(harness):
    with TestClient(harness.app) as client:
        holder_headers = operator_headers(client, "holder")

        with client.websocket_connect("/ws") as requester_ws:
            requester_client_id = requester_ws.receive_json()["client_id"]
            requester_ws.receive_json()

            client.post("/api/operator/request", headers={"X-Client-Id": requester_client_id})
            requester_ws.receive_json(mode="text")  # operator_state (request_pending)
            client.post("/api/operator/release", headers=holder_headers)

            granted = requester_ws.receive_json(mode="text")
            assert granted["type"] == "operator_granted"


def test_takeover_timeout_grants_requester_with_no_response(fast_takeover_harness):
    with TestClient(fast_takeover_harness.app) as client:
        operator_headers(client, "holder")

        with client.websocket_connect("/ws") as requester_ws:
            requester_client_id = requester_ws.receive_json()["client_id"]
            requester_ws.receive_json()

            client.post("/api/operator/request", headers={"X-Client-Id": requester_client_id})
            requester_ws.receive_json(mode="text")  # operator_state (request_pending)

            granted = requester_ws.receive_json(mode="text")
            assert granted["type"] == "operator_granted"
            result = requester_ws.receive_json(mode="text")
            assert result == {"type": "takeover_result", "result": "granted"}
