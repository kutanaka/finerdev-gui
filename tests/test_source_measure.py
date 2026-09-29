"""SourceMeasure: the composite source-meter widget (design.md section 4.3)
and its /api/call actions + /api/widgets/{id}/iv.pdf."""

import pytest
from fastapi.testclient import TestClient

from devgui.runtime.bus import BusManager
from devgui.runtime.devices import DeviceManager
from devgui.server import _iv_rows
from devgui.widgets import Category, Device, SourceMeasure

from tests.test_server import operator_headers, receive_until


class FakeSourceMeter:
    """finerdev SourceMeter-style API: methods return False on failure
    (with a reason queued for get_message()), get() returns the last
    measurement as [current, voltage, ...] or rows of that."""

    def __init__(self):
        self.volt = 0.0
        self.on = False
        self.val = [0, 0, 0, 0]
        self.msg = []
        self.calls = []

    def open(self):
        self.calls.append(("open",))

    def setV(self, volt):
        self.calls.append(("setV", volt))
        if volt > 0.004:
            self.msg.append(f"too high imput voltage {volt}.")
            return False
        self.volt = volt
        return True

    def output(self, onOff):
        self.calls.append(("output", onOff))
        self.on = onOff

    def meas(self):
        self.calls.append(("meas",))
        self.val = [self.volt / 1000, self.volt, 1.0, 2.0, 3.0]
        return True

    def measIV(self, vstart, vend, vstep):
        self.calls.append(("measIV", vstart, vend, vstep))
        n = int(round((vend - vstart) / vstep)) + 1
        self.val = [[(vstart + k * vstep) / 1000, vstart + k * vstep, 0.0] for k in range(n)]
        return True

    def get(self):
        self.calls.append(("get",))
        return self.val

    def get_message(self):
        text = ";".join(self.msg)
        self.msg = []
        return text


def _client(dev, **widget_kwargs):
    kwargs = dict(min=0, max=0.005, step=0.0001, sweep_default=(0, 0.005, 0.001))
    kwargs.update(widget_kwargs)
    layout = [
        Category(
            "RX",
            [
                Device(
                    "SM1",
                    dev,
                    widgets=[
                        SourceMeasure(
                            "SM",
                            call=dev.setV,
                            output=dev.output,
                            meas=dev.meas,
                            sweep=dev.measIV,
                            get=dev.get,
                            message=dev.get_message,
                            **kwargs,
                        )
                    ],
                )
            ],
        )
    ]
    from devgui.server import create_app

    bus_manager = BusManager()
    app = create_app(layout, DeviceManager(layout, bus_manager), bus_manager)
    return TestClient(app), bus_manager


@pytest.fixture
def sm():
    dev = FakeSourceMeter()
    client, bus_manager = _client(dev)
    with client:
        yield client, dev
    bus_manager.stop_all(wait=False)


@pytest.fixture
def sm_milli():
    """The same, with the panel in mV/mA (as in examples/layout_example.py)."""
    dev = FakeSourceMeter()
    client, bus_manager = _client(
        dev, voltage_unit="mV", current_unit="mA", max=5, step=0.1, sweep_default=(0, 5, 0.1)
    )
    with client:
        yield client, dev
    bus_manager.stop_all(wait=False)


def call(client, headers, action, value=None):
    return client.post("/api/call/SM1:0", json={"action": action, "value": value}, headers=headers).json()


def test_widget_requires_callables():
    with pytest.raises(TypeError):
        SourceMeasure("SM", call=print, output=print, meas=print, sweep="x", get=print)
    with pytest.raises(ValueError):
        SourceMeasure("SM", call=print, output=print, meas=print, sweep=print, get=print, min=1, max=0)
    with pytest.raises(ValueError):
        SourceMeasure("SM", call=print, output=print, meas=print, sweep=print, get=print, voltage_unit="kV")


def test_iv_rows_accepts_one_row_or_many_and_drops_extra_columns():
    np = pytest.importorskip("numpy")
    assert _iv_rows([1e-6, 0.001, 9, 9]) == [[1e-6, 0.001]]
    assert _iv_rows(np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])) == [[1.0, 2.0], [4.0, 5.0]]
    with pytest.raises(ValueError):
        _iv_rows([1.0])
    with pytest.raises(ValueError):
        _iv_rows([])


def test_layout_serializes_sweep_default_and_initial_state(sm):
    client, _ = sm
    widget = client.get("/api/layout").json()["categories"][0]["devices"][0]["widgets"][0]
    assert widget["type"] == "SourceMeasure"
    assert widget["sweep_default"] == [0, 0.005, 0.001]
    assert widget["state"] == {
        "output": False,
        "voltage": None,
        "current": None,
        "sweep": None,
        "running": None,
    }


def test_meas_and_sweep_refused_while_output_off(sm):
    client, dev = sm
    headers = operator_headers(client)
    body = call(client, headers, "meas", 0.001)
    assert body["ok"] is False and "output is off" in body["error"]
    body = call(client, headers, "sweep", {"vstart": 0, "vend": 0.002, "vstep": 0.001})
    assert body["ok"] is False
    assert not any(c[0] in ("setV", "meas", "measIV") for c in dev.calls)


def test_meas_runs_setv_meas_get_and_reports_current_and_voltage(sm):
    client, dev = sm
    headers = operator_headers(client)
    assert call(client, headers, "output", True)["state"]["output"] is True
    body = call(client, headers, "meas", 0.002)
    assert body["ok"] is True
    assert dev.calls[-3:] == [("setV", 0.002), ("meas",), ("get",)]
    assert body["state"]["current"] == pytest.approx(2e-6)
    assert body["state"]["voltage"] == 0.002


def test_meas_false_return_is_an_error_with_get_message_reason(sm):
    client, dev = sm
    headers = operator_headers(client)
    call(client, headers, "output", True)
    body = call(client, headers, "meas", 0.0045)
    assert body["ok"] is False
    assert "setV() returned False: too high imput voltage 0.0045." in body["error"]
    assert ("meas",) not in dev.calls


def test_meas_voltage_out_of_range_rejected_before_any_call(sm):
    client, dev = sm
    headers = operator_headers(client)
    call(client, headers, "output", True)
    body = call(client, headers, "meas", 0.01)
    assert body["ok"] is False and "above max" in body["error"]
    assert not any(c[0] == "setV" for c in dev.calls)


def test_sweep_stores_rows_and_last_row_becomes_reading(sm):
    client, dev = sm
    headers = operator_headers(client)
    call(client, headers, "output", True)
    body = call(client, headers, "sweep", {"vstart": 0, "vend": 0.003, "vstep": 0.001})
    assert body["ok"] is True
    assert ("measIV", 0.0, 0.003, 0.001) in dev.calls
    state = body["state"]
    assert len(state["sweep"]["rows"]) == 4
    assert state["voltage"] == pytest.approx(0.003)
    assert state["current"] == pytest.approx(3e-6)


def test_sweep_rejects_bad_params(sm):
    client, _ = sm
    headers = operator_headers(client)
    call(client, headers, "output", True)
    assert call(client, headers, "sweep", {"vstart": 0.003, "vend": 0.001, "vstep": 0.001})["ok"] is False
    assert call(client, headers, "sweep", {"vstart": 0, "vend": 0.001, "vstep": 0})["ok"] is False


def test_state_is_broadcast_and_each_device_call_logged(sm):
    client, _ = sm
    headers = operator_headers(client)
    with client.websocket_connect("/ws") as ws:
        receive_until(ws, "snapshot")
        call(client, headers, "output", True)
        call(client, headers, "meas", 0.001)
        texts = []
        value = None
        for _ in range(30):
            msg = ws.receive_json()
            if msg["type"] == "command_log":
                texts.append(msg["text"])
            if msg["type"] == "widget_value" and msg["value"]["voltage"] is not None:
                value = msg["value"]
                break
        assert value["output"] is True and value["voltage"] == 0.001
        assert "SM1.output(True) -> None" in texts
        assert "SM1.setV(0.001) -> True" in texts
        assert "SM1.meas() -> True" in texts


def test_reopen_resets_output_state(sm):
    client, _ = sm
    headers = operator_headers(client)
    call(client, headers, "output", True)
    client.post("/api/devices/SM1/close", headers=headers)
    client.post("/api/devices/SM1/open", headers=headers)
    widget = client.get("/api/layout").json()["categories"][0]["devices"][0]["widgets"][0]
    assert widget["state"]["output"] is False


def test_iv_pdf_404_until_a_sweep_then_pdf(sm):
    client, _ = sm
    assert client.get("/api/widgets/SM1:0/iv.pdf").status_code == 404
    headers = operator_headers(client)
    call(client, headers, "output", True)
    call(client, headers, "sweep", {"vstart": 0, "vend": 0.003, "vstep": 0.001})
    resp = client.get("/api/widgets/SM1:0/iv.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "SM1_IV_" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-1.4")


def test_milli_units_scale_device_args_and_readings(sm_milli):
    client, dev = sm_milli
    widget = client.get("/api/layout").json()["categories"][0]["devices"][0]["widgets"][0]
    assert (widget["voltage_unit"], widget["current_unit"]) == ("mV", "mA")
    headers = operator_headers(client)
    call(client, headers, "output", True)

    body = call(client, headers, "meas", 0.3)
    assert ("setV", 0.0003) in dev.calls  # 0.3 mV -> V, without float noise
    # FakeSourceMeter: I = V / 1000 -> 0.3 mV gives 0.3 uA = 0.0003 mA
    assert body["state"]["voltage"] == 0.3
    assert body["state"]["current"] == pytest.approx(0.0003)

    body = call(client, headers, "sweep", {"vstart": 0, "vend": 3, "vstep": 1})
    assert ("measIV", 0.0, 0.003, 0.001) in dev.calls
    assert [r[1] for r in body["state"]["sweep"]["rows"]] == [0, 1, 2, 3]
    assert body["state"]["sweep"]["params"] == [0, 3, 1]

    # min/max are in the display unit: 5 mV is the limit, not 5 V
    assert call(client, headers, "meas", 6)["ok"] is False


def test_running_state_is_broadcast_during_sweep_and_cleared_after(sm):
    client, _ = sm
    headers = operator_headers(client)
    call(client, headers, "output", True)
    with client.websocket_connect("/ws") as ws:
        receive_until(ws, "snapshot")
        call(client, headers, "sweep", {"vstart": 0, "vend": 0.002, "vstep": 0.001})
        running = [receive_until(ws, "widget_value")["value"]["running"] for _ in range(2)]
    assert running == ["sweep", None]
