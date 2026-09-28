import logging
import threading
import time

import pytest

from devgui.runtime.bus import Bus, BusManager, Priority


@pytest.fixture
def manager():
    m = BusManager(slow_call_warning=30.0)
    yield m
    m.stop_all(wait=False)


def test_same_bus_calls_do_not_overlap(manager):
    intervals = []
    lock = threading.Lock()

    def make_task(tag):
        def task():
            start = time.monotonic()
            time.sleep(0.05)
            end = time.monotonic()
            with lock:
                intervals.append((tag, start, end))
            return tag

        return task

    futures = [manager.submit("bus-a", make_task(i)) for i in range(4)]
    results = [f.result(timeout=5) for f in futures]
    assert results == [0, 1, 2, 3]

    intervals.sort(key=lambda t: t[1])
    for (_, _, prev_end), (_, next_start, _) in zip(intervals, intervals[1:]):
        assert next_start >= prev_end


def test_different_buses_run_concurrently(manager):
    def make_task():
        def task():
            time.sleep(0.2)

        return task

    start = time.monotonic()
    f1 = manager.submit("bus-a", make_task())
    f2 = manager.submit("bus-b", make_task())
    f1.result(timeout=5)
    f2.result(timeout=5)
    elapsed = time.monotonic() - start
    assert elapsed < 0.35


def test_high_priority_jumps_ahead_of_queued_low_priority(manager):
    order = []
    order_lock = threading.Lock()
    gate = threading.Event()

    def blocker():
        gate.wait(timeout=5)

    def make_recorder(tag):
        def task():
            with order_lock:
                order.append(tag)

        return task

    # Occupy the worker so subsequent submissions queue up behind it.
    blocker_future = manager.submit("bus-a", blocker, priority=Priority.HIGH)

    low1 = manager.submit("bus-a", make_recorder("low1"), priority=Priority.LOW)
    low2 = manager.submit("bus-a", make_recorder("low2"), priority=Priority.LOW)
    high = manager.submit("bus-a", make_recorder("high"), priority=Priority.HIGH)

    gate.set()
    blocker_future.result(timeout=5)
    low1.result(timeout=5)
    low2.result(timeout=5)
    high.result(timeout=5)

    assert order.index("high") < order.index("low1")
    assert order.index("high") < order.index("low2")


def test_exception_in_task_is_set_on_future(manager):
    def failing():
        raise RuntimeError("boom")

    future = manager.submit("bus-a", failing)
    with pytest.raises(RuntimeError, match="boom"):
        future.result(timeout=5)


def test_slow_call_logs_warning(manager, caplog):
    bus = Bus("slow-bus", slow_call_warning=0.01)
    with caplog.at_level(logging.WARNING):
        future = bus.submit(lambda: time.sleep(0.05))
        future.result(timeout=5)
    bus.stop(wait=False)
    assert any("slow-bus" in record.message for record in caplog.records)


def test_stop_drains_pending_tasks_then_stops_thread():
    bus = Bus("bus-x")
    results = []
    for i in range(5):
        bus.submit(lambda i=i: results.append(i))
    bus.stop(wait=True)
    assert results == [0, 1, 2, 3, 4]
    assert not bus._thread.is_alive()
