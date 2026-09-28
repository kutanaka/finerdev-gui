import asyncio

import pytest

from devgui.runtime.bus import BusManager
from devgui.runtime.devices import DeviceManager
from devgui.runtime.poller import Poller
from devgui.widgets import Category, Device, Display
from examples.mock_devices import MockDevice


@pytest.fixture
def bus_manager():
    m = BusManager()
    yield m
    m.stop_all(wait=False)


async def make_connected(device_manager, name):
    await asyncio.wrap_future(device_manager.get(name).open())


async def test_poll_records_successful_value(bus_manager):
    mock = MockDevice()
    mock.set(42.0)
    widget = Display("V", call=mock.get, poll=0.02)
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    await make_connected(dm, "D1")

    poller = Poller(layout, dm, bus_manager)
    poller.start()
    await asyncio.sleep(0.08)
    await poller.stop()

    log = poller.get_log(widget.id)
    assert len(log) > 0
    assert log[-1]["value"] == "42.0"
    assert log[-1]["error"] is False
    assert "t" in log[-1]


async def test_poll_applies_fmt(bus_manager):
    mock = MockDevice()
    mock.set(3.14159)
    widget = Display("V", call=mock.get, poll=0.02, fmt="{:.2f}")
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    await make_connected(dm, "D1")

    poller = Poller(layout, dm, bus_manager)
    poller.start()
    await asyncio.sleep(0.06)
    await poller.stop()

    log = poller.get_log(widget.id)
    assert log[-1]["value"] == "3.14"


async def test_poll_records_error_row_on_exception(bus_manager):
    mock = MockDevice(error_rate=1.0, seed=1)
    widget = Display("V", call=mock.get, poll=0.02)
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    await make_connected(dm, "D1")

    poller = Poller(layout, dm, bus_manager)
    poller.start()
    await asyncio.sleep(0.06)
    await poller.stop()

    log = poller.get_log(widget.id)
    assert len(log) > 0
    assert log[-1]["error"] is True
    assert "RuntimeError" in log[-1]["value"]


async def test_poll_skips_while_not_connected(bus_manager):
    mock = MockDevice()
    widget = Display("V", call=mock.get, poll=0.02)
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    # Deliberately not opened: state stays DISCONNECTED.

    poller = Poller(layout, dm, bus_manager)
    poller.start()
    await asyncio.sleep(0.08)
    await poller.stop()

    assert poller.get_log(widget.id) == []


async def test_ring_buffer_respects_max_rows(bus_manager):
    mock = MockDevice()
    widget = Display("V", call=mock.get, poll=0.01, visible_rows=3, max_rows=3)
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    await make_connected(dm, "D1")

    poller = Poller(layout, dm, bus_manager)
    poller.start()
    await asyncio.sleep(0.15)
    await poller.stop()

    assert len(poller.get_log(widget.id)) <= 3


async def test_slow_poll_is_skipped_not_queued(bus_manager):
    mock = MockDevice(delay=0.08)
    widget = Display("V", call=mock.get, poll=0.02)
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    await make_connected(dm, "D1")
    mock.calls.clear()  # drop the open() entry

    poller = Poller(layout, dm, bus_manager)
    poller.start()
    await asyncio.sleep(0.3)
    await poller.stop()

    # 0.3s / 0.02s poll would be ~15 ticks with no skip logic; a call that
    # takes 0.08s should yield far fewer completed calls than that.
    assert 0 < len(mock.calls) <= 6

    intervals = sorted(mock.calls, key=lambda c: c[1])
    for (_, _, prev_end), (_, next_start, _) in zip(intervals, intervals[1:]):
        assert next_start >= prev_end


async def test_on_entry_callback_receives_new_rows(bus_manager):
    mock = MockDevice()
    mock.set(7.0)
    widget = Display("V", call=mock.get, poll=0.02)
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    await make_connected(dm, "D1")

    received = []
    poller = Poller(layout, dm, bus_manager, on_entry=lambda wid, entry: received.append((wid, entry)))
    poller.start()
    await asyncio.sleep(0.06)
    await poller.stop()

    assert len(received) > 0
    assert received[-1][0] == widget.id
    assert received[-1][1]["value"] == "7.0"


async def test_stop_cancels_background_tasks(bus_manager):
    mock = MockDevice()
    widget = Display("V", call=mock.get, poll=0.02)
    layout = [Category("A", [Device("D1", mock, widgets=[widget])])]
    dm = DeviceManager(layout, bus_manager)
    await make_connected(dm, "D1")

    poller = Poller(layout, dm, bus_manager)
    poller.start()
    task = poller._tasks[0]
    await asyncio.sleep(0.03)
    await poller.stop()
    assert task.cancelled() or task.done()
    assert poller._tasks == []
