import pytest

from devgui.runtime.bus import BusManager
from devgui.runtime.devices import (
    DeviceManager,
    DeviceNotInstalledError,
    DeviceRuntime,
    DeviceState,
)
from devgui.widgets import Category, Device
from examples.mock_devices import ChildOnlyMockDevice, MockDevice, NoOpenMockDevice


@pytest.fixture
def bus_manager():
    m = BusManager()
    yield m
    m.stop_all(wait=False)


def test_device_with_open_starts_disconnected(bus_manager):
    runtime = DeviceRuntime(Device("D1", MockDevice(), widgets=[]), bus_manager)
    assert runtime.state == DeviceState.DISCONNECTED


def test_device_without_open_is_always_connected(bus_manager):
    runtime = DeviceRuntime(Device("D1", NoOpenMockDevice(), widgets=[]), bus_manager)
    assert runtime.state == DeviceState.CONNECTED


def test_not_installed_when_instance_is_none(bus_manager):
    runtime = DeviceRuntime(Device("D1", None, widgets=[]), bus_manager)
    assert runtime.state == DeviceState.NOT_INSTALLED


def test_not_installed_open_raises(bus_manager):
    runtime = DeviceRuntime(Device("D1", None, widgets=[]), bus_manager)
    with pytest.raises(DeviceNotInstalledError):
        runtime.open()


def test_open_success_transitions_to_connected(bus_manager):
    mock = MockDevice()
    runtime = DeviceRuntime(Device("D1", mock, widgets=[]), bus_manager)
    future = runtime.open()
    future.result(timeout=5)
    assert runtime.state == DeviceState.CONNECTED
    assert mock.is_open is True


def test_open_failure_transitions_to_error_with_message(bus_manager):
    mock = MockDevice(fail_open=True)
    runtime = DeviceRuntime(Device("D1", mock, widgets=[]), bus_manager)
    future = runtime.open()
    with pytest.raises(RuntimeError):
        future.result(timeout=5)
    assert runtime.state == DeviceState.ERROR
    assert "RuntimeError" in runtime.error_message


def test_error_state_can_retry_open(bus_manager):
    mock = MockDevice(fail_open=True)
    runtime = DeviceRuntime(Device("D1", mock, widgets=[]), bus_manager)
    with pytest.raises(RuntimeError):
        runtime.open().result(timeout=5)
    assert runtime.state == DeviceState.ERROR

    mock.fail_open = False
    runtime.open().result(timeout=5)
    assert runtime.state == DeviceState.CONNECTED


def test_close_success_transitions_to_disconnected(bus_manager):
    mock = MockDevice()
    runtime = DeviceRuntime(Device("D1", mock, widgets=[]), bus_manager)
    runtime.open().result(timeout=5)
    runtime.close().result(timeout=5)
    assert runtime.state == DeviceState.DISCONNECTED
    assert mock.is_open is False


def test_close_failure_transitions_to_error(bus_manager):
    mock = MockDevice(error_rate=1.0, seed=1)
    runtime = DeviceRuntime(Device("D1", mock, widgets=[]), bus_manager)
    runtime.open().result(timeout=5)
    with pytest.raises(RuntimeError):
        runtime.close().result(timeout=5)
    assert runtime.state == DeviceState.ERROR


def test_child_only_mock_device_detected_via_inherited_open(bus_manager):
    mock = ChildOnlyMockDevice()
    runtime = DeviceRuntime(Device("D1", mock, widgets=[]), bus_manager)
    assert runtime.state == DeviceState.DISCONNECTED
    runtime.open().result(timeout=5)
    assert runtime.state == DeviceState.CONNECTED
    assert mock.is_open is True


def test_auto_open_all_opens_eligible_devices_and_skips_others(bus_manager):
    auto_mock = MockDevice()
    no_auto_mock = MockDevice()
    no_open_mock = NoOpenMockDevice()
    layout = [
        Category(
            "A",
            [
                Device("Auto1", auto_mock, bus="b1", widgets=[], auto_open=True),
                Device("NoAuto1", no_auto_mock, bus="b2", widgets=[], auto_open=False),
                Device("NoOpen1", no_open_mock, bus="b3", widgets=[]),
                Device("Placeholder1", None, bus="b4", widgets=[]),
            ],
        )
    ]
    manager = DeviceManager(layout, bus_manager)
    futures = manager.auto_open_all()
    for _name, f in futures:
        f.result(timeout=5)

    assert auto_mock.is_open is True
    assert no_auto_mock.is_open is False
    assert manager.get("Auto1").state == DeviceState.CONNECTED
    assert manager.get("NoAuto1").state == DeviceState.DISCONNECTED
    assert manager.get("NoOpen1").state == DeviceState.CONNECTED
    assert manager.get("Placeholder1").state == DeviceState.NOT_INSTALLED


def test_auto_open_one_failure_does_not_block_others(bus_manager):
    good = MockDevice()
    bad = MockDevice(fail_open=True)
    layout = [
        Category(
            "A",
            [
                Device("Good", good, bus="b1", widgets=[]),
                Device("Bad", bad, bus="b2", widgets=[]),
            ],
        )
    ]
    manager = DeviceManager(layout, bus_manager)
    futures = manager.auto_open_all()
    results = []
    for _name, f in futures:
        try:
            f.result(timeout=5)
            results.append("ok")
        except RuntimeError:
            results.append("failed")

    assert manager.get("Good").state == DeviceState.CONNECTED
    assert manager.get("Bad").state == DeviceState.ERROR


def test_close_all_connected_closes_only_connected_and_survives_failure(bus_manager):
    ok_device = MockDevice()
    failing_device = MockDevice()
    layout = [
        Category(
            "A",
            [
                Device("Ok", ok_device, bus="b1", widgets=[]),
                Device("Failing", failing_device, bus="b2", widgets=[]),
            ],
        )
    ]
    manager = DeviceManager(layout, bus_manager)
    for _name, f in manager.auto_open_all():
        f.result(timeout=5)

    failing_device.error_rate = 1.0

    manager.close_all_connected()  # must not raise

    assert manager.get("Ok").state == DeviceState.DISCONNECTED
    assert manager.get("Failing").state == DeviceState.ERROR
